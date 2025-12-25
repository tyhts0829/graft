"""fill effect のハッチ生成に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.effects.fill import _build_evenodd_groups, _point_in_polygon, _polygon_area_abs
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


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


def _square_loop(x0: float, y0: float, size: float) -> np.ndarray:
    return np.array(
        [
            [x0, y0, 0.0],
            [x0 + size, y0, 0.0],
            [x0 + size, y0 + size, 0.0],
            [x0, y0 + size, 0.0],
            [x0, y0, 0.0],
        ],
        dtype=np.float32,
    )


@primitive
def fill_test_three_disjoint_squares() -> RealizedGeometry:
    """離れた 3 つの正方形（閉ポリライン×3）を返す。"""
    a = _square_loop(0.0, 0.0, 10.0)
    b = _square_loop(20.0, 0.0, 10.0)
    c = _square_loop(40.0, 0.0, 10.0)
    coords = np.concatenate([a, b, c], axis=0)
    offsets = np.array([0, a.shape[0], a.shape[0] + b.shape[0], a.shape[0] + b.shape[0] + c.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def fill_test_two_disjoint_squares() -> RealizedGeometry:
    """離れた 2 つの正方形（閉ポリライン×2）を返す。"""
    a = _square_loop(0.0, 0.0, 10.0)
    b = _square_loop(20.0, 0.0, 10.0)
    coords = np.concatenate([a, b], axis=0)
    offsets = np.array([0, a.shape[0], a.shape[0] + b.shape[0]], dtype=np.int32)
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


def test_fill_evenodd_grouping_groups_square_with_hole() -> None:
    g = G.fill_test_square_with_hole()
    base = realize(g)
    coords2d = base.coords[:, :2].astype(np.float32, copy=False)
    assert _build_evenodd_groups(coords2d, base.offsets) == [[0, 1]]


def test_fill_evenodd_grouping_does_not_treat_touching_polygons_as_hole() -> None:
    # 隣接セル（共有辺/頂点）の代表点が「境界上」に載るケースを想定し、
    # グルーピングが誤って hole 扱いしないことを担保する。
    outer = np.array(
        [
            [0.0, 0.0],
            [2.0, 0.0],
            [2.0, 2.0],
            [0.0, 2.0],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )
    touching = np.array(
        [
            [0.0, 0.5],
            [-1.0, 0.5],
            [-1.0, 1.5],
            [0.0, 1.5],
            [0.0, 0.5],
        ],
        dtype=np.float32,
    )
    coords2d = np.concatenate([outer, touching], axis=0)
    offsets = np.array([0, outer.shape[0], outer.shape[0] + touching.shape[0]], dtype=np.int32)

    assert _build_evenodd_groups(coords2d, offsets) == [[0], [1]]


def test_point_in_polygon_treats_boundary_as_outside() -> None:
    poly = np.array(
        [
            [0.0, 0.0],
            [10.0, 0.0],
            [10.0, 10.0],
            [0.0, 10.0],
            [0.0, 0.0],
        ],
        dtype=np.float32,
    )

    assert _point_in_polygon(np.array([5.0, 5.0], dtype=np.float32), poly)
    assert not _point_in_polygon(np.array([0.0, 5.0], dtype=np.float32), poly)
    assert not _point_in_polygon(np.array([0.0, 0.0], dtype=np.float32), poly)
    assert not _point_in_polygon(np.array([15.0, 5.0], dtype=np.float32), poly)


def test_fill_text_o_respects_hole() -> None:
    g = G.text(text="o", font="SFNS.ttf", scale=100.0)
    boundary = realize(g)
    coords2d = boundary.coords[:, :2].astype(np.float32, copy=False)
    groups = _build_evenodd_groups(coords2d, boundary.offsets)

    hole_poly: np.ndarray | None = None
    for group in groups:
        if len(group) < 2:
            continue
        areas: list[tuple[float, int]] = []
        for ring_i in group:
            s = int(boundary.offsets[ring_i])
            e = int(boundary.offsets[ring_i + 1])
            areas.append((_polygon_area_abs(coords2d[s:e]), int(ring_i)))
        areas.sort(key=lambda t: t[0])
        hole_i = areas[0][1]
        s = int(boundary.offsets[hole_i])
        e = int(boundary.offsets[hole_i + 1])
        hole_poly = coords2d[s:e]
        break

    assert hole_poly is not None
    x0 = float(np.min(hole_poly[:, 0]))
    x1 = float(np.max(hole_poly[:, 0]))
    y0 = float(np.min(hole_poly[:, 1]))
    y1 = float(np.max(hole_poly[:, 1]))
    probe = np.array([(x0 + x1) * 0.5, (y0 + y1) * 0.5], dtype=np.float32)
    assert _point_in_polygon(probe, hole_poly)

    filled = realize(E.fill(angle_sets=1, angle=0.0, density=25.0, remove_boundary=True)(g))
    assert filled.coords.shape[0] > 0
    for seg in _iter_polylines(filled):
        if seg.shape != (2, 3):
            continue
        mid = seg.mean(axis=0)
        assert not _point_in_polygon(mid[:2].astype(np.float32, copy=False), hole_poly)


def _mean_dir_from_segments(segments: list[np.ndarray]) -> np.ndarray:
    dirs: list[np.ndarray] = []
    for seg in segments:
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
        raise AssertionError("線分が無い")
    mean = np.mean(np.stack(dirs, axis=0), axis=0)
    mean_n = float(np.linalg.norm(mean))
    if mean_n <= 0.0:
        raise AssertionError("方向平均が 0")
    return (mean / mean_n).astype(np.float64, copy=False)


def test_fill_groupwise_angle_cycles_across_disjoint_rings() -> None:
    g = G.fill_test_three_disjoint_squares()
    filled = E.fill(angle_sets=1, angle=[0.0, 90.0], density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    buckets: list[list[np.ndarray]] = [[], [], []]
    for seg in _iter_polylines(realized):
        if seg.shape != (2, 3):
            continue
        mid = seg.mean(axis=0)
        x = float(mid[0])
        if x < 15.0:
            buckets[0].append(seg)
        elif x < 35.0:
            buckets[1].append(seg)
        else:
            buckets[2].append(seg)

    assert all(buckets)
    d0 = _mean_dir_from_segments(buckets[0])
    d1 = _mean_dir_from_segments(buckets[1])
    d2 = _mean_dir_from_segments(buckets[2])

    # angle=0° -> 水平（+X）
    assert float(d0[0]) > 0.99 and abs(float(d0[1])) < 0.05
    # angle=90° -> 垂直（+Y）
    assert float(d1[1]) > 0.99 and abs(float(d1[0])) < 0.05
    # cycle で再び angle=0°
    assert float(d2[0]) > 0.99 and abs(float(d2[1])) < 0.05


def test_fill_groupwise_remove_boundary_cycles() -> None:
    g = G.fill_test_two_disjoint_squares()
    filled = E.fill(density=0.0, remove_boundary=[True, False])(g)
    realized = realize(filled)

    # 1つ目は remove_boundary=True なので境界が出ない。2つ目だけ境界が残る。
    assert len(realized.offsets) - 1 == 1
    poly = next(_iter_polylines(realized))
    assert poly.shape[0] == 5
    assert float(np.mean(poly[:, 0])) > 15.0


def test_fill_empty_geometry_is_noop() -> None:
    g = G.fill_test_empty()
    filled = E.fill(angle_sets=2, angle=0.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def test_fill_degenerate_input_is_noop() -> None:
    g = E.scale(scale=(50.0, 0.0, 1.0))(G.polygon(scale=1.0))
    base = realize(g)

    # 退化入力（面積ほぼ 0）は fill を適用してもそのまま返す（remove_boundary も無視する）。
    filled = E.fill(angle_sets=1, angle=45.0, density=10.0, remove_boundary=True)(g)
    realized = realize(filled)

    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == base.offsets.tolist()


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
