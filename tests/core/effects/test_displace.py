"""displace effect（Perlin ノイズ変位）の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.effects.displace import displace as displace_impl
from grafix.core.operation_authoring import primitive
from grafix.core.realize import RealizeError, realize
from grafix.core.realized_geometry import GeomTuple


@primitive
def displace_test_polyline() -> GeomTuple:
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
    return coords, offsets


@primitive
def displace_test_empty() -> GeomTuple:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return coords, offsets


@primitive
def displace_test_radial_points_xy() -> GeomTuple:
    """(x,y) 方向の円形マスク検証用の点列を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [5.0, 5.0, 0.0],
            [9.0, 5.0, 0.0],
            [9.0, 9.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return coords, offsets


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"min_gradient_factor": -0.1}, "min_gradient_factor"),
        ({"min_gradient_factor": 1.1}, "min_gradient_factor"),
        ({"max_gradient_factor": 0.9}, "max_gradient_factor"),
        ({"max_gradient_factor": 4.1}, "max_gradient_factor"),
    ],
)
def test_displace_rejects_invalid_factor_ranges_through_public_effect(
    kwargs: dict[str, object],
    match: str,
) -> None:
    empty = G.displace_test_empty()

    with pytest.raises(RealizeError) as exc_info:
        realize(E.displace(**kwargs)(empty))  # type: ignore[arg-type]
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert match in str(exc_info.value.__cause__)


@pytest.mark.parametrize(
    "gradient_radius",
    [(0.0, 0.5, 0.5), (-0.5, 0.5, 0.5)],
)
def test_displace_radial_rejects_nonpositive_radius_before_empty_input(
    gradient_radius: tuple[float, float, float],
) -> None:
    with pytest.raises(RealizeError) as exc_info:
        realize(
            E.displace(
                gradient_profile="radial",
                gradient_radius=gradient_radius,
            )(G.displace_test_empty())
        )

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "gradient_radius" in str(exc_info.value.__cause__)


def test_displace_amplitude_zero_is_noop() -> None:
    g = G.displace_test_polyline()
    base = realize(g)
    out = realize(
        E.displace(
            amplitude=(0.0, 0.0, 0.0),
            spatial_freq=(0.04, 0.04, 0.04),
            amplitude_gradient=(0.0, 0.0, 0.0),
            frequency_gradient=(0.0, 0.0, 0.0),
            min_gradient_factor=0.1,
            max_gradient_factor=2.0,
            t=0.0,
        )(g)
    )
    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_displace_changes_coords_and_preserves_offsets() -> None:
    g = G.displace_test_polyline()
    base = realize(g)
    out = realize(
        E.displace(amplitude=(8.0, 8.0, 8.0), spatial_freq=(0.04, 0.04, 0.04))(g)
    )

    assert out.coords.shape == base.coords.shape
    assert out.coords.dtype == np.float32
    assert out.offsets.tolist() == base.offsets.tolist()
    assert float(np.max(np.abs(out.coords - base.coords))) > 1e-4


def test_displace_deterministic_for_same_inputs() -> None:
    g = G.displace_test_polyline()
    base = realize(g)

    base_tuple = (base.coords, base.offsets)
    out1_coords, out1_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(0.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )
    out2_coords, out2_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(0.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )

    np.testing.assert_allclose(out1_coords, out2_coords, rtol=0.0, atol=0.0)
    assert out1_offsets.tolist() == out2_offsets.tolist()


def test_displace_fixed_input_output_characterization() -> None:
    base = realize(G.displace_test_polyline())

    out_coords, out_offsets = displace_impl(
        (base.coords, base.offsets),
        amplitude=(2.0, 3.0, 4.0),
        spatial_freq=(0.07, 0.05, 0.03),
        amplitude_gradient=(0.5, -0.25, 0.75),
        frequency_gradient=(0.2, 0.1, -0.1),
        gradient_center_offset=(0.1, -0.2, 0.0),
        min_gradient_factor=0.2,
        max_gradient_factor=2.5,
        t=0.375,
    )

    expected = np.array(
        [
            [-0.019935915, 0.28738573, -0.98359835],
            [5.381473, 0.81586874, -0.9492474],
            [10.936907, 3.8023105, -0.09292004],
            [13.466412, 9.606008, 1.2416333],
        ],
        dtype=np.float32,
    )
    np.testing.assert_array_equal(out_coords, expected)
    np.testing.assert_array_equal(out_offsets, base.offsets)


def test_displace_radial_gradient_output_characterization() -> None:
    base = realize(G.displace_test_polyline())

    out_coords, out_offsets = displace_impl(
        (base.coords, base.offsets),
        amplitude=(2.3, -4.1, 1.2),
        spatial_freq=(0.03, 0.07, -0.02),
        amplitude_gradient=(1.7, -2.8, 0.3),
        frequency_gradient=(2.7, -5.8, 0.3),
        gradient_center_offset=(0.2, -0.1, 0.4),
        gradient_profile="radial",
        gradient_radius=(0.3, 0.7, 1.2),
        min_gradient_factor=0.0,
        max_gradient_factor=4.0,
        t=0.25,
    )

    expected = np.array(
        [
            [0.1, 0.0945662, 0.37714347],
            [5.1, 0.0, 0.1784323],
            [10.2, 5.048712, -0.42965135],
            [12.3, 6.7496, -0.09342495],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(out_coords, expected, rtol=0.0, atol=1e-6)
    np.testing.assert_array_equal(out_offsets, base.offsets)


def test_displace_time_changes_output() -> None:
    g = G.displace_test_polyline()
    base = realize(g)

    base_tuple = (base.coords, base.offsets)
    out0_coords, _out0_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        t=0.0,
    )
    out1_coords, _out1_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        t=0.25,
    )
    assert float(np.max(np.abs(out1_coords - out0_coords))) > 1e-4


