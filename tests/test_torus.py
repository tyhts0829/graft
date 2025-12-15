"""torus プリミティブのワイヤーフレーム形状に関するテスト群。"""

from __future__ import annotations

import numpy as np

from src.core.geometry import Geometry
from src.core.realize import realize
from src.primitives import torus as _torus_module  # noqa: F401


def _assert_polylines_closed(coords: np.ndarray, offsets: np.ndarray) -> None:
    """各ポリラインが閉じていることを検証する。"""
    for i in range(int(offsets.shape[0]) - 1):
        start = int(offsets[i])
        end = int(offsets[i + 1])
        np.testing.assert_array_equal(coords[start], coords[end - 1])


def test_torus_offsets_and_closed_polylines() -> None:
    """子午線+緯線の本数と offsets、閉ポリラインを満たす。"""
    major_segments = 8
    minor_segments = 6
    g = Geometry.create(
        "torus",
        params={
            "major_radius": 2.0,
            "minor_radius": 0.5,
            "major_segments": major_segments,
            "minor_segments": minor_segments,
        },
    )

    realized = realize(g)

    meridian_len = minor_segments + 1
    parallel_len = major_segments + 1
    expected_coords_n = major_segments * meridian_len + minor_segments * parallel_len

    assert realized.coords.shape == (expected_coords_n, 3)
    assert realized.offsets.shape == (major_segments + minor_segments + 1,)
    assert realized.offsets[0] == 0
    assert realized.offsets[-1] == expected_coords_n

    expected_offsets = [0]
    for i in range(major_segments):
        expected_offsets.append((i + 1) * meridian_len)
    base = major_segments * meridian_len
    for j in range(minor_segments):
        expected_offsets.append(base + (j + 1) * parallel_len)

    assert realized.offsets.tolist() == expected_offsets
    _assert_polylines_closed(realized.coords, realized.offsets)


def test_torus_center_and_scale_affect_coords() -> None:
    """center/scale が座標に反映される。"""
    params = {
        "major_radius": 2.0,
        "minor_radius": 0.5,
        "major_segments": 7,
        "minor_segments": 5,
    }

    base = realize(Geometry.create("torus", params=params))

    scaled = realize(
        Geometry.create(
            "torus",
            params={
                **params,
                "center": (10.0, 20.0, 30.0),
                "scale": (2.0, 3.0, 4.0),
            },
        )
    )

    scale_vec = np.array([2.0, 3.0, 4.0], dtype=np.float32)
    center_vec = np.array([10.0, 20.0, 30.0], dtype=np.float32)
    expected = base.coords * scale_vec + center_vec
    np.testing.assert_array_equal(scaled.coords, expected)


def test_torus_clamps_segments_lt_3() -> None:
    """major_segments/minor_segments < 3 は 3 にクランプされる。"""
    g = Geometry.create(
        "torus",
        params={
            "major_segments": 2,
            "minor_segments": 1,
        },
    )
    realized = realize(g)

    major_segments = 3
    minor_segments = 3
    meridian_len = minor_segments + 1
    parallel_len = major_segments + 1
    expected_coords_n = major_segments * meridian_len + minor_segments * parallel_len

    assert realized.coords.shape == (expected_coords_n, 3)
    assert realized.offsets.tolist() == [0, 4, 8, 12, 16, 20, 24]
    _assert_polylines_closed(realized.coords, realized.offsets)

