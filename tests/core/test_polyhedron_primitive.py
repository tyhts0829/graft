from __future__ import annotations

from pathlib import Path

import numpy as np

from grafix.api import G
from grafix.core.realize import realize


def test_polyhedron_data_files_exist() -> None:
    import grafix.core.primitives.polyhedron as polyhedron_module

    data_dir = Path(polyhedron_module.__file__).parent / "regular_polyhedron"
    assert data_dir.is_dir()

    expected = [
        "tetrahedron_vertices_list.npz",
        "hexahedron_vertices_list.npz",
        "octahedron_vertices_list.npz",
        "dodecahedron_vertices_list.npz",
        "icosahedron_vertices_list.npz",
    ]
    for name in expected:
        assert (data_dir / name).is_file()


def test_polyhedron_realize_returns_nonempty_geometry() -> None:
    for type_index in range(5):
        realized = realize(G.polyhedron(type_index=type_index))
        assert realized.coords.dtype == np.float32
        assert realized.offsets.dtype == np.int32
        assert realized.coords.shape[0] > 0
        assert realized.offsets.shape[0] > 1
        assert int(realized.offsets[-1]) == int(realized.coords.shape[0])
