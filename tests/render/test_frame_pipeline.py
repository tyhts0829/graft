"""frame_pipeline の描画シーケンスをテスト（Renderer スタブ使用）。"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.core.geometry import Geometry
from src.render.frame_pipeline import render_scene
from src.render.layer import Layer, LayerStyleDefaults
from src.primitives import circle as _circle_module  # noqa: F401


@dataclass
class StubRenderer:
    calls: list[tuple] = field(default_factory=list)

    def render_layer(self, realized, indices, *, color, thickness) -> None:
        self.calls.append((realized, indices, color, thickness))


def test_render_scene_normalizes_and_renders_layers() -> None:
    g1 = Geometry.create("circle", params={"r": 1.0})
    g2 = Geometry.create("circle", params={"r": 2.0})

    def draw(t: float):
        return [Layer(g1, site_id="layer:1", color=None, thickness=None), g2]

    defaults = LayerStyleDefaults(color=(0.1, 0.2, 0.3), thickness=0.05)
    renderer = StubRenderer()

    render_scene(draw, t=0.0, defaults=defaults, renderer=renderer)

    assert len(renderer.calls) == 2
    colors = [call[2] for call in renderer.calls]
    thicknesses = [call[3] for call in renderer.calls]
    assert colors == [(0.1, 0.2, 0.3), (0.1, 0.2, 0.3)]
    assert thicknesses == [0.05, 0.05]
    assert all(isinstance(call[1], np.ndarray) for call in renderer.calls)
