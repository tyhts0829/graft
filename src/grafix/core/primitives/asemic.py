"""
Purpose:
    外部fontを使わず、再現可能な擬似glyphを通常textと同じlayoutで文章化する。
Use when:
    asemic字形生成、seed identity、stroke style、またはtext互換layoutを変更する場合。
Constraints:
    - glyph seedを安定hashから導き、Pythonのprocess依存hashやambient乱数へ依存しない。
    - 同じ文字と生成引数には同じ字形を使い、cached glyph arrayを変更可能な出力として漏らさない。
    - 1em layoutを共有helperで確定してからscaleとcenterを適用する。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from functools import lru_cache

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.operation_authoring import primitive
from grafix.core.primitives._text_layout import (
    aligned_line_origin_em,
    bounding_box_polylines_em,
    measure_line_width_em,
    wrap_line_by_width_em,
)
from grafix.core.geometry_kernels.packed import empty_packed_geometry
from grafix.core.realized_geometry import GeomTuple

_NUMPY_RNG_MAX_NODES = 32
_MAX_CACHED_BEZIER_SAMPLES = 64
_NUMBA_RNG_KERNEL: Callable[[np.ndarray], np.ndarray] | None = None
_STROKE_STYLE_CHOICES = ("line", "bezier")
_TEXT_ALIGN_CHOICES = ("left", "center", "right")

asemic_meta = {
    "text": ParamMeta(
        kind="str",
        description="擬似字形で描画する文字列を指定し、改行で複数行に分けます。",
    ),
    "seed": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=999999,
        description="文字ごとの字形を決定し、同じ文字を同じ形で再現できるようにします。",
    ),
    # --- glyph params（全文共通）---
    "n_nodes": ParamMeta(
        kind="int",
        ui_min=3,
        ui_max=200,
        description="各字形の骨格グラフに配置するノード数を指定します。",
    ),
    "candidates": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=50,
        description="ノード配置時に比較する候補点を増やし、分布の均一さを調整します。",
    ),
    "stroke_min": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=20,
        description="一つの字形を構成するストローク本数の下限を指定します。",
    ),
    "stroke_max": ParamMeta(
        kind="int",
        ui_min=0,
        ui_max=20,
        description="一つの字形を構成するストローク本数の上限を指定します。",
    ),
    "walk_min_steps": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=20,
        description="骨格グラフ上で一つのストロークが進む最小ステップ数を指定します。",
    ),
    "walk_max_steps": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=20,
        description="骨格グラフ上で一つのストロークが進む最大ステップ数を指定します。",
    ),
    "stroke_style": ParamMeta(
        kind="choice",
        choices=_STROKE_STYLE_CHOICES,
        description="骨格を折れ線のまま描くか、Bézier 曲線で滑らかに描くか選択します。",
    ),
    "bezier_samples": ParamMeta(
        kind="int",
        ui_min=2,
        ui_max=64,
        description="Bézier 化した各セグメントを構成するサンプリング点数を指定します。",
    ),
    "bezier_tension": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=1.0,
        description="Bézier ストロークの張りを調整し、大きいほど直線に近づけます。",
    ),
    # --- layout params ---
    "text_align": ParamMeta(
        kind="choice",
        choices=_TEXT_ALIGN_CHOICES,
        description="各行の擬似字形を左揃え・中央揃え・右揃えのいずれで配置するか選択します。",
    ),
    "glyph_advance_em": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=3.0,
        description="空白以外の文字を一文字進める距離を em 単位で指定します。",
    ),
    "space_advance_em": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=3.0,
        description="空白文字で進める距離を em 単位で指定します。",
    ),
    "letter_spacing_em": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=2.0,
        description="各文字送りへ追加する間隔を em 単位で指定します。",
    ),
    "line_height": ParamMeta(
        kind="float",
        ui_min=0.8,
        ui_max=3.0,
        description="複数行のベースライン間隔を em 単位で指定します。",
    ),
    "use_bounding_box": ParamMeta(
        kind="bool",
        description="指定幅での自動改行と任意のボックス枠描画を有効にします。",
    ),
    "box_width": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=300.0,
        description="自動改行と枠描画に使うボックス幅を出力座標単位で指定します。",
    ),
    "box_height": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=300.0,
        description="枠描画に使うボックス高さを出力座標単位で指定します。",
    ),
    "show_bounding_box": ParamMeta(
        kind="bool",
        description="指定した幅と高さのボックス枠を擬似字形へ追加します。",
    ),
    # --- placement ---
    "center": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=300.0,
        description="生成した擬似文字列全体を平行移動する XYZ 座標を指定します。",
    ),
    "scale": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=200.0,
        description="1 em を基準に生成した擬似字形へ適用する等方スケールを指定します。",
    ),
}

ASEMIC_UI_VISIBLE = {
    "bezier_samples": lambda v: v.get("stroke_style", "bezier") == "bezier",
    "bezier_tension": lambda v: v.get("stroke_style", "bezier") == "bezier",
    "box_width": lambda v: v.get("use_bounding_box") is True,
    "box_height": lambda v: v.get("use_bounding_box") is True,
    "show_bounding_box": lambda v: v.get("use_bounding_box") is True,
}


def _stable_hash64(text: str) -> int:
    """Python の `hash()` に依存しない安定ハッシュ（64-bit）を返す。"""
    h = hashlib.blake2b(text.encode("utf-8"), digest_size=8)
    return int.from_bytes(h.digest(), byteorder="big", signed=False)


def _best_candidate_points(
    rng: np.random.Generator,
    *,
    n: int,
    candidates: int,
) -> np.ndarray:
    """Mitchell 風 best-candidate で点をそこそこ均一にばら撒く。"""
    if n <= 0:
        return np.zeros((0, 2), dtype=np.float64)

    pts = np.empty((n, 2), dtype=np.float64)
    pts[0] = rng.uniform(-0.5, 0.5, size=(2,))

    for i in range(1, n):
        cand = rng.uniform(-0.5, 0.5, size=(candidates, 2))
        diff = cand[:, None, :] - pts[None, :i, :]
        dist2 = (diff * diff).sum(axis=2)
        min_dist2 = dist2.min(axis=1)
        best = int(np.argmax(min_dist2))
        pts[i] = cand[best]

    return pts


def _build_rng_adjacency(
    points: np.ndarray,
    *,
    use_numpy: bool = True,
) -> list[set[int]]:
    """Relative Neighborhood Graph (RNG) を構築し、隣接集合を返す。"""
    n = int(points.shape[0])
    if n <= 0:
        return []

    points64 = np.asarray(points, dtype=np.float64)
    if use_numpy and n <= _NUMPY_RNG_MAX_NODES:
        matrix = _build_rng_adjacency_matrix_numpy(points64)
    else:
        matrix = _build_rng_adjacency_matrix(points64)
    return [set(np.flatnonzero(matrix[i]).tolist()) for i in range(n)]


def _build_rng_adjacency_matrix_numpy(points: np.ndarray) -> np.ndarray:
    """小規模glyph用にRNG adjacencyを一括計算する。"""

    dx = points[:, None, 0] - points[None, :, 0]
    dy = points[:, None, 1] - points[None, :, 1]
    distance_sq = dx * dx + dy * dy
    edge_distance = distance_sq[:, :, None]
    blocked = np.any(
        (distance_sq[:, None, :] < edge_distance)
        & (distance_sq[None, :, :] < edge_distance),
        axis=2,
    )
    adjacency = (
        np.isfinite(distance_sq)
        & (distance_sq > 0.0)
        & ~blocked
    )
    np.fill_diagonal(adjacency, False)
    return adjacency


def _build_rng_adjacency_matrix_impl(points: np.ndarray) -> np.ndarray:
    """Numba compile対象となる大規模glyph用RNG loop。"""

    n = points.shape[0]
    distance_sq = np.empty((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            dx = points[i, 0] - points[j, 0]
            dy = points[i, 1] - points[j, 1]
            distance_sq[i, j] = dx * dx + dy * dy

    adjacency = np.zeros((n, n), dtype=np.bool_)
    for i in range(n):
        for j in range(i + 1, n):
            dij = distance_sq[i, j]
            if not np.isfinite(dij) or dij <= 0.0:
                continue
            blocked = False
            for k in range(n):
                if k == i or k == j:
                    continue
                if distance_sq[i, k] < dij and distance_sq[j, k] < dij:
                    blocked = True
                    break
            if not blocked:
                adjacency[i, j] = True
                adjacency[j, i] = True
    return adjacency


def _build_rng_adjacency_matrix(points: np.ndarray) -> np.ndarray:
    """大規模glyphでだけNumba kernelを遅延作成して実行する。"""

    global _NUMBA_RNG_KERNEL
    kernel = _NUMBA_RNG_KERNEL
    if kernel is None:
        from numba import njit  # type: ignore[attr-defined, import-untyped]

        kernel = njit(cache=True)(_build_rng_adjacency_matrix_impl)
        _NUMBA_RNG_KERNEL = kernel
    return kernel(points)


def _random_walk_strokes(
    rng: np.random.Generator,
    *,
    adjacency: list[set[int]],
    stroke_min: int,
    stroke_max: int,
    walk_min_steps: int,
    walk_max_steps: int,
) -> list[list[int]]:
    n = len(adjacency)
    if n <= 0:
        return []

    n_strokes = (
        int(rng.integers(stroke_min, stroke_max + 1))
        if stroke_max > 0
        else stroke_min
    )
    if n_strokes <= 0:
        return []

    strokes: list[list[int]] = []

    for _ in range(n_strokes):
        starts = [i for i, nb in enumerate(adjacency) if nb]
        if not starts:
            break
        current = int(rng.choice(starts))
        steps = int(rng.integers(walk_min_steps, walk_max_steps + 1))

        path: list[int] = [current]
        for _step in range(steps):
            neighbors = list(adjacency[current])
            if not neighbors:
                break
            nxt = int(rng.choice(neighbors))
            adjacency[current].remove(nxt)
            adjacency[nxt].remove(current)
            current = nxt
            path.append(current)

        if len(path) >= 2:
            strokes.append(path)

    return strokes


def _polylines_to_realized(
    polylines: list[np.ndarray],
    *,
    center: tuple[float, float, float],
    scale: float,
) -> GeomTuple:
    filtered = [
        p.astype(np.float32, copy=False) for p in polylines if int(p.shape[0]) >= 2
    ]
    if not filtered:
        return empty_packed_geometry()

    coords = np.concatenate(filtered, axis=0).astype(np.float32, copy=False)

    offsets = np.zeros(len(filtered) + 1, dtype=np.int32)
    acc = 0
    for i, line in enumerate(filtered):
        acc += int(line.shape[0])
        offsets[i + 1] = acc

    cx, cy, cz = center
    s_f = scale
    if (cx, cy, cz) != (0.0, 0.0, 0.0) or s_f != 1.0:
        center_vec = np.asarray(center, dtype=np.float32)
        coords = coords * np.float32(s_f) + center_vec

    return coords, offsets


def _make_bezier_basis(
    samples_per_segment: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """cubic Bézier係数を生成する。"""

    ts = np.linspace(0.0, 1.0, num=samples_per_segment, dtype=np.float64)
    u = 1.0 - ts
    basis = (
        u**3,
        3.0 * (u**2) * ts,
        3.0 * u * (ts**2),
        ts**3,
    )
    for values in basis:
        values.setflags(write=False)
    return basis


@lru_cache(maxsize=64)
def _bezier_basis(
    samples_per_segment: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """通常範囲の同一sample数で共有するcubic Bézier係数を返す。"""

    return _make_bezier_basis(samples_per_segment)


def _sample_bezier(points: np.ndarray, *, samples_per_segment: int, tension: float) -> np.ndarray:
    """折れ線を Catmull-Rom 風の合成 Bézier としてサンプル点列化する。"""
    n = int(points.shape[0])
    if n < 2:
        return points

    samples = samples_per_segment
    t = tension

    m = np.zeros((n, 2), dtype=np.float64)
    if n == 2:
        m[0] = points[1] - points[0]
        m[1] = points[1] - points[0]
    else:
        m[0] = points[1] - points[0]
        m[-1] = points[-1] - points[-2]
        m[1:-1] = 0.5 * (points[2:] - points[:-2])

    # tension=1 で直線化、tension=0 で通常の Catmull-Rom。
    m *= 1.0 - t

    if samples <= _MAX_CACHED_BEZIER_SAMPLES:
        b0, b1, b2, b3 = _bezier_basis(samples)
    else:
        b0, b1, b2, b3 = _make_bezier_basis(samples)
    segs: list[np.ndarray] = []
    for i in range(n - 1):
        p0 = points[i]
        p1 = points[i + 1]
        c1 = p0 + m[i] / 3.0
        c2 = p1 - m[i + 1] / 3.0

        sample_slice = slice(1, None) if i > 0 else slice(None)
        curve = (
            b0[sample_slice, None] * p0
            + b1[sample_slice, None] * c1
            + b2[sample_slice, None] * c2
            + b3[sample_slice, None] * p1
        )
        segs.append(curve)

    return np.concatenate(segs, axis=0) if segs else points


def _char_advance_em(
    char: str,
    *,
    glyph_advance_em: float,
    space_advance_em: float,
) -> float:
    if char == " ":
        return space_advance_em
    return glyph_advance_em


@lru_cache(maxsize=256)
def _generate_asemic_glyph(
    *,
    seed: int,
    n_nodes: int,
    candidates: int,
    stroke_min: int,
    stroke_max: int,
    walk_min_steps: int,
    walk_max_steps: int,
    stroke_style: str,
    bezier_samples: int,
    bezier_tension: float,
) -> tuple[np.ndarray, ...]:
    """1 文字分のストローク（ポリライン列）を生成して返す（1em=1.0, 左上起点）。"""
    nodes = n_nodes
    if nodes < 2:
        return ()

    rng = np.random.default_rng(seed)

    pts = _best_candidate_points(rng, n=nodes, candidates=candidates)
    adjacency = _build_rng_adjacency(
        pts,
        use_numpy=stroke_style != "line",
    )
    strokes = _random_walk_strokes(
        rng,
        adjacency=adjacency,
        stroke_min=stroke_min,
        stroke_max=stroke_max,
        walk_min_steps=walk_min_steps,
        walk_max_steps=walk_max_steps,
    )
    if not strokes:
        return ()

    style = stroke_style

    polylines: list[np.ndarray] = []
    for path in strokes:
        poly = pts[np.asarray(path, dtype=np.int64)]
        if style == "bezier":
            poly = _sample_bezier(
                poly,
                samples_per_segment=bezier_samples,
                tension=bezier_tension,
            )

        # glyph 座標系は左上起点にしたいので [-0.5,0.5]^2 → [0,1]^2 へシフト。
        poly = poly + np.float64(0.5)
        poly32 = poly.astype(np.float32, copy=False)
        if int(poly32.shape[0]) < 2:
            continue
        arr3 = np.zeros((poly32.shape[0], 3), dtype=np.float32)
        arr3[:, :2] = poly32
        polylines.append(arr3)

    for polyline in polylines:
        polyline.setflags(write=False)
    return tuple(polylines)


@lru_cache(maxsize=1)
def _dot_polylines() -> tuple[np.ndarray, ...]:
    """ピリオド `.` 用のドット（小円）を返す（1em=1.0, 左上起点）。"""
    cx = 0.5
    cy = 0.85
    r = 0.06
    segments = 12

    angles = np.linspace(0.0, 2.0 * np.pi, num=segments, endpoint=False, dtype=np.float64)
    x = cx + r * np.cos(angles)
    y = cy + r * np.sin(angles)
    xy = np.stack([x, y], axis=1).astype(np.float32, copy=False)
    xy = np.concatenate([xy, xy[:1]], axis=0)

    arr3 = np.zeros((xy.shape[0], 3), dtype=np.float32)
    arr3[:, :2] = xy
    arr3.setflags(write=False)
    return (arr3,)


@primitive(meta=asemic_meta, ui_visible=ASEMIC_UI_VISIBLE)
def asemic(
    *,
    text: str = "A",
    seed: int = 0,
    n_nodes: int = 28,
    candidates: int = 12,
    stroke_min: int = 2,
    stroke_max: int = 5,
    walk_min_steps: int = 2,
    walk_max_steps: int = 4,
    stroke_style: str = "bezier",
    bezier_samples: int = 12,
    bezier_tension: float = 0.5,
    text_align: str = "left",
    glyph_advance_em: float = 1.0,
    space_advance_em: float = 0.35,
    letter_spacing_em: float = 0.0,
    line_height: float = 1.2,
    use_bounding_box: bool = False,
    box_width: float = -1.0,
    box_height: float = -1.0,
    show_bounding_box: bool = False,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> GeomTuple:
    """擬似文字（asemic）の文章をポリライン列として生成する。

    同じ文字は同じ字形になる（seed を文字で派生させる）ため、フォントのように使える。
    レイアウトは `text.py` と同様に「1em=1.0 の座標系 → 最後に scale/center 適用」。

    Parameters
    ----------
    text : str, default "A"
        描画する文字列。`\\n` 区切りで複数行を表す。
    seed : int, default 0
        0 以上の全体 seed。同じ文字は `seed` と文字から派生した seed で生成され、決定的に同じ字形になる。
    n_nodes : int, default 28
        0 以上のノード数。0 または 1 では通常文字のストロークを生成しない。
    candidates : int, default 12
        1 以上の best-candidate 候補数（大きいほど均一になりやすい）。
    stroke_min : int, default 2
        0 以上かつ `stroke_max` 以下のストローク本数最小値。
    stroke_max : int, default 5
        `stroke_min` 以上のストローク本数最大値。
    walk_min_steps : int, default 2
        1 以上かつ `walk_max_steps` 以下のランダムウォーク最小ステップ数。
    walk_max_steps : int, default 4
        `walk_min_steps` 以上のランダムウォーク最大ステップ数。
    stroke_style : {"line", "bezier"}, default "bezier"
        ストロークの描画スタイル。
        `"bezier"` は折れ線を合成 Bézier としてサンプル点列化して滑らかにする。
    bezier_samples : int, default 12
        `"bezier"` 時の 1 セグメントあたりのサンプル点数（2 以上）。
    bezier_tension : float, default 0.5
        `"bezier"` 時の張り（0=曲がりやすい, 1=直線寄り）。
    text_align : {"left","center","right"}, default "left"
        行揃え。
    glyph_advance_em : float, default 1.0
        空白以外の文字送り（em）。
    space_advance_em : float, default 0.35
        空白の文字送り（em）。
    letter_spacing_em : float, default 0.0
        追加の文字間スペーシング（em）。
    line_height : float, default 1.2
        行送り（em）。
    use_bounding_box : bool, default False
        True のとき `box_width` による自動改行と、`show_bounding_box` による枠描画を有効にする。
    box_width : float, default -1.0
        幅による自動改行を行う際のボックス幅（出力座標系）。0 以下なら無効。
    box_height : float, default -1.0
        デバッグ用ボックス表示の高さ（出力座標系）。0 以下なら無効。
    show_bounding_box : bool, default False
        True のとき、`box_width/box_height` で指定されたボックス枠（4本の線分）を追加で描画する。
    center : tuple[float, float, float], default (0,0,0)
        平行移動ベクトル (cx, cy, cz)。
    scale : float, default 1.0
        等方スケール倍率。1em の出力スケール（例: 1em=40mm → scale=40）。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        文章のポリライン列（coords, offsets）。

    Raises
    ------
    ValueError
        seed、ノード数、候補数、ストローク数、ウォーク長、Bézier の
        サンプル数または張りが定義域外の場合。
    """
    if seed < 0:
        raise ValueError("asemic の seed は 0 以上である必要がある")
    if n_nodes < 0:
        raise ValueError("asemic の n_nodes は 0 以上である必要がある")
    if candidates < 1:
        raise ValueError("asemic の candidates は 1 以上である必要がある")
    if stroke_min < 0 or stroke_max < 0:
        raise ValueError("asemic の stroke_min/stroke_max は 0 以上である必要がある")
    if stroke_min > stroke_max:
        raise ValueError("asemic の stroke_min は stroke_max 以下である必要がある")
    if walk_min_steps < 1 or walk_max_steps < 1:
        raise ValueError(
            "asemic の walk_min_steps/walk_max_steps は 1 以上である必要がある"
        )
    if walk_min_steps > walk_max_steps:
        raise ValueError(
            "asemic の walk_min_steps は walk_max_steps 以下である必要がある"
        )
    if bezier_samples < 2:
        raise ValueError("asemic の bezier_samples は 2 以上である必要がある")
    if not 0.0 <= bezier_tension <= 1.0:
        raise ValueError(
            "asemic の bezier_tension は 0 以上 1 以下である必要がある"
        )

    text_s = text
    seed_i = seed
    n_nodes_i = n_nodes
    candidates_i = candidates
    stroke_min_i = stroke_min
    stroke_max_i = stroke_max
    walk_min_steps_i = walk_min_steps
    walk_max_steps_i = walk_max_steps
    stroke_style_s = stroke_style
    bezier_samples_i = bezier_samples
    text_align_s = text_align
    use_bb = use_bounding_box
    show_bounding_box_b = show_bounding_box
    cx, cy, cz = center
    s_f = scale

    base_seed = seed_i
    glyph_adv = glyph_advance_em
    space_adv = space_advance_em

    def char_advance_em(char: str) -> float:
        return _char_advance_em(
            char,
            glyph_advance_em=glyph_adv,
            space_advance_em=space_adv,
        )

    lines = text_s.split("\n")
    s_abs = abs(s_f)
    bw = box_width
    bh = box_height

    if use_bb and bw > 0.0 and s_abs > 0.0:
        bw_em = bw / s_abs
        wrapped: list[str] = []
        for line_str in lines:
            wrapped.extend(
                wrap_line_by_width_em(
                    line_str,
                    max_width_em=bw_em,
                    char_advance_em=char_advance_em,
                    letter_spacing_em=letter_spacing_em,
                )
            )
        lines = wrapped

    polylines: list[np.ndarray] = []
    glyph_cache: dict[str, tuple[np.ndarray, ...]] = {}

    y_em = 0.0
    for li, line_str in enumerate(lines):
        width_em = measure_line_width_em(
            line_str,
            char_advance_em=char_advance_em,
            letter_spacing_em=letter_spacing_em,
        )
        x_em = aligned_line_origin_em(width_em, text_align_s)

        cur_x_em = float(x_em)
        for ch in line_str:
            if ch != " ":
                cached = glyph_cache.get(ch)
                if cached is None:
                    if ch == ".":
                        cached = _dot_polylines()
                    else:
                        seed_char = _stable_hash64(f"{base_seed}|{ch}")
                        cached = _generate_asemic_glyph(
                            seed=seed_char,
                            n_nodes=n_nodes_i,
                            candidates=candidates_i,
                            stroke_min=stroke_min_i,
                            stroke_max=stroke_max_i,
                            walk_min_steps=walk_min_steps_i,
                            walk_max_steps=walk_max_steps_i,
                            stroke_style=stroke_style_s,
                            bezier_samples=bezier_samples_i,
                            bezier_tension=bezier_tension,
                        )
                    glyph_cache[ch] = cached

                if cached:
                    if cur_x_em == 0.0 and y_em == 0.0:
                        polylines.extend(cached)
                    else:
                        shift = np.array([cur_x_em, y_em, 0.0], dtype=np.float32)
                        for p in cached:
                            polylines.append(p + shift)

            cur_x_em += char_advance_em(ch) + letter_spacing_em

        if li < len(lines) - 1:
            y_em += line_height

    if (
        use_bb
        and show_bounding_box_b
        and bw > 0.0
        and bh > 0.0
        and s_abs > 0.0
    ):
        bw_em = bw / s_abs
        bh_em = bh / s_abs

        polylines.extend(
            bounding_box_polylines_em(
                width_em=bw_em,
                height_em=bh_em,
                align=text_align_s,
            )
        )

    return _polylines_to_realized(
        polylines,
        center=(cx, cy, cz),
        scale=float(s_f),
    )


__all__ = ["asemic", "asemic_meta"]
