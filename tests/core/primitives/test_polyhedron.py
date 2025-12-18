"""polyhedron プリミティブの面ポリライン形状に関するテスト群。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix.core.geometry import Geometry
from grafix.core.realize import realize
from grafix.core.primitives import polyhedron as _polyhedron_module  # noqa: F401


@pytest.mark.parametrize(
    "type_index,expected_faces",
    [
        (0, 4),  # tetrahedron
        (1, 6),  # hexahedron
        (2, 8),  # octahedron
        (3, 12),  # dodecahedron
        (4, 20),  # icosahedron
    ],
)
def test_polyhedron_face_count_and_closed_polylines(type_index: int, expected_faces: int) -> None:
    """面数が一致し、各面が閉ポリライン（先頭==末尾）になっている。"""
    g = Geometry.create("polyhedron", params={"type_index": type_index})
    realized = realize(g)

    assert realized.offsets.shape[0] == expected_faces + 1

    for i in range(expected_faces):
        start = int(realized.offsets[i])
        end = int(realized.offsets[i + 1])
        assert end - start >= 2
        np.testing.assert_array_equal(realized.coords[start], realized.coords[end - 1])


def test_polyhedron_type_index_is_clamped() -> None:
    """type_index の範囲外指定はクランプされる。"""
    tetra = realize(Geometry.create("polyhedron", params={"type_index": 0}))
    under = realize(Geometry.create("polyhedron", params={"type_index": -1}))
    assert under.offsets.shape == tetra.offsets.shape

    icosa = realize(Geometry.create("polyhedron", params={"type_index": 4}))
    over = realize(Geometry.create("polyhedron", params={"type_index": 999}))
    assert over.offsets.shape == icosa.offsets.shape


def test_polyhedron_center_and_scale_affect_coords() -> None:
    """center/scale が座標に反映される。"""
    base = realize(Geometry.create("polyhedron", params={"type_index": 0}))

    center = np.array([10.0, 20.0, 30.0], dtype=np.float32)
    scale = np.array([2.0, 3.0, 4.0], dtype=np.float32)
    moved = realize(
        Geometry.create(
            "polyhedron",
            params={
                "type_index": 0,
                "center": (float(center[0]), float(center[1]), float(center[2])),
                "scale": (float(scale[0]), float(scale[1]), float(scale[2])),
            },
        )
    )

    expected = base.coords * scale + center
    np.testing.assert_allclose(moved.coords, expected, rtol=0.0, atol=1e-6)
