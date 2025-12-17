"""SVG export（`graft.export.svg.export_svg`）のテスト。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import numpy as np
import pytest

from graft.core.geometry import Geometry
from graft.core.layer import Layer
from graft.core.pipeline import RealizedLayer
from graft.core.realized_geometry import RealizedGeometry
from graft.export.svg import export_svg

_SVG_NS = "http://www.w3.org/2000/svg"
_NS = {"svg": _SVG_NS}


def _realized_layer(
    *,
    coords: list[list[float]],
    offsets: list[int],
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    thickness: float = 0.001,
) -> RealizedLayer:
    geometry = Geometry.create("line")
    layer = Layer(geometry=geometry, site_id="layer:1")
    realized = RealizedGeometry(
        coords=np.asarray(coords, dtype=np.float32),
        offsets=np.asarray(offsets, dtype=np.int32),
    )
    return RealizedLayer(layer=layer, realized=realized, color=color, thickness=thickness)


def _parse_svg(text: str) -> ET.Element:
    root = ET.fromstring(text)
    assert root.tag == f"{{{_SVG_NS}}}svg"
    return root


def test_export_svg_writes_valid_svg(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[[0.0, 0.0, 0.0], [10.0, 20.0, 0.0]],
            offsets=[0, 2],
            color=(1.0, 0.0, 0.0),
            thickness=0.001,
        )
    ]
    out_path = tmp_path / "nested" / "out.svg"

    returned = export_svg(layers, out_path, canvas_size=(100, 200))
    assert returned == out_path
    assert out_path.exists()

    root = _parse_svg(out_path.read_text(encoding="utf-8"))
    assert root.attrib["viewBox"] == "0 0 100 200"
    assert root.attrib["width"] == "100"
    assert root.attrib["height"] == "200"

    paths = root.findall("svg:path", _NS)
    assert len(paths) == 1
    path = paths[0]
    assert path.attrib["fill"] == "none"
    assert path.attrib["stroke"] == "#FF0000"
    assert path.attrib["stroke-width"] == "0.050"
    assert path.attrib["stroke-linecap"] == "round"
    assert path.attrib["stroke-linejoin"] == "round"
    assert path.attrib["d"] == "M 0.000 0.000 L 10.000 20.000"


def test_export_svg_outputs_multiple_paths_for_multiple_polylines(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [1.0, 1.0, 0.0],
            ],
            offsets=[0, 2, 4],
        )
    ]
    out_path = tmp_path / "out.svg"
    export_svg(layers, out_path, canvas_size=(10, 10))

    root = _parse_svg(out_path.read_text(encoding="utf-8"))
    paths = root.findall("svg:path", _NS)
    assert len(paths) == 2
    assert paths[0].attrib["d"] == "M 0.000 0.000 L 1.000 0.000"
    assert paths[1].attrib["d"] == "M 0.000 1.000 L 1.000 1.000"


def test_export_svg_skips_polylines_with_less_than_two_points(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
            ],
            offsets=[0, 1, 3],
        )
    ]
    out_path = tmp_path / "out.svg"
    export_svg(layers, out_path, canvas_size=(10, 10))

    root = _parse_svg(out_path.read_text(encoding="utf-8"))
    paths = root.findall("svg:path", _NS)
    assert len(paths) == 1
    assert paths[0].attrib["d"] == "M 1.000 0.000 L 2.000 0.000"


def test_export_svg_is_deterministic(tmp_path) -> None:
    layers = [
        _realized_layer(
            coords=[[0.0, 0.0, 0.0], [10.0, 20.0, 0.0]],
            offsets=[0, 2],
            color=(0.1, 0.2, 0.3),
            thickness=0.002,
        )
    ]

    a = tmp_path / "a.svg"
    b = tmp_path / "b.svg"
    export_svg(layers, a, canvas_size=(100, 100))
    export_svg(layers, b, canvas_size=(100, 100))

    assert a.read_bytes() == b.read_bytes()


def test_export_svg_rejects_canvas_size_none(tmp_path) -> None:
    layers = [_realized_layer(coords=[[0.0, 0.0, 0.0], [1.0, 1.0, 0.0]], offsets=[0, 2])]
    with pytest.raises(ValueError):
        export_svg(layers, tmp_path / "out.svg", canvas_size=None)

