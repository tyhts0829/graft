"""
どこで: `sketch/perf_sketch.py`。
何を: API を用いた簡単なスケッチを定義し、run でプレビュー表示する。
なぜ: 動作確認用の最小エントリポイント/計測用の負荷生成として利用するため。
"""

import math
import os

from grafix.api import E, G, L, run
from grafix.core.layer import Layer

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


_CASE = os.environ.get("GRAFIX_SKETCH_CASE", "polyhedron").strip().lower()
_CPU_ITERS = _env_int("GRAFIX_SKETCH_CPU_ITERS", 0)
_CIRCLE_SEGMENTS = _env_int("GRAFIX_SKETCH_SEGMENTS", 50_000)
_MANY_LAYERS = _env_int("GRAFIX_SKETCH_LAYERS", 200)
_STATIC_UNIQUE = _env_int("GRAFIX_SKETCH_STATIC_UNIQUE", 64)
_UPLOAD_SEGMENTS = _env_int("GRAFIX_SKETCH_UPLOAD_SEGMENTS", 500_000)
_UPLOAD_LAYERS = _env_int("GRAFIX_SKETCH_UPLOAD_LAYERS", 2)
_PARAMETER_GUI = _env_flag("GRAFIX_SKETCH_PARAMETER_GUI", True)
_N_WORKER = _env_int("GRAFIX_SKETCH_N_WORKER", 0)

_STATIC_LAYERS_CACHE: list[Layer] | None = None
_UPLOAD_SKIP_LAYERS_CACHE: list[Layer] | None = None


def _static_layers() -> list[Layer]:
    global _STATIC_LAYERS_CACHE
    if _STATIC_LAYERS_CACHE is not None:
        return _STATIC_LAYERS_CACHE

    total_layers = max(1, int(_MANY_LAYERS))
    unique = max(1, min(int(_STATIC_UNIQUE), total_layers))

    # 静的ジオメトリを少数用意し、レイヤーで使い回す。
    # - unique を小さくすると geometry_id が繰り返され、GPU メッシュキャッシュが効きやすい。
    # - unique を layers と同数にすると「静的だがユニーク」になり、キャッシュ上限/スラッシングの確認に使える。
    geometries = [
        G.polygon(n_sides=6, phase=(360.0 * float(i) / float(unique)))
        for i in range(unique)
    ]

    # Parameter GUI を有効にしても行数が爆発しないよう、site_id は 1 つにまとめる。
    site_id = "perf_sketch:static_layers"

    layers: list[Layer] = []
    for i in range(total_layers):
        g = geometries[i % unique]
        layers.append(Layer(geometry=g, site_id=site_id, thickness=0.001))

    _STATIC_LAYERS_CACHE = layers
    return layers


def _upload_skip_layers() -> list[Layer]:
    global _UPLOAD_SKIP_LAYERS_CACHE
    if _UPLOAD_SKIP_LAYERS_CACHE is not None:
        return _UPLOAD_SKIP_LAYERS_CACHE

    # GPU upload が支配的になるよう、大きい 1 本ポリラインを静的に用意する。
    # - geometry_id が安定 → upload skip が効けば、2 フレーム目以降は upload が消える。
    # - Layer は複数回同一 geometry を描く（既定 2）。これで 1 フレーム目からキャッシュが昇格する。
    g = G.circle(r=0.45, segments=max(3, int(_UPLOAD_SEGMENTS)))
    site_id = "perf_sketch:upload_skip"

    layers: list[Layer] = []
    for _ in range(max(1, int(_UPLOAD_LAYERS))):
        layers.append(Layer(geometry=g, site_id=site_id, thickness=0.001))

    _UPLOAD_SKIP_LAYERS_CACHE = layers
    return layers


def draw(t: float):
    """
    計測用スケッチ。

    環境変数
    --------
    GRAFIX_SKETCH_CASE : str
        `polyhedron`（既定）, `many_vertices`, `cpu_draw`, `many_layers`, `static_layers`, `upload_skip`。
    GRAFIX_SKETCH_SEGMENTS : int
        `many_vertices` の分割数。
    GRAFIX_SKETCH_CPU_ITERS : int
        `cpu_draw` の負荷（0 で無効）。
    GRAFIX_SKETCH_LAYERS : int
        `many_layers` のレイヤー数。
    GRAFIX_SKETCH_STATIC_UNIQUE : int
        `static_layers` のユニークジオメトリ数（既定 64）。
        - 小さいほど geometry_id が繰り返され、GPU upload skip が効きやすい。
    GRAFIX_SKETCH_UPLOAD_SEGMENTS : int
        `upload_skip` の頂点数（既定 500_000）。
    GRAFIX_SKETCH_UPLOAD_LAYERS : int
        `upload_skip` の同一ジオメトリ描画回数（既定 2）。
    GRAFIX_SKETCH_PARAMETER_GUI : bool
        Parameter GUI を有効化する（既定 True）。
    GRAFIX_SKETCH_N_WORKER : int
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

    if _CASE == "static_layers":
        # 多レイヤーだがジオメトリは静的（geometry_id が安定）なケース。
        # GPU メッシュキャッシュ（upload skip）の効果確認用。
        return _static_layers()

    if _CASE == "upload_skip":
        # GPU upload が支配的な静的ケース（upload skip が効くかの確認用）。
        return _upload_skip_layers()

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
