"""twist effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import RealizeError, realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def twist_test_line_y3() -> RealizedGeometry:
    """y=0/0.5/1 の 3 点ポリラインを返す（x=1,z=0 固定）。"""
    coords = np.array([[1.0, 0.0, 0.0], [1.0, 0.5, 0.0], [1.0, 1.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 3], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def twist_test_line_y3_x2() -> RealizedGeometry:
    """y=0/0.5/1 の 3 点ポリラインを返す（x=2,z=0 固定）。"""
    coords = np.array([[2.0, 0.0, 0.0], [2.0, 0.5, 0.0], [2.0, 1.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 3], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def twist_test_asym_y3() -> RealizedGeometry:
    """auto_center 検証用の 3 点ポリラインを返す。"""
    coords = np.array([[2.0, 0.0, 0.0], [0.0, 0.5, 0.0], [2.0, 1.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 3], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def twist_test_two_lines_y2() -> RealizedGeometry:
    """y=0/1 の 2 点線分×2 本を返す（offsets 検証用）。"""
    coords = np.array(
        [[1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [2.0, 0.0, 0.0], [2.0, 1.0, 0.0]],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 4], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def twist_test_same_y() -> RealizedGeometry:
    """y が一定の 2 点線分を返す（rng=0 no-op 用）。"""
    coords = np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def twist_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_twist_y_axis_90_at_ends_and_zero_in_middle() -> None:
    g = G.twist_test_line_y3()
    realized = realize(
        E.twist(
            angle=90.0,
            axis_dir=(0.0, 1.0, 0.0),
            auto_center=False,
            pivot=(0.0, 0.0, 0.0),
        )(g)
    )

    expected = np.array([[0.0, 0.0, -1.0], [1.0, 0.5, 0.0], [0.0, 1.0, 1.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 3]


def test_twist_angle_zero_is_noop() -> None:
    g = G.twist_test_line_y3()
    base = realize(g)
    realized = realize(E.twist(angle=0.0, axis_dir=(0.0, 1.0, 0.0))(g))
    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=0.0)
    assert realized.offsets.tolist() == base.offsets.tolist()


def test_twist_axis_range_zero_is_noop() -> None:
    g = G.twist_test_same_y()
    base = realize(g)
    realized = realize(E.twist(angle=90.0, axis_dir=(0.0, 1.0, 0.0))(g))
    np.testing.assert_allclose(realized.coords, base.coords, rtol=0.0, atol=0.0)
    assert realized.offsets.tolist() == base.offsets.tolist()


def test_twist_preserves_offsets_for_multiple_polylines() -> None:
    g = G.twist_test_two_lines_y2()
    realized = realize(E.twist(angle=90.0, axis_dir=(0.0, 1.0, 0.0))(g))
    assert realized.coords.shape == (4, 3)
    assert realized.offsets.tolist() == [0, 2, 4]


def test_twist_empty_geometry_is_noop() -> None:
    g = G.twist_test_empty()
    realized = realize(E.twist(angle=90.0, axis_dir=(0.0, 1.0, 0.0))(g))
    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def test_twist_axis_dir_zero_raises() -> None:
    g = G.twist_test_line_y3()
    with pytest.raises(RealizeError) as exc:
        _ = realize(E.twist(angle=90.0, axis_dir=(0.0, 0.0, 0.0))(g))
    assert isinstance(exc.value.__cause__, ValueError)


def test_twist_pivot_changes_rotation_center() -> None:
    g = G.twist_test_line_y3_x2()
    realized = realize(
        E.twist(
            angle=90.0,
            axis_dir=(0.0, 1.0, 0.0),
            auto_center=False,
            pivot=(1.0, 0.0, 0.0),
        )(g)
    )

    expected = np.array([[1.0, 0.0, -1.0], [2.0, 0.5, 0.0], [1.0, 1.0, 1.0]], dtype=np.float32)
    np.testing.assert_allclose(realized.coords, expected, rtol=0.0, atol=1e-6)


def test_twist_auto_center_matches_explicit_pivot_mean() -> None:
    g = G.twist_test_asym_y3()
    base = realize(g)
    center = base.coords.astype(np.float64, copy=False).mean(axis=0)
    pivot = (float(center[0]), float(center[1]), float(center[2]))

    out_auto_center = realize(E.twist(angle=90.0, axis_dir=(0.0, 1.0, 0.0), auto_center=True)(g))
    out_pivot = realize(
        E.twist(
            angle=90.0,
            axis_dir=(0.0, 1.0, 0.0),
            auto_center=False,
            pivot=pivot,
        )(g)
    )
    np.testing.assert_allclose(out_auto_center.coords, out_pivot.coords, rtol=0.0, atol=1e-6)


def test_twist_axis_dir_sign_flip_is_equivalent() -> None:
    g = G.twist_test_line_y3_x2()
    out_pos = realize(E.twist(angle=90.0, axis_dir=(0.0, 1.0, 0.0))(g))
    out_neg = realize(E.twist(angle=90.0, axis_dir=(0.0, -1.0, 0.0))(g))
    np.testing.assert_allclose(out_pos.coords, out_neg.coords, rtol=0.0, atol=1e-6)
