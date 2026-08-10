"""
どこで: `src/grafix/devtools/benchmarks/cases.py`。
何を: effect ベンチ用の入力ジオメトリ（ケース）を生成する。
なぜ: 「点数が多い」「線が多い」「閉曲線」など特徴の違いで性能差を比較するため。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from grafix.core.realized_geometry import RealizedGeometry


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """effect ベンチ入力ケース。

    Attributes
    ----------
    case_id : str
        安定な識別子。
    label : str
        レポート表示用の短い名前。
    description : str
        目的・形状・規模の説明。
    inputs : tuple[RealizedGeometry, ...]
        effect へ順番どおりに渡す入力実体ジオメトリ列。
    tags : tuple[str, ...]
        入力の特徴を表す安定な scenario tag。
    """

    case_id: str
    label: str
    description: str
    inputs: tuple[RealizedGeometry, ...]
    tags: tuple[str, ...]

    @property
    def n_inputs(self) -> int:
        """effect へ渡す入力数を返す。"""

        return len(self.inputs)


def describe_geometry(geom: RealizedGeometry) -> dict[str, int | bool]:
    """RealizedGeometry の規模情報を辞書で返す。"""
    n_vertices = int(geom.coords.shape[0])
    n_lines = int(geom.offsets.size) - 1

    closed_lines = 0
    for i in range(n_lines):
        s = int(geom.offsets[i])
        e = int(geom.offsets[i + 1])
        pts = geom.coords[s:e]
        if pts.shape[0] >= 2 and np.allclose(pts[0], pts[-1], atol=1e-6, rtol=0.0):
            closed_lines += 1

    return {
        "n_vertices": n_vertices,
        "n_lines": n_lines,
        "closed_lines": int(closed_lines),
        "all_closed": bool(n_lines > 0 and closed_lines == n_lines),
    }


def build_default_cases(*, seed: int) -> list[BenchmarkCase]:
    """ベンチの既定ケース列を生成して返す。

    Notes
    -----
    - すべて XY 平面（z=0）上で生成する（planar 判定で no-op になりにくくする）。
    - サイズは「正確さ寄り」で少し重めにしている。必要なら CLI 側でケースを絞る。
    """
    rng = np.random.default_rng(int(seed))

    cases: list[BenchmarkCase] = []

    cases.append(
        BenchmarkCase(
            case_id="line_small",
            label="line (2 verts)",
            description="最小ケース（2 点の線分 1 本）",
            inputs=(_line_segment(),),
            tags=("unary", "small"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="polyline_long",
            label="polyline (50k verts)",
            description="1 本の長い折れ線（頂点数が多い）",
            inputs=(_polyline_sine(n_vertices=50_000),),
            tags=("unary", "huge-single-line"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="polyline_spaced_long",
            label="spaced polyline (50k verts)",
            description="subdivide の最短線分ガードを確実に超える長い折れ線",
            inputs=(_polyline_spaced(n_vertices=50_000, spacing=0.125),),
            tags=("unary", "huge-single-line", "subdivide-actual-work"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="many_lines",
            label="many lines (5k)",
            description="短い線分を多数（ポリライン本数が多い）",
            inputs=(_many_line_segments(n_lines=5_000, rng=rng),),
            tags=("unary", "many-short-lines"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="ring_big",
            label="ring (5k sides)",
            description="大きめの閉曲線（正多角形リング）",
            inputs=(_regular_polygon_ring(n_sides=5_000, radius=120.0),),
            tags=("unary", "rings"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="rings_2",
            label="rings (outer+hole)",
            description="外周 + 穴の 2 リング（平面領域系 effect 向け）",
            inputs=(_two_rings(outer_sides=2_000, inner_sides=1_200),),
            tags=("unary", "mask-grid", "rings"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="fill_ring_height_100",
            label="fill ring (4k edges, height 100)",
            description=(
                "fill の旧/new density 変換で同じ間隔になる、高さ 100・4096 辺の閉曲線"
            ),
            inputs=(_regular_polygon_ring(n_sides=4_096, radius=50.0),),
            tags=("unary", "rings", "high-edge", "scale-1x"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="fill_ring_height_400",
            label="fill ring (4k edges, height 400)",
            description=(
                "高さ 100 の matched fill fixture を辺数一定のまま 4 倍した閉曲線"
            ),
            inputs=(_regular_polygon_ring(n_sides=4_096, radius=200.0),),
            tags=("unary", "rings", "high-edge", "scale-4x"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="many_rings",
            label="many rings (512)",
            description="離して並べた正方形リング 512 個",
            inputs=(_many_square_rings(n_rings=512),),
            tags=("unary", "many-short-lines", "rings"),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="binary_mask",
            label="binary (lines + rings)",
            description="多数の横線 + 外周と穴のマスク（clip / warp 向け）",
            inputs=(
                _parallel_lines(n_lines=1_000, half_extent=180.0),
                _two_rings(outer_sides=512, inner_sides=256),
            ),
            tags=("binary", "mask-grid", "many-short-lines", "rings"),
        )
    )

    return cases


def _line_segment() -> RealizedGeometry:
    coords = np.asarray([[0.0, 0.0, 0.0], [100.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.asarray([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _polyline_sine(*, n_vertices: int) -> RealizedGeometry:
    n = int(n_vertices)
    if n < 2:
        n = 2

    t = np.linspace(0.0, 60.0, num=n, dtype=np.float32)
    x = t
    y = (30.0 * np.sin(t * 0.4)).astype(np.float32, copy=False)
    z = np.zeros_like(x)
    coords = np.stack([x, y, z], axis=1).astype(np.float32, copy=False)
    offsets = np.asarray([0, n], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _polyline_spaced(*, n_vertices: int, spacing: float) -> RealizedGeometry:
    """隣接距離が ``subdivide`` の停止閾値を十分上回る折れ線を返す。"""

    n = max(2, int(n_vertices))
    step = np.float32(spacing)
    x = np.arange(n, dtype=np.float32) * step
    y = (5.0 * np.sin(x * np.float32(0.02))).astype(np.float32, copy=False)
    z = np.zeros_like(x)
    coords = np.stack([x, y, z], axis=1).astype(np.float32, copy=False)
    offsets = np.asarray([0, n], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _many_line_segments(*, n_lines: int, rng: np.random.Generator) -> RealizedGeometry:
    m = int(n_lines)
    if m < 1:
        m = 1

    # ランダムに散らした短い線分（向きと長さは固定、位置のみ乱数）。
    starts = rng.uniform(-200.0, 200.0, size=(m, 3)).astype(np.float32, copy=False)
    starts[:, 2] = 0.0
    delta = np.array([10.0, 3.0, 0.0], dtype=np.float32)
    ends = starts + delta[None, :]

    coords = np.empty((m * 2, 3), dtype=np.float32)
    coords[0::2] = starts
    coords[1::2] = ends
    offsets = np.arange(0, coords.shape[0] + 1, 2, dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _parallel_lines(*, n_lines: int, half_extent: float) -> RealizedGeometry:
    """同じ長さの水平線を等間隔に並べた geometry を返す。"""

    count = max(1, int(n_lines))
    extent = abs(float(half_extent))
    ys = np.linspace(-extent, extent, num=count, dtype=np.float32)

    coords = np.empty((2 * count, 3), dtype=np.float32)
    coords[0::2, 0] = np.float32(-extent)
    coords[1::2, 0] = np.float32(extent)
    coords[0::2, 1] = ys
    coords[1::2, 1] = ys
    coords[:, 2] = np.float32(0.0)
    offsets = np.arange(0, coords.shape[0] + 1, 2, dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _regular_polygon_ring(*, n_sides: int, radius: float) -> RealizedGeometry:
    sides = int(n_sides)
    if sides < 3:
        sides = 3
    r = float(radius)

    angles = np.linspace(0.0, 2.0 * np.pi, num=sides, endpoint=False, dtype=np.float64)
    x = (r * np.cos(angles)).astype(np.float32, copy=False)
    y = (r * np.sin(angles)).astype(np.float32, copy=False)
    z = np.zeros_like(x)

    coords = np.stack([x, y, z], axis=1).astype(np.float32, copy=False)
    coords = np.concatenate([coords, coords[:1]], axis=0)
    offsets = np.asarray([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _two_rings(*, outer_sides: int, inner_sides: int) -> RealizedGeometry:
    outer = _regular_polygon_ring(n_sides=int(outer_sides), radius=150.0)
    inner = _regular_polygon_ring(n_sides=int(inner_sides), radius=60.0)
    coords = np.concatenate([outer.coords, inner.coords], axis=0).astype(np.float32, copy=False)
    offsets = np.asarray(
        [
            0,
            int(outer.coords.shape[0]),
            int(outer.coords.shape[0] + inner.coords.shape[0]),
        ],
        dtype=np.int32,
    )
    return RealizedGeometry(coords=coords, offsets=offsets)


def _many_square_rings(*, n_rings: int) -> RealizedGeometry:
    """互いに接触しない正方形リングを規則格子へ並べて返す。"""

    count = max(1, int(n_rings))
    columns = int(np.ceil(np.sqrt(float(count))))
    indices = np.arange(count, dtype=np.int32)
    center_x = (indices % columns - np.float32(0.5 * float(columns - 1))).astype(
        np.float32
    ) * np.float32(4.0)
    rows = int(np.ceil(float(count) / float(columns)))
    center_y = (indices // columns - np.float32(0.5 * float(rows - 1))).astype(
        np.float32
    ) * np.float32(4.0)

    corners = np.asarray(
        [
            [-1.0, -1.0],
            [1.0, -1.0],
            [1.0, 1.0],
            [-1.0, 1.0],
            [-1.0, -1.0],
        ],
        dtype=np.float32,
    )
    coords = np.zeros((count, 5, 3), dtype=np.float32)
    coords[:, :, 0] = center_x[:, None] + corners[None, :, 0]
    coords[:, :, 1] = center_y[:, None] + corners[None, :, 1]
    packed = coords.reshape(count * 5, 3)
    offsets = np.arange(0, packed.shape[0] + 1, 5, dtype=np.int32)
    return RealizedGeometry(coords=packed, offsets=offsets)
