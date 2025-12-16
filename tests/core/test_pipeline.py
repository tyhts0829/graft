"""core.pipeline の `realize_scene` をテスト。"""

from __future__ import annotations

import numpy as np

from src.core.geometry import Geometry
from src.core.pipeline import realize_scene
from src.core.layer import Layer, LayerStyleDefaults
from src.core.primitives import circle as _circle_module  # noqa: F401


def test_realize_scene_normalizes_and_realizes_layers() -> None:
    g1 = Geometry.create("circle", params={"r": 1.0})
    g2 = Geometry.create("circle", params={"r": 2.0})

    def draw(t: float):
        return [Layer(g1, site_id="layer:1", color=None, thickness=None), g2]

    defaults = LayerStyleDefaults(color=(0.1, 0.2, 0.3), thickness=0.05)
    realized_layers = realize_scene(draw, t=0.0, defaults=defaults)

    assert len(realized_layers) == 2
    colors = [item.color for item in realized_layers]
    thicknesses = [item.thickness for item in realized_layers]
    assert colors == [(0.1, 0.2, 0.3), (0.1, 0.2, 0.3)]
    assert thicknesses == [0.05, 0.05]
    assert all(isinstance(item.realized.coords, np.ndarray) for item in realized_layers)
