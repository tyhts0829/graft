"""
どこで: `tools/benchmarks/cases.py`。
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
    geometry : RealizedGeometry
        入力実体ジオメトリ（通常は 1 要素 inputs[0] として渡す）。
    """

    case_id: str
    label: str
    description: str
    geometry: RealizedGeometry


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
            geometry=_line_segment(),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="polyline_long",
            label="polyline (50k verts)",
            description="1 本の長い折れ線（頂点数が多い）",
            geometry=_polyline_sine(n_vertices=50_000),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="many_lines",
            label="many lines (5k)",
            description="短い線分を多数（ポリライン本数が多い）",
            geometry=_many_line_segments(n_lines=5_000, rng=rng),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="ring_big",
            label="ring (5k sides)",
            description="大きめの閉曲線（正多角形リング）",
            geometry=_regular_polygon_ring(n_sides=5_000, radius=120.0),
        )
    )
    cases.append(
        BenchmarkCase(
            case_id="rings_2",
            label="rings (outer+hole)",
            description="外周 + 穴の 2 リング（平面領域系 effect 向け）",
            geometry=_two_rings(outer_sides=2_000, inner_sides=1_200),
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

