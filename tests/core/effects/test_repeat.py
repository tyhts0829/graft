"""repeat effect の複製と補間に関するテスト群。"""

from __future__ import annotations

import numpy as np

from graft.api import E, G
from graft.core.primitive_registry import primitive
from graft.core.realize import realize
from graft.core.realized_geometry import RealizedGeometry


@primitive
def repeat_test_line_0_1() -> RealizedGeometry:
    """x 軸上の 2 点ポリライン（0→1）を返す。"""
    coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def repeat_test_line_1_2() -> RealizedGeometry:
    """x 軸上の 2 点ポリライン（1→2）を返す。"""
    coords = np.array([[1.0, 0.0, 0.0], [2.0, 0.0, 0.0]], dtype=np.float32)
    offsets = np.array([0, 2], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def repeat_test_two_polylines() -> RealizedGeometry:
    """2 本のポリラインを返す。"""
    a = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    b = np.array([[0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [2.0, 1.0, 0.0]], dtype=np.float32)
    coords = np.concatenate([a, b], axis=0)
    offsets = np.array([0, a.shape[0], a.shape[0] + b.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_repeat_count_zero_is_noop() -> None:
    g = G.repeat_test_line_0_1()
    base = realize(g)

    out = realize(E.repeat(count=0, offset=(10.0, 0.0, 0.0))(g))
    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=1e-6)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_repeat_offset_interpolates_over_count() -> None:
    g = G.repeat_test_line_0_1()
    out = realize(E.repeat(count=2, offset=(10.0, 0.0, 0.0))(g))

    expected = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [5.0, 0.0, 0.0],
            [6.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [11.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(out.coords, expected, rtol=0.0, atol=1e-6)
    assert out.offsets.tolist() == [0, 2, 4, 6]


def test_repeat_scale_uses_pivot_when_auto_center_false() -> None:
    g = G.repeat_test_line_1_2()
    out = realize(
        E.repeat(
            count=1,
            auto_center=False,
            pivot=(0.0, 0.0, 0.0),
            scale=(2.0, 2.0, 2.0),
        )(g)
    )

    expected = np.array(
        [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 0.0, 0.0], [4.0, 0.0, 0.0]],
        dtype=np.float32,
    )
    np.testing.assert_allclose(out.coords, expected, rtol=0.0, atol=1e-6)
    assert out.offsets.tolist() == [0, 2, 4]


def test_repeat_rotation_step_works() -> None:
    g = G.repeat_test_line_1_2()
    out = realize(
        E.repeat(
            count=1,
            auto_center=False,
            pivot=(0.0, 0.0, 0.0),
            rotation_step=(0.0, 0.0, 90.0),
        )(g)
    )

    expected = np.array(
        [[1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 2.0, 0.0]],
        dtype=np.float32,
    )
    np.testing.assert_allclose(out.coords, expected, rtol=0.0, atol=1e-6)
    assert out.offsets.tolist() == [0, 2, 4]


def test_repeat_preserves_polyline_boundaries() -> None:
    g = G.repeat_test_two_polylines()
    out = realize(E.repeat(count=1, offset=(10.0, 0.0, 0.0))(g))

    assert out.coords.shape == (10, 3)
    assert out.offsets.tolist() == [0, 2, 5, 7, 10]

    base = realize(g)
    np.testing.assert_allclose(out.coords[: base.coords.shape[0]], base.coords, rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(
        out.coords[base.coords.shape[0] :],
        base.coords + np.array([10.0, 0.0, 0.0], dtype=np.float32),
        rtol=0.0,
        atol=1e-6,
    )
