"""fill effect のハッチ生成に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.api import E, G
from src.core.primitive_registry import primitive
from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry


@primitive
def fill_test_square() -> RealizedGeometry:
    """一辺 10 の正方形（閉ポリライン）を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def fill_test_square_with_hole() -> RealizedGeometry:
    """外周+穴（2 輪郭）の正方形を返す。"""
    outer = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    hole = np.array(
        [
            [3.0, 3.0, 0.0],
            [7.0, 3.0, 0.0],
            [7.0, 7.0, 0.0],
            [3.0, 7.0, 0.0],
            [3.0, 3.0, 0.0],
        ],
        dtype=np.float32,
    )
    coords = np.concatenate([outer, hole], axis=0)
    offsets = np.array([0, outer.shape[0], outer.shape[0] + hole.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def fill_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _iter_polylines(realized: RealizedGeometry):
    offsets = realized.offsets
    for i in range(len(offsets) - 1):
        s = int(offsets[i])
        e = int(offsets[i + 1])
        yield realized.coords[s:e]


def test_fill_square_generates_expected_line_count() -> None:
    g = G.fill_test_square()
    filled = E.fill(angle_sets=1, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    assert len(realized.offsets) - 1 == 10
    assert realized.coords.shape == (20, 3)
    for seg in _iter_polylines(realized):
        assert seg.shape == (2, 3)
        assert float(seg[0, 1]) == float(seg[1, 1])


def test_fill_remove_boundary_false_keeps_input() -> None:
    g = G.fill_test_square()
    filled = E.fill(angle_sets=1, angle=0.0, density=10.0, remove_boundary=False)(g)
    realized = realize(filled)

    assert len(realized.offsets) - 1 == 11
    first = next(_iter_polylines(realized))
    np.testing.assert_allclose(first, realize(g).coords, rtol=0.0, atol=1e-6)


def test_fill_outer_with_hole_avoids_hole_region() -> None:
    g = G.fill_test_square_with_hole()
    filled = E.fill(angle_sets=1, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    # y=0..9 の 10 本のうち、穴の y 範囲 [3,7) に入る 4 本は 2 セグメントに分割される。
    assert len(realized.offsets) - 1 == 14

    for seg in _iter_polylines(realized):
        mid = seg.mean(axis=0)
        assert not (3.0 < float(mid[0]) < 7.0 and 3.0 < float(mid[1]) < 7.0)


def test_fill_empty_geometry_is_noop() -> None:
    g = G.fill_test_empty()
    filled = E.fill(angle_sets=2, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def _hatch_direction(realized: RealizedGeometry) -> np.ndarray:
    dirs: list[np.ndarray] = []
    for seg in _iter_polylines(realized):
        if seg.shape[0] < 2:
            continue
        d = seg[-1] - seg[0]
        n = float(np.linalg.norm(d))
        if n <= 1e-9:
            continue
        d = d / n
        idx = int(np.argmax(np.abs(d)))
        if float(d[idx]) < 0.0:
            d = -d
        dirs.append(d.astype(np.float64, copy=False))
    if not dirs:
        raise AssertionError("塗り線が生成されていない")
    mean = np.mean(np.stack(dirs, axis=0), axis=0)
    mean_n = float(np.linalg.norm(mean))
    if mean_n <= 0.0:
        raise AssertionError("塗り線方向の計算に失敗した")
    return (mean / mean_n).astype(np.float64, copy=False)


def test_fill_hatch_direction_is_stable_under_rotation() -> None:
    g = G.fill_test_square()

    prev_dir: np.ndarray | None = None
    for deg in np.linspace(0.0, 60.0, 31):
        rot = (float(deg), float(deg), float(deg))
        filled = (
            E.affine(rotation=rot)
            .fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g)
        )
        realized = realize(filled)
        d = _hatch_direction(realized)
        if prev_dir is not None:
            dot = float(abs(np.dot(prev_dir, d)))
            assert dot > 0.5
        prev_dir = d


def test_fill_hatch_attaches_under_z_rotation() -> None:
    g = G.fill_test_square()

    base = realize(E.fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g))
    base_dir = _hatch_direction(base)

    for deg in [0.0, 15.0, 30.0, 60.0, 120.0]:
        filled = (
            E.affine(rotation=(0.0, 0.0, float(deg)))
            .fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g)
        )
        realized = realize(filled)
        d = _hatch_direction(realized)

        th = np.deg2rad(float(deg))
        c = float(np.cos(-th))
        s = float(np.sin(-th))
        rz = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
        d_local = rz @ d
        assert float(abs(np.dot(d_local, base_dir))) > 0.99
