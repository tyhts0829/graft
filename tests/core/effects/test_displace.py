"""displace effect（Perlin ノイズ変位）の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.api import E, G
from src.core.primitive_registry import primitive
from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry
from src.core.effects.displace import displace as displace_impl


@primitive
def displace_test_polyline() -> RealizedGeometry:
    """適度に非整数な 4 点ポリラインを返す。"""
    coords = np.array(
        [
            [0.1, 0.2, 0.3],
            [5.1, 0.0, 0.0],
            [10.2, 3.4, 0.0],
            [12.3, 9.8, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def displace_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_displace_amplitude_zero_is_noop() -> None:
    g = G.displace_test_polyline()
    base = realize(g)
    out = realize(
        E.displace(
            amplitude_mm=(0.0, 0.0, 0.0),
            spatial_freq=(0.04, 0.04, 0.04),
            amplitude_gradient=(0.0, 0.0, 0.0),
            frequency_gradient=(0.0, 0.0, 0.0),
            min_gradient_factor=0.1,
            max_gradient_factor=2.0,
            t_sec=0.0,
        )(g)
    )
    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_displace_changes_coords_and_preserves_offsets() -> None:
    g = G.displace_test_polyline()
    base = realize(g)
    out = realize(E.displace(amplitude_mm=(8.0, 8.0, 8.0), spatial_freq=(0.04, 0.04, 0.04))(g))

    assert out.coords.shape == base.coords.shape
    assert out.coords.dtype == np.float32
    assert out.offsets.tolist() == base.offsets.tolist()
    assert float(np.max(np.abs(out.coords - base.coords))) > 1e-4


def test_displace_deterministic_for_same_inputs() -> None:
    g = G.displace_test_polyline()
    base = realize(g)

    out1 = displace_impl(
        [base],
        amplitude_mm=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(0.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t_sec=0.0,
    )
    out2 = displace_impl(
        [base],
        amplitude_mm=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(0.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t_sec=0.0,
    )

    np.testing.assert_allclose(out1.coords, out2.coords, rtol=0.0, atol=0.0)
    assert out1.offsets.tolist() == out2.offsets.tolist()


def test_displace_time_changes_output() -> None:
    g = G.displace_test_polyline()
    base = realize(g)

    out0 = displace_impl(
        [base],
        amplitude_mm=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        t_sec=0.0,
    )
    out1 = displace_impl(
        [base],
        amplitude_mm=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        t_sec=0.25,
    )
    assert float(np.max(np.abs(out1.coords - out0.coords))) > 1e-4


def test_displace_empty_geometry_is_noop() -> None:
    g = G.displace_test_empty()
    out = realize(E.displace(amplitude_mm=(8.0, 8.0, 8.0), spatial_freq=(0.04, 0.04, 0.04))(g))
    assert out.coords.shape == (0, 3)
    assert out.offsets.tolist() == [0]
