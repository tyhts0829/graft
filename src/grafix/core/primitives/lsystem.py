"""
Purpose:
    L-systemの記号規則から、植物・回路風の分岐した開polylineを再現可能に生成する。
Use when:
    preset/custom規則、turtle記号、branch stack、またはseed付きjitterを変更する場合。
Constraints:
    - 同じprogram・seed・引数から同じjitter列を生成し、出力をopen stroke群として保つ。
    - 展開文字数のhard limitを越える前に拒否し、無制限な世代展開を許さない。
    - 壊れたcustom rule行やbracketは可能な範囲で解釈し、既存のwarning fallbackを維持する。
Side effects:
    不正なcustom規則またはstack不整合をPython warningとして通知する。
"""

from __future__ import annotations

import math
import warnings
from functools import lru_cache

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.operation_authoring import primitive
from grafix.core.geometry_kernels.packed import empty_packed_geometry
from grafix.core.realized_geometry import GeomTuple

_MAX_EXPANDED_CHARS = 500_000

_PRESETS: dict[str, tuple[str, dict[str, str]]] = {
    "plant": (
        "X",
        {
            "X": "F-[[X]+X]+F[+FX]-X",
            "F": "FF",
        },
    ),
    "circuit": (
        "X",
        {
            "X": "F[+X]F[-X]FX",
            "F": "FF",
        },
    ),
}

_DEFAULT_CUSTOM_AXIOM = "X"
_DEFAULT_CUSTOM_RULES = "X=F-[[X]+X]+F[+FX]-X\nF=FF"
_KIND_CHOICES = ("plant", "circuit", "custom")

lsystem_meta = {
    "kind": ParamMeta(
        kind="choice",
        choices=_KIND_CHOICES,
        description="使用する植物・回路プリセット、または独自の書き換え規則を選択します。",
    ),
    "iters": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=8,
        description="公理へ書き換え規則を繰り返し適用する世代数を指定します。",
    ),
    "center": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=300.0,
        description="タートルが描画を開始する XYZ 座標を指定します。",
    ),
    "heading": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=360.0,
        description="+X 軸を基準とするタートルの初期方向を度単位で指定します。",
    ),
    "angle": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=180.0,
        description="プログラム中の + と - がタートルを回転させる角度を指定します。",
    ),
    "step": ParamMeta(
        kind="float",
        ui_min=0.1,
        ui_max=50.0,
        description="プログラム中の F と f が一回で前進する距離を指定します。",
    ),
    "jitter": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=0.25,
        description="各前進距離と回転角へ加える再現可能な相対ゆらぎの幅を指定します。",
    ),
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=9999,
        description="ゆらぎの乱数列を決定し、同じ形を再現できるようにします。",
    ),
    "axiom": ParamMeta(
        kind="str",
        description="独自規則を展開するときの出発点となる初期文字列を指定します。",
    ),
    "rules": ParamMeta(
        kind="str",
        description="独自 L-system の一文字ごとの置換を A=... 形式で指定します。",
    ),
}

LSYSTEM_UI_VISIBLE = {
    "axiom": lambda v: v.get("kind", "plant") == "custom",
    "rules": lambda v: v.get("kind", "plant") == "custom",
}


