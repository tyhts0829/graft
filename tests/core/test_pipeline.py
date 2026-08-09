"""core.pipeline の `realize_scene` をテスト。"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from grafix.api import G, P
from grafix.core.evaluation_context import EvaluationResources
from grafix.core.geometry import Geometry
from grafix.core.layer import Layer, LayerStyleDefaults
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters import ParamStore, parameter_context
from grafix.core.parameters.context import parameter_context_from_snapshot
from grafix.core.parameters.layer_style import (
    LAYER_STYLE_LINE_COLOR,
    LAYER_STYLE_LINE_THICKNESS,
    LAYER_STYLE_OP,
    layer_style_key,
)
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.pipeline import realize_scene
from grafix.core.preset_catalog import (
    PresetCatalogBuilder,
    PresetDeclaration,
)
from grafix.core.realize import RealizeCacheStore, RealizeSession
from grafix.runtime_config_loader import runtime_config
from tests.param_store_test_support import runtime_state


_TEST_RUNTIME_CONFIG = replace(runtime_config(), font_dirs=())


def test_realize_scene_normalizes_and_realizes_layers() -> None:
    g1 = G.polygon(n_sides=3)
    g2 = G.polygon(n_sides=6)

    def draw(t: float):
        return [
            Layer(
                g1,
                site_id="layer:1",
                color=None,
                thickness=None,
                gcode_optimize=False,
            ),
            g2,
        ]

    defaults = LayerStyleDefaults(color=(0.1, 0.2, 0.3), thickness=0.05)
    realized_layers = realize_scene(draw, t=0.0, defaults=defaults, config=_TEST_RUNTIME_CONFIG)

    assert len(realized_layers) == 2
    colors = [item.color for item in realized_layers]
    thicknesses = [item.thickness for item in realized_layers]
    assert colors == [(0.1, 0.2, 0.3), (0.1, 0.2, 0.3)]
    assert thicknesses == [0.05, 0.05]
    assert all(isinstance(item.realized.coords, np.ndarray) for item in realized_layers)
    assert [item.cache_key.geometry_id for item in realized_layers] == [g1.id, g2.id]
    assert realized_layers[0].cache_key.evaluation == realized_layers[1].cache_key.evaluation
    assert [item.layer.gcode_optimize for item in realized_layers] == [False, True]


def test_realize_scene_reuses_explicit_session_between_frames() -> None:
    geometry = G.polygon(n_sides=5)

    def draw(_t: float) -> Geometry:
        return geometry

    defaults = LayerStyleDefaults(color=(0.1, 0.2, 0.3), thickness=0.05)
    with RealizeSession() as session:
        first = realize_scene(
            draw, t=0.0, defaults=defaults, session=session, config=_TEST_RUNTIME_CONFIG
        )
        second = realize_scene(
            draw, t=1.0, defaults=defaults, session=session, config=_TEST_RUNTIME_CONFIG
        )

    assert second[0].realized is first[0].realized
    assert second[0].cache_key == first[0].cache_key


def test_layer_gcode_optimization_master_does_not_change_geometry_cache_identity() -> None:
    geometry = G.polygon(n_sides=5)

    def draw(_t: float):
        return (
            Layer(geometry, site_id="layer:enabled", gcode_optimize=True),
            Layer(geometry, site_id="layer:disabled", gcode_optimize=False),
        )

    realized_layers = realize_scene(
        draw,
        t=0.0,
        defaults=LayerStyleDefaults(color=(0.1, 0.2, 0.3), thickness=0.05),
        config=_TEST_RUNTIME_CONFIG,
    )

    assert realized_layers[0].cache_key == realized_layers[1].cache_key
    assert realized_layers[0].realized is realized_layers[1].realized


def test_realize_scene_standalone_preserves_body_error_while_closing_owned_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body_error = RuntimeError("draw failed")
    resource_close_error = OSError("resources failed")
    store_close_error = KeyboardInterrupt("store failed")
    calls: list[str] = []
    original_resources_close = EvaluationResources.close
    original_store_close = RealizeCacheStore.close

    def close_resources(resources: EvaluationResources) -> None:
        calls.append("resources")
        original_resources_close(resources)
        raise resource_close_error

    def close_store(store: RealizeCacheStore) -> None:
        calls.append("store")
        original_store_close(store)
        raise store_close_error

    def draw(_t: float) -> Geometry:
        raise body_error

    monkeypatch.setattr(EvaluationResources, "close", close_resources)
    monkeypatch.setattr(RealizeCacheStore, "close", close_store)

    with pytest.raises(RuntimeError) as exc_info:
        realize_scene(
            draw,
            t=0.0,
            defaults=LayerStyleDefaults(
                color=(0.0, 0.0, 0.0),
                thickness=0.01,
            ),
            config=_TEST_RUNTIME_CONFIG,
        )

    assert exc_info.value is body_error
    assert calls == ["resources", "store"]
    assert body_error.__notes__ == [
        "Secondary cleanup failure (close realize session): "
        "OSError: resources failed",
        "Secondary cleanup failure (close owned realize cache store): "
        "KeyboardInterrupt: store failed",
    ]


def test_realize_scene_binds_explicit_preset_snapshot() -> None:
    calls: list[str] = []

    def local_preset() -> Geometry:
        calls.append("called")
        return Geometry.create(op="concat")

    builder = PresetCatalogBuilder()
    builder.register(
        PresetDeclaration(
            name="pipeline_local_probe",
            func=local_preset,
            invoker=lambda *_args, **_kwargs: local_preset(),
            schema=ParameterOpSchema(
                meta={},
                defaults={},
                param_order=(),
                ui_visible={},
            ),
        )
    )
    presets = builder.freeze()
    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)

    with RealizeSession() as session:
        layers = realize_scene(
            lambda _t: P.pipeline_local_probe(),
            t=0.0,
            defaults=defaults,
            session=session,
            presets=presets,
            config=_TEST_RUNTIME_CONFIG,
        )

    assert calls == ["called"]
    assert len(layers) == 1


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"layer": object()}, TypeError),
        ({"realized": object()}, TypeError),
        ({"cache_key": []}, TypeError),
        ({"cache_key": ("other", (0, 0))}, TypeError),
        ({"cache_key": ("placeholder", (True, 0))}, TypeError),
        ({"color": [0.0, 0.0, 0.0]}, TypeError),
        ({"thickness": "0.1"}, TypeError),
    ],
)
def test_realized_layer_validates_direct_construction(
    changes: dict[str, object],
    error: type[Exception],
) -> None:
    geometry = G.polygon(n_sides=3)
    valid = realize_scene(
        lambda _t: geometry,
        t=0.0,
        defaults=LayerStyleDefaults(
            color=(0.0, 0.0, 0.0),
            thickness=0.01,
        ),
        config=_TEST_RUNTIME_CONFIG,
    )[0]
    with pytest.raises(error):
        replace(valid, **changes)


def test_realize_scene_observes_and_applies_layer_style_overrides() -> None:
    g = G.polygon(n_sides=3)

    def draw(t: float):
        return [Layer(g, site_id="layer:1", color=None, thickness=None, name="bg")]

    defaults = LayerStyleDefaults(color=(0.1, 0.2, 0.3), thickness=0.05)
    store = ParamStore()

    with parameter_context(store=store, cc_snapshot=None):
        _ = realize_scene(draw, t=0.0, defaults=defaults, config=_TEST_RUNTIME_CONFIG)

    assert store.get_label(LAYER_STYLE_OP, "layer:1") == "bg"

    key_thickness = layer_style_key("layer:1", LAYER_STYLE_LINE_THICKNESS)
    key_color = layer_style_key("layer:1", LAYER_STYLE_LINE_COLOR)

    meta_thickness = store.get_meta(key_thickness)
    meta_color = store.get_meta(key_color)
    assert meta_thickness is not None
    assert meta_color is not None

    ok, err = update_state_from_ui(store, key_thickness, 0.123, meta=meta_thickness, override=True)
    assert ok and err is None
    ok, err = update_state_from_ui(store, key_color, (255, 0, 0), meta=meta_color, override=True)
    assert ok and err is None

    with parameter_context(store=store, cc_snapshot=None):
        realized_layers = realize_scene(draw, t=0.0, defaults=defaults, config=_TEST_RUNTIME_CONFIG)

    assert realized_layers[0].thickness == 0.123
    assert realized_layers[0].color == (1.0, 0.0, 0.0)
    runtime = runtime_state(store)
    assert runtime.last_effective_by_key[key_thickness] == 0.123
    assert runtime.last_source_by_key[key_thickness] == "ui"
    assert runtime.last_effective_by_key[key_color] == (255, 0, 0)
    assert runtime.last_source_by_key[key_color] == "ui"


def test_realize_scene_records_layer_style_without_param_store() -> None:
    g = G.polygon(n_sides=3)

    def draw(t: float):
        return [Layer(g, site_id="layer:1", color=None, thickness=None, name="bg")]

    defaults = LayerStyleDefaults(color=(0.1, 0.2, 0.3), thickness=0.05)

    with parameter_context_from_snapshot(snapshot={}, cc_snapshot=None) as frame_params:
        realized_layers = realize_scene(draw, t=0.0, defaults=defaults, config=_TEST_RUNTIME_CONFIG)

    assert realized_layers[0].thickness == 0.05
    assert realized_layers[0].color == (0.1, 0.2, 0.3)

    records_by_key = {record.key: record for record in frame_params.records}
    thickness_record = records_by_key[layer_style_key("layer:1", LAYER_STYLE_LINE_THICKNESS)]
    color_record = records_by_key[layer_style_key("layer:1", LAYER_STYLE_LINE_COLOR)]
    assert thickness_record.effective == 0.05
    assert thickness_record.source == "code"
    assert color_record.effective == (26, 51, 76)
    assert color_record.source == "code"
    assert any(
        (rec.op, rec.site_id, rec.label) == (LAYER_STYLE_OP, "layer:1", "bg")
        for rec in frame_params.labels
    )
