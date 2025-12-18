"""
どこで: `sketch/perf_sketch.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイント/計測用の負荷生成として利用するため。
"""

import math
import os

from graft.api import E, G, L, run

CANVAS_WIDTH = 300
CANVAS_HEIGHT = 300


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return int(default)
    try:
        return int(value)
    except Exception:
        return int(default)


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return bool(default)
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _cpu_burn(t: float, iters: int) -> float:
    # draw の CPU 負荷を意図的に増やすための簡易ループ（計測用）。
    x = 0.0
    for i in range(int(iters)):
        a = float(i) * 0.001 + float(t)
        x += math.sin(a) * math.cos(a * 0.7)
    return x


_CASE = os.environ.get("GRAFT_SKETCH_CASE", "polyhedron").strip().lower()
_CPU_ITERS = _env_int("GRAFT_SKETCH_CPU_ITERS", 0)
_CIRCLE_SEGMENTS = _env_int("GRAFT_SKETCH_SEGMENTS", 50_000)
_MANY_LAYERS = _env_int("GRAFT_SKETCH_LAYERS", 200)
_PARAMETER_GUI = _env_flag("GRAFT_SKETCH_PARAMETER_GUI", True)
_N_WORKER = _env_int("GRAFT_SKETCH_N_WORKER", 0)


def draw(t: float):
    """
    計測用スケッチ。

    環境変数
    --------
    GRAFT_SKETCH_CASE : str
        `polyhedron`（既定）, `many_vertices`, `cpu_draw`, `many_layers`。
    GRAFT_SKETCH_SEGMENTS : int
        `many_vertices` の分割数。
    GRAFT_SKETCH_CPU_ITERS : int
        `cpu_draw` の負荷（0 で無効）。
    GRAFT_SKETCH_LAYERS : int
        `many_layers` のレイヤー数。
    GRAFT_SKETCH_PARAMETER_GUI : bool
        Parameter GUI を有効化する（既定 True）。
    GRAFT_SKETCH_N_WORKER : int
        `run(..., n_worker=...)` に渡す worker 数（既定 0）。
    """

    if _CASE == "many_vertices":
        # indices/転送を強くする: 巨大ポリライン（segments が大きいほど重い）。
        circle = G.circle(r=0.45, segments=_CIRCLE_SEGMENTS)
        eff = E.rotate(rotation=(0.0, 0.0, t * 10.0))
        return L(eff(circle))

    if _CASE == "cpu_draw":
        # draw 自体が支配的になる状況を作る（mp-draw の計測用）。
        burn = _cpu_burn(t, _CPU_ITERS) if _CPU_ITERS > 0 else 0.0
        r = 0.25 + 0.05 * (burn % 1.0)
        return L(G.circle(r=r, segments=256))

    if _CASE == "many_layers":
        # “レイヤー数”で負荷を作る。各レイヤーは軽いが、draw と realize の両方が増える。
        layers = []
        for i in range(max(1, int(_MANY_LAYERS))):
            phase = (t * 30.0 + float(i)) % 360.0
            g = G.polygon(n_sides=6, phase=phase)
            layers.append(L(g, thickness=0.001))
        return layers

    # 既定: 既存の最小例（realize/indices をほどよく含む）。
    ply1 = G.polyhedron()
    eff1 = (
        E(name="eff_ply1")
        .affine()
        .fill()
        .subdivide()
        .displace()
        .rotate(rotation=(t * 5, t * 5, t * 5))
    )
    return L(eff1(ply1))


if __name__ == "__main__":
    run(
        draw,
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
        render_scale=3.0,
        canvas_size=(CANVAS_WIDTH, CANVAS_HEIGHT),
        parameter_gui=_PARAMETER_GUI,
        n_worker=_N_WORKER,
    )
