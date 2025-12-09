"""LayerHelper (L) の挙動テスト。"""

from __future__ import annotations

import pytest

from src.api import L
from src.core.geometry import Geometry


def _g(name: str = "circle") -> Geometry:
    return Geometry.create(name, params={"r": 1.0})


def test_L_returns_list_for_single_geometry() -> None:
    layers = L(_g())
    assert len(layers) == 1
    assert layers[0].geometry.op == "circle"


def test_L_applies_common_style_to_multiple_geometries() -> None:
    g1, g2 = _g("circle"), _g("circle")
    layers = L([g1, g2], color=(1.0, 0.0, 0.0), thickness=0.02, name="foo")
    assert len(layers) == 2
    assert all(layer.color == (1.0, 0.0, 0.0) for layer in layers)
    assert all(layer.thickness == 0.02 for layer in layers)
    assert all(layer.name == "foo" for layer in layers)


def test_L_rejects_non_geometry_inputs() -> None:
    with pytest.raises(TypeError):
        L([_g(), 123])


def test_L_rejects_non_positive_thickness() -> None:
    with pytest.raises(ValueError):
        L(_g(), thickness=0.0)
