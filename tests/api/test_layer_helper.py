"""LayerNamespace (L) の挙動テスト。"""

from __future__ import annotations

import pytest

from grafix.api import L
from grafix.core.geometry import Geometry


def _g(name: str = "circle") -> Geometry:
    return Geometry.create(name, params={"r": 1.0})


def test_L_returns_layer_for_single_geometry() -> None:
    layer = L.layer(_g())
    assert layer.geometry.op == "circle"
    assert layer.gcode_optimize is True


def test_L_passes_gcode_optimize_to_layer() -> None:
    layer = L.layer(_g(), gcode_optimize=False)

    assert layer.gcode_optimize is False


@pytest.mark.parametrize("value", [None, 0, 1, "false", object()])
def test_L_rejects_non_bool_gcode_optimize(value: object) -> None:
    with pytest.raises(TypeError, match="gcode_optimize"):
        L.layer(_g(), gcode_optimize=value)  # type: ignore[arg-type]


def test_L_applies_common_style_to_multiple_geometries() -> None:
    g1, g2 = _g("circle"), _g("circle")
    layer = L(name="foo").layer(
        [g1, g2],
        color=(1.0, 0.0, 0.0),
        thickness=0.02,
        gcode_optimize=False,
    )
    assert layer.color == (1.0, 0.0, 0.0)
    assert layer.thickness == 0.02
    assert layer.name == "foo"
    assert layer.gcode_optimize is False
    assert layer.geometry.op == "concat"
    # concat inputs should be preserved
    assert len(layer.geometry.inputs) == 2


def test_L_rejects_non_geometry_inputs() -> None:
    with pytest.raises(TypeError):
        L.layer([_g(), 123])


def test_L_rejects_non_positive_thickness() -> None:
    with pytest.raises(ValueError):
        L.layer(_g(), thickness=0.0)


def test_L_rejects_empty_list() -> None:
    with pytest.raises(ValueError):
        L.layer([])


def test_L_rejects_builder_style_kwargs() -> None:
    with pytest.raises(TypeError):
        L(color=(1.0, 0.0, 0.0))  # type: ignore[call-arg]


def test_L_rejects_layer_name_kwarg() -> None:
    with pytest.raises(TypeError):
        L(name="foo").layer(_g(), name="bar")  # type: ignore[call-arg]
