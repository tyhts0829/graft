"""effect の bypass パラメータのテスト。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.effect_registry import effect_registry
from grafix.core.geometry import Geometry
from grafix.core.realize import realize
from grafix.core.realized_geometry import concat_realized_geometries


def test_effect_bypass_returns_input_geometry_unchanged() -> None:
    g = G.polygon(n_sides=6)

    base = realize(g)
    applied = realize(E.scale(scale=(2.0, 3.0, 4.0))(g))
    bypassed = realize(E.scale(bypass=True, scale=(2.0, 3.0, 4.0))(g))

    assert not np.array_equal(applied.coords, base.coords)
    assert np.array_equal(bypassed.coords, base.coords)
    assert np.array_equal(bypassed.offsets, base.offsets)


def test_effect_bypass_multiple_inputs_pass_through_by_concat() -> None:
    g1 = G.polygon(n_sides=3)
    g2 = G.polygon(n_sides=4, center=(10.0, 0.0, 0.0))

    node = Geometry.create("scale", inputs=(g1, g2), params={"bypass": True})
    out = realize(node)
    expected = concat_realized_geometries(realize(g1), realize(g2))

    assert np.array_equal(out.coords, expected.coords)
    assert np.array_equal(out.offsets, expected.offsets)


def test_effect_bypass_empty_inputs_returns_empty_geometry() -> None:
    func = effect_registry.get("scale")
    out = func([], (("bypass", True),))

    assert out.coords.shape == (0, 3)
    assert out.offsets.shape == (1,)
    assert out.offsets.tolist() == [0]

