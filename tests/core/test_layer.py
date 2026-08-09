"""Layer モデルとスタイル解決ユーティリティのテスト。"""

from __future__ import annotations

from math import nan

import pytest

from grafix.core.geometry import Geometry
from grafix.core.layer import (
    Layer,
    LayerStyleDefaults,
    ResolvedLayer,
    resolve_layer_style,
)


def _geometry() -> Geometry:
    return Geometry.create("layer-test-geometry", params={"radius": 1.0})


def test_resolve_layer_style_fills_missing_values() -> None:
    layer = Layer(geometry=_geometry(), site_id="layer:1", color=None, thickness=None)
    defaults = LayerStyleDefaults(color=(0.5, 0.5, 0.5), thickness=0.02)

    resolved = resolve_layer_style(layer, defaults)

    assert resolved.color == defaults.color
    assert resolved.thickness == defaults.thickness


def test_layer_gcode_optimize_defaults_true_and_preserves_false() -> None:
    default_layer = Layer(geometry=_geometry(), site_id="layer:default")
    disabled_layer = Layer(
        geometry=_geometry(),
        site_id="layer:disabled",
        gcode_optimize=False,
    )

    assert default_layer.gcode_optimize is True
    assert disabled_layer.gcode_optimize is False


@pytest.mark.parametrize("value", [None, 0, 1, "false", object()])
def test_layer_gcode_optimize_requires_exact_bool(value: object) -> None:
    with pytest.raises(TypeError, match="gcode_optimize"):
        Layer(
            geometry=_geometry(),
            site_id="layer:1",
            gcode_optimize=value,  # type: ignore[arg-type]
        )


def test_resolve_layer_style_rejects_non_positive_thickness() -> None:
    with pytest.raises(ValueError):
        Layer(
            geometry=_geometry(),
            site_id="layer:1",
            color=(1.0, 0.0, 0.0),
            thickness=0.0,
        )


@pytest.mark.parametrize("value", [True, "0.1"])
def test_layer_rejects_non_numeric_thickness(value: object) -> None:
    with pytest.raises(TypeError):
        Layer(
            geometry=_geometry(),
            site_id="layer:1",
            thickness=value,  # type: ignore[arg-type]
        )


def test_layer_rejects_non_finite_thickness() -> None:
    with pytest.raises(ValueError):
        Layer(geometry=_geometry(), site_id="layer:1", thickness=nan)


@pytest.mark.parametrize(
    ("color", "error"),
    [
        ([0.0, 0.0, 0.0], TypeError),
        ((False, 0.0, 0.0), TypeError),
        (("0", 0.0, 0.0), TypeError),
        ((nan, 0.0, 0.0), ValueError),
        ((1.1, 0.0, 0.0), ValueError),
    ],
)
def test_layer_rejects_invalid_rgb01(
    color: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        Layer(
            geometry=_geometry(),
            site_id="layer:1",
            color=color,  # type: ignore[arg-type]
        )


def test_layer_style_defaults_share_layer_style_validation() -> None:
    with pytest.raises(TypeError):
        LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=True)
    with pytest.raises(ValueError):
        LayerStyleDefaults(color=(0.0, 0.0, nan), thickness=0.01)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"geometry": object(), "site_id": "layer:1"}, "geometry"),
        ({"geometry": _geometry(), "site_id": 1}, "site_id"),
        ({"geometry": _geometry(), "site_id": ""}, "site_id"),
        ({"geometry": _geometry(), "site_id": "layer:1", "name": 1}, "name"),
    ],
)
def test_layer_rejects_noncanonical_composition_values(
    kwargs: dict[str, object],
    match: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        Layer(**kwargs)  # type: ignore[arg-type]


def test_resolved_layer_validates_direct_construction() -> None:
    layer = Layer(geometry=_geometry(), site_id="layer:1")
    with pytest.raises(TypeError, match="layer"):
        ResolvedLayer(
            layer=object(),  # type: ignore[arg-type]
            color=(0.0, 0.0, 0.0),
            thickness=0.01,
        )
    with pytest.raises(TypeError, match="RGB01"):
        ResolvedLayer(
            layer=layer,
            color=[0.0, 0.0, 0.0],  # type: ignore[arg-type]
            thickness=0.01,
        )
    with pytest.raises(TypeError, match="thickness"):
        ResolvedLayer(
            layer=layer,
            color=(0.0, 0.0, 0.0),
            thickness="0.01",  # type: ignore[arg-type]
        )


def test_resolve_layer_style_rejects_wrong_dto_types() -> None:
    layer = Layer(geometry=_geometry(), site_id="layer:1")
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    with pytest.raises(TypeError, match="layer"):
        resolve_layer_style(object(), defaults)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="defaults"):
        resolve_layer_style(layer, object())  # type: ignore[arg-type]