def _parse_rules_text(rules: str) -> dict[str, str]:
    """`custom` 用の rules 文字列（`A=...`）を辞書に変換する。"""
    out: dict[str, str] = {}

    for line_no, raw in enumerate(rules.splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            warnings.warn(
                f"lsystem rules の {line_no} 行目を無視しました（'=' がありません）: {raw!r}",
                UserWarning,
                stacklevel=3,
            )
            continue
        lhs, rhs = s.split("=", 1)
        lhs = lhs.strip()
        if len(lhs) != 1:
            warnings.warn(
                f"lsystem rules の {line_no} 行目を無視しました（左辺は 1 文字が必要）: {raw!r}",
                UserWarning,
                stacklevel=3,
            )
            continue
        out[lhs] = rhs
    return out


def _expand_lsystem(axiom: str, rules: dict[str, str], *, iters: int) -> str:
    """L-system を展開して最終文字列を返す。"""
    s = axiom
    for _ in range(iters):
        parts: list[str] = []
        for ch in s:
            parts.append(rules.get(ch, ch))
        s = "".join(parts)
        if len(s) > _MAX_EXPANDED_CHARS:
            raise ValueError(
                "lsystem の展開結果が大きすぎます（iters/rules を下げてください）"
            )
    return s


@lru_cache(maxsize=16)
def _expand_preset(kind: str, iters: int) -> str:
    """組み込みpresetの展開結果を、変更不能な文字列として再利用する。"""

    axiom, rules = _PRESETS[kind]
    return _expand_lsystem(axiom, rules, iters=iters)


def _turtle_to_geom_tuple(
    program: str,
    *,
    start_xy: tuple[float, float],
    heading_deg: float,
    angle_deg: float,
    step: float,
    jitter: float,
    seed: int,
    z: float,
    batch_random: bool,
) -> GeomTuple:
    """タートル解釈し、開ポリライン列をpacked geometryとして返す。"""
    x, y = start_xy
    heading = math.radians(heading_deg)
    angle_base = math.radians(angle_deg)
    step_base = step
    jitter_f = jitter

    rng = np.random.default_rng(seed)
    random_values: np.ndarray | None = None
    random_at = 0
    if (
        batch_random
        and 0.0 < jitter_f <= 0.25
        and abs(heading) <= 20.0 * math.pi
        and abs(angle_base) <= 2.0 * math.pi
    ):
        random_count = sum(program.count(symbol) for symbol in "Ff+-")
        if random_count:
            random_values = rng.uniform(
                -jitter_f,
                jitter_f,
                size=random_count,
            )

    lines_xy: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = [(x, y)]
    stack: list[tuple[float, float, float, list[tuple[float, float]]]] = []
    direction_cache: dict[object, tuple[float, float]] = {}

    def finish_current() -> None:
        nonlocal current
        if len(current) >= 2:
            lines_xy.append(current)

    for ch in program:
        if ch == "F" or ch == "f":
            if jitter_f <= 0.0:
                dist = step_base
                direction_key: object = (
                    heading
                    if heading != 0.0
                    else (0.0, math.copysign(1.0, heading))
                )
                direction = direction_cache.get(direction_key)
                if direction is None:
                    direction = (math.cos(heading), math.sin(heading))
                    if len(direction_cache) < 256:
                        direction_cache[direction_key] = direction
                cos_heading, sin_heading = direction
            else:
                if random_values is None:
                    random_value = float(rng.uniform(-jitter_f, jitter_f))
                else:
                    random_value = float(random_values[random_at])
                    random_at += 1
                dist = step_base * (
                    1.0 + random_value
                )
                cos_heading = math.cos(heading)
                sin_heading = math.sin(heading)
            x += dist * cos_heading
            y += dist * sin_heading
            if ch == "F":
                current.append((x, y))
            else:
                finish_current()
                current = [(x, y)]
            continue

        if ch == "+" or ch == "-":
            if jitter_f <= 0.0:
                d = angle_base
            else:
                if random_values is None:
                    random_value = float(rng.uniform(-jitter_f, jitter_f))
                else:
                    random_value = float(random_values[random_at])
                    random_at += 1
                d = angle_base * (
                    1.0 + random_value
                )
            heading = heading + d if ch == "+" else heading - d
            continue

        if ch == "[":
            stack.append((x, y, heading, current))
            current = [(x, y)]
            continue

        if ch == "]":
            if not stack:
                warnings.warn(
                    "lsystem のプログラムに余分な ']' があるため無視します",
                    UserWarning,
                    stacklevel=3,
                )
                continue
            finish_current()
            x, y, heading, current = stack.pop()
            continue

        # 未知の記号（例: X）は no-op とする。

    if stack:
        warnings.warn(
            "lsystem のプログラムに閉じていない '[' があるため、残りを無視します",
            UserWarning,
            stacklevel=3,
        )

    finish_current()
    while stack:
        _x, _y, _heading, prev = stack.pop()
        if len(prev) >= 2:
            lines_xy.append(prev)

    zf = z
    if not lines_xy:
        return empty_packed_geometry()

    total_vertices = sum(len(poly) for poly in lines_xy)
    coords = np.empty((total_vertices, 3), dtype=np.float32)
    offsets = np.empty((len(lines_xy) + 1,), dtype=np.int32)
    offsets[0] = 0
    cursor = 0
    for index, poly in enumerate(lines_xy, start=1):
        xy = np.asarray(poly, dtype=np.float32)
        next_cursor = cursor + int(xy.shape[0])
        coords[cursor:next_cursor, :2] = xy
        coords[cursor:next_cursor, 2] = zf
        cursor = next_cursor
        offsets[index] = cursor
    return coords, offsets


@primitive(meta=lsystem_meta, ui_visible=LSYSTEM_UI_VISIBLE)
def lsystem(
    *,
    kind: str = "plant",
    iters: int = 5,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    heading: float = 90.0,
    angle: float = 25.0,
    step: float = 6.0,
    jitter: float = 0.0,
    seed: int = 0,
    axiom: str = _DEFAULT_CUSTOM_AXIOM,
    rules: str = _DEFAULT_CUSTOM_RULES,
) -> GeomTuple:
    """L-system を展開し、枝分かれした線（開ポリライン列）を生成する。

    L-system は「文字列の置換規則」で形を作る手法で、
    `axiom`（初期文字列）から始めて `rules`（置換規則）を `iters` 回だけ適用し、
    得られた最終文字列をタートル（Turtle）として解釈して線を描く。

    - 展開: 文字ごとに `rules` を適用して文字列を更新する
    - 描画: `F f + - [ ]` だけが意味を持つ（それ以外の文字は no-op）

    `X` のような「描画しない記号」を `rules` の中間シンボルとして使うのが典型。
    例えば、fractal plant では `X` を展開のために使い、描画は `F` だけで行う。

    入力が多少壊れていても試行錯誤しやすいように、次は例外にせず warning にする。

    - `rules` の不正行: warning を出して無視する
    - `[`/`]` の不整合: warning を出して可能な範囲で解釈する

    記号（最小セット）
    ----------------
    - `F`: 前進 + 描画
    - `f`: 前進（描画しない）
    - `+`: 左回転（+angle）
    - `-`: 右回転（-angle）
    - `[`: push（位置・向き）
    - `]`: pop（復帰）

    Parameters
    ----------
    kind : {"plant","circuit","custom"}, default "plant"
        プリセット種別。
        `"custom"` の場合は `axiom` と `rules` を使用する。
    iters : int, default 5
        0 以上の展開回数（0 で axiom をそのまま解釈する）。
    center : tuple[float, float, float], default (0,0,0)
        開始点の座標 (cx, cy, cz)。
    heading : float, default 90.0
        初期向き [deg]。0° で +X 方向、90° で +Y 方向。
    angle : float, default 25.0
        回転角 [deg]（`+/-`）。
    step : float, default 6.0
        0 より大きい前進距離（`F/f`）。
    jitter : float, default 0.0
        角度/距離の相対ゆらぎ（0 以上）。
        `jitter>0` のとき、各 `F/f` と `+/-` ごとに `U(-jitter, +jitter)` を掛ける。
    seed : int, default 0
        0 以上の乱数 seed（決定性）。
    axiom : str, default "X"
        初期文字列（展開の出発点）。`kind="custom"` のときのみ使用する。

        例: `axiom="F"`, `rules="F=FF"`, `iters=3` のとき、展開結果は `FFFFFFFF` になり、
        それをタートルとして解釈して線を描く。

        もう少し複雑な例（分岐と「変数」）:

        - 分岐は `[` と `]` で作る（`[` で位置/向きを保存し、`]` でそこへ戻る）
        - `X` のような文字は「描画しない変数」として扱い、展開のために使う（タートル解釈では no-op）

        例えば次のように書くと、`X` が毎回同じ形に展開されて「枝の先端が伸びる」ような挙動になる。

        - `axiom="X"`
        - `rules="X=F[+X]F[-X]FX\\nF=FF"`
        - `iters=5`

        ※最終文字列に `X` が残っていても、その `X` 自体は描画されない（次の世代の“芽”として残る）。
    rules : str, default "X=...\\nF=..."
        置換規則（行ごとに `A=...` 形式）。`kind="custom"` のときのみ使用する。

        - 左辺 `A` は 1 文字（シンボル）
        - 右辺は置換後の文字列（空でもよい。例: `X=` で X を消す）
        - 空行と `#` コメント行は無視する
        - 不正行は warning を出して無視する

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        生成された枝ポリライン列（coords, offsets）。

    Raises
    ------
    ValueError
        `iters`、`jitter`、`seed` のいずれかが負、または `step` が 0 以下の場合。
    """
    if iters < 0:
        raise ValueError("lsystem の iters は 0 以上である必要がある")
    if step <= 0.0:
        raise ValueError("lsystem の step は 0 より大きい必要がある")
    if jitter < 0.0:
        raise ValueError("lsystem の jitter は 0 以上である必要がある")
    if seed < 0:
        raise ValueError("lsystem の seed は 0 以上である必要がある")

    kind_s = kind
    iters_i = iters
    seed_i = seed
    ax = axiom
    rules_text = rules
    cx, cy, cz = center

    if kind_s == "custom":
        rules_map = _parse_rules_text(rules_text)
        program = _expand_lsystem(ax, rules_map, iters=iters_i)
        batch_random = False
    else:
        program = _expand_preset(kind_s, iters_i)
        batch_random = True
    if not program:
        return empty_packed_geometry()

    return _turtle_to_geom_tuple(
        program,
        start_xy=(cx, cy),
        heading_deg=heading,
        angle_deg=angle,
        step=step,
        jitter=jitter,
        seed=seed_i,
        z=cz,
        batch_random=batch_random,
    )
