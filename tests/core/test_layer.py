"""Layer モデルとスタイル解決ユーティリティのテスト。"""

from __future__ import annotations

import pytest

from src.core.geometry import Geometry
from src.core.layer import Layer, LayerStyleDefaults, resolve_layer_style


def _geometry() -> Geometry:
    return Geometry.create("circle", params={"r": 1.0})


def test_resolve_layer_style_fills_missing_values() -> None:
    layer = Layer(geometry=_geometry(), site_id="layer:1", color=None, thickness=None)
    defaults = LayerStyleDefaults(color=(0.5, 0.5, 0.5), thickness=0.02)

    resolved = resolve_layer_style(layer, defaults)

    assert resolved.color == defaults.color
    assert resolved.thickness == defaults.thickness


def test_resolve_layer_style_rejects_non_positive_thickness() -> None:
    layer = Layer(
        geometry=_geometry(),
        site_id="layer:1",
        color=(1.0, 0.0, 0.0),
        thickness=0.0,
    )
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)

    with pytest.raises(ValueError):
        resolve_layer_style(layer, defaults)
