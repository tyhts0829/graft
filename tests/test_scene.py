"""シーン正規化ヘルパのテスト。"""

from __future__ import annotations

import pytest

from src.core.geometry import Geometry
from src.render.layer import Layer
from src.render.scene import normalize_scene


def _g(name: str = "circle") -> Geometry:
    return Geometry.create(name, params={"r": 1.0})


def test_normalize_scene_wraps_geometry() -> None:
    g = _g()
    layers = normalize_scene(g)
    assert len(layers) == 1
    assert isinstance(layers[0], Layer)
    assert layers[0].geometry is g


def test_normalize_scene_flattens_nested_sequences() -> None:
    g1, g2 = _g("circle"), _g("circle")
    l = Layer(geometry=_g("circle"), thickness=0.01)
    layers = normalize_scene([g1, [l, g2]])
    assert [layer.geometry for layer in layers] == [g1, l.geometry, g2]


def test_normalize_scene_raises_on_invalid_type() -> None:
    with pytest.raises(TypeError):
        normalize_scene(123)  # type: ignore[arg-type]