def test_displace_empty_geometry_is_noop() -> None:
    g = G.displace_test_empty()
    out = realize(
        E.displace(amplitude=(8.0, 8.0, 8.0), spatial_freq=(0.04, 0.04, 0.04))(g)
    )
    assert out.coords.shape == (0, 3)
    assert out.offsets.tolist() == [0]


def test_displace_gradient_center_offset_noop_without_gradient() -> None:
    g = G.displace_test_polyline()
    base = realize(g)

    base_tuple = (base.coords, base.offsets)
    out0_coords, _out0_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(0.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        gradient_center_offset=(0.0, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )
    out1_coords, _out1_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(0.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        gradient_center_offset=(0.25, -0.25, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )

    np.testing.assert_allclose(out1_coords, out0_coords, rtol=0.0, atol=0.0)


def test_displace_gradient_center_offset_changes_output_with_gradient() -> None:
    g = G.displace_test_polyline()
    base = realize(g)

    base_tuple = (base.coords, base.offsets)
    out0_coords, _out0_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(2.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        gradient_center_offset=(0.0, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )
    out1_coords, _out1_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(2.0, 0.0, 0.0),
        frequency_gradient=(0.0, 0.0, 0.0),
        gradient_center_offset=(0.25, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )

    assert float(np.max(np.abs(out1_coords - out0_coords))) > 1e-4


def test_displace_gradient_profile_default_is_linear() -> None:
    g = G.displace_test_polyline()
    base = realize(g)
    base_tuple = (base.coords, base.offsets)
    out0_coords, out0_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(2.0, 0.0, 0.0),
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )
    out1_coords, out1_offsets = displace_impl(
        base_tuple,
        amplitude=(8.0, 8.0, 8.0),
        spatial_freq=(0.04, 0.04, 0.04),
        amplitude_gradient=(2.0, 0.0, 0.0),
        gradient_profile="linear",
        min_gradient_factor=0.1,
        max_gradient_factor=2.0,
        t=0.0,
    )
    np.testing.assert_allclose(out1_coords, out0_coords, rtol=0.0, atol=0.0)
    assert out1_offsets.tolist() == out0_offsets.tolist()


def test_E_displace_gradient_center_offset_zero_equals_omitted() -> None:
    g = G.displace_test_polyline()

    out0 = realize(
        E.displace(
            amplitude=(8.0, 8.0, 8.0),
            spatial_freq=(0.04, 0.04, 0.04),
            amplitude_gradient=(2.0, 0.0, 0.0),
            t=0.0,
        )(g)
    )
    out1 = realize(
        E.displace(
            amplitude=(8.0, 8.0, 8.0),
            spatial_freq=(0.04, 0.04, 0.04),
            amplitude_gradient=(2.0, 0.0, 0.0),
            gradient_center_offset=(0.0, 0.0, 0.0),
            t=0.0,
        )(g)
    )

    np.testing.assert_allclose(out1.coords, out0.coords, rtol=0.0, atol=0.0)
    assert out1.offsets.tolist() == out0.offsets.tolist()


def test_displace_radial_profile_round_mask_in_xy() -> None:
    g = G.displace_test_radial_points_xy()
    base = realize(g)
    out_coords, _out_offsets = displace_impl(
        (base.coords, base.offsets),
        amplitude=(50.0, 0.0, 0.0),
        spatial_freq=(0.0, 0.0, 0.0),
        amplitude_gradient=(1.0, 0.0, 0.0),
        gradient_profile="radial",
        gradient_radius=(0.5, 0.5, 0.5),
        min_gradient_factor=0.0,
        max_gradient_factor=4.0,
        t=0.123,
    )
    dx = out_coords[:, 0] - base.coords[:, 0]
    np.testing.assert_allclose(dx[[0, 1, 4]], 0.0, rtol=0.0, atol=0.0)
    assert abs(float(dx[2])) > abs(float(dx[3])) > 1e-4


def test_displace_radial_profile_inverts_with_negative_gradient() -> None:
    g = G.displace_test_radial_points_xy()
    base = realize(g)
    out_coords, _out_offsets = displace_impl(
        (base.coords, base.offsets),
        amplitude=(50.0, 0.0, 0.0),
        spatial_freq=(0.0, 0.0, 0.0),
        amplitude_gradient=(-1.0, 0.0, 0.0),
        gradient_profile="radial",
        gradient_radius=(0.5, 0.5, 0.5),
        min_gradient_factor=0.0,
        max_gradient_factor=4.0,
        t=0.123,
    )
    dx = out_coords[:, 0] - base.coords[:, 0]
    assert abs(float(dx[0])) > abs(float(dx[2])) > 1e-4
