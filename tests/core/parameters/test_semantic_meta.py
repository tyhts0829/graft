from __future__ import annotations

import math
from dataclasses import replace

import pytest

from grafix.core.parameters.codec import (
    dumps_param_store,
    encode_param_store,
    loads_param_store_result,
)
from grafix.core.parameters.context import (
    parameter_context,
    parameter_context_from_snapshot,
)
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.meta_spec import meta_from_spec, meta_to_spec
from grafix.core.parameters.resolver import resolve_params
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.variations import create_variation, list_variations
from grafix.core.parameters.view import rows_from_snapshot


SEMANTIC_META = ParamMeta(
    kind="float",
    ui_min=0.1,
    ui_max=100.0,
    display_name="Stroke width",
    description="描画する線の太さ。",
    unit="mm",
    step=0.1,
    format="%.2f",
    scale="log",
    category="Stroke",
    advanced=True,
    recommended_range=(0.2, 5.0),
)


def _store_with_semantic_meta() -> tuple[ParamStore, ParameterKey]:
    store = ParamStore()
    key = ParameterKey(op="semantic-op", site_id="site-1", arg="width")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=1.5,
                meta=SEMANTIC_META,
                effective=1.5,
                source="code",
                explicit=False,
            )
        ],
    )
    return store, key


def test_meta_spec_roundtrip_preserves_all_semantic_fields() -> None:
    spec = meta_to_spec(SEMANTIC_META)

    assert spec["recommended_range"] == [0.2, 5.0]
    assert spec["choices"] is None
    assert meta_from_spec(spec) == SEMANTIC_META


@pytest.mark.parametrize("kind", ["unknown", "", "FLOAT"])
def test_param_meta_rejects_unknown_kind(kind: str) -> None:
    with pytest.raises(ValueError, match="kind"):
        ParamMeta(kind=kind)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "choices",
    [None, (), ("a", "a"), ("a", 1)],
)
def test_choice_requires_unique_exact_string_choices(choices: object) -> None:
    with pytest.raises((TypeError, ValueError), match="choice"):
        ParamMeta(kind="choice", choices=choices)  # type: ignore[arg-type]


def test_non_choice_rejects_choices() -> None:
    with pytest.raises(ValueError, match="choices"):
        ParamMeta(kind="float", choices=("a",))


@pytest.mark.parametrize(
    ("kind", "ui_min", "ui_max"),
    [
        ("bool", 0, None),
        ("str", None, 1),
        ("choice", 0, 1),
        ("float", True, 1.0),
        ("float", 0.0, float("inf")),
        ("int", 1, 1),
        ("vec3", 2.0, 1.0),
    ],
)
def test_param_meta_rejects_invalid_kind_range(
    kind: str,
    ui_min: object,
    ui_max: object,
) -> None:
    kwargs = (
        {"choices": ("a", "b")}
        if kind == "choice"
        else {}
    )
    with pytest.raises((TypeError, ValueError)):
        ParamMeta(  # type: ignore[arg-type]
            kind=kind,
            ui_min=ui_min,
            ui_max=ui_max,
            **kwargs,
        )


@pytest.mark.parametrize("step", [0.0, -0.1, math.nan, math.inf, -math.inf])
def test_param_meta_rejects_invalid_step_value(step: float) -> None:
    with pytest.raises(ValueError, match="step"):
        ParamMeta(kind="float", step=step)


@pytest.mark.parametrize("step", [True, "0.1", object()])
def test_param_meta_rejects_non_numeric_step(step: object) -> None:
    with pytest.raises(TypeError, match="step"):
        ParamMeta(kind="float", step=step)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "recommended_range",
    [
        (),
        (1.0,),
        (1.0, 2.0, 3.0),
        (1.0, 1.0),
        (2.0, 1.0),
        (math.nan, 1.0),
        (1.0, math.inf),
    ],
)
def test_param_meta_rejects_invalid_recommended_range(
    recommended_range: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError, match="recommended_range"):
        ParamMeta(
            kind="float",
            recommended_range=recommended_range,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("recommended_range", ["0,1", ("low", 1.0), object()])
def test_param_meta_rejects_non_numeric_recommended_range(
    recommended_range: object,
) -> None:
    with pytest.raises(TypeError, match="recommended_range"):
        ParamMeta(
            kind="float",
            recommended_range=recommended_range,  # type: ignore[arg-type]
        )


def test_param_meta_rejects_invalid_scale() -> None:
    with pytest.raises(ValueError, match="scale"):
        ParamMeta(kind="float", scale="sqrt")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="scale"):
        ParamMeta(kind="float", scale=1)  # type: ignore[arg-type]


def test_codec_and_variation_roundtrip_preserve_semantic_meta() -> None:
    store, key = _store_with_semantic_meta()
    create_variation(store, "semantic", created_at=10.0)

    encoded = encode_param_store(store)
    encoded_meta = encoded["meta"][0]
    assert encoded_meta["display_name"] == "Stroke width"
    assert encoded_meta["scale"] == "log"
    assert encoded_meta["recommended_range"] == [0.2, 5.0]

    loaded = loads_param_store_result(dumps_param_store(store)).store

    assert loaded.get_meta(key) == SEMANTIC_META
    variations = list_variations(loaded)
    assert len(variations) == 1
    adjustment = variations[0].parameter_snapshot.get(key)
    assert adjustment is not None
    assert adjustment.meta == SEMANTIC_META


def test_adjustment_snapshot_restores_only_gui_range_and_keeps_current_semantic_meta() -> None:
    store, key = _store_with_semantic_meta()
    snapshot = store.capture_adjustment_snapshot()
    current_meta = ParamMeta(
        kind="float",
        ui_min=1.0,
        ui_max=1000.0,
        display_name="Current width",
        description="現在のコードが定義した説明。",
        unit="cm",
        step=1.0,
        format="%.1f",
        scale="log",
        category="Current",
        advanced=False,
        recommended_range=(2.0, 20.0),
    )
    store._set_meta(key, current_meta)

    assert store.apply_adjustment_snapshot(snapshot) is True
    assert store.get_meta(key) == ParamMeta(
        kind="float",
        ui_min=SEMANTIC_META.ui_min,
        ui_max=SEMANTIC_META.ui_max,
        display_name=current_meta.display_name,
        description=current_meta.description,
        unit=current_meta.unit,
        step=current_meta.step,
        format=current_meta.format,
        scale=current_meta.scale,
        category=current_meta.category,
        advanced=current_meta.advanced,
        recommended_range=current_meta.recommended_range,
    )


def test_rows_from_snapshot_carries_semantic_meta() -> None:
    store, _key = _store_with_semantic_meta()

    [row] = rows_from_snapshot(store_snapshot(store))

    assert row.display_name == SEMANTIC_META.display_name
    assert row.description == SEMANTIC_META.description
    assert row.unit == SEMANTIC_META.unit
    assert row.step == SEMANTIC_META.step
    assert row.format == SEMANTIC_META.format
    assert row.scale == SEMANTIC_META.scale
    assert row.category == SEMANTIC_META.category
    assert row.advanced is True
    assert row.recommended_range == SEMANTIC_META.recommended_range


def test_merge_refreshes_code_owned_meta_and_preserves_stored_gui_range() -> None:
    store = ParamStore()
    key = ParameterKey(op="semantic-op", site_id="site-1", arg="width")
    stale_meta = replace(
        SEMANTIC_META,
        ui_min=-10.0,
        ui_max=10.0,
        display_name="Stale width",
        description=None,
        unit="px",
        step=2.0,
        format="%.0f",
        scale="linear",
        category="Stale",
        advanced=False,
        recommended_range=(1.0, 2.0),
    )
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=1.5,
                meta=stale_meta,
                effective=1.5,
                source="code",
                explicit=False,
            )
        ],
    )

    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=1.5,
                meta=SEMANTIC_META,
                effective=1.5,
                source="code",
                explicit=False,
            )
        ],
    )

    expected = replace(SEMANTIC_META, ui_min=-10.0, ui_max=10.0)
    assert store.get_meta(key) == expected

    revision = store.revision
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=1.5,
                meta=SEMANTIC_META,
                effective=1.5,
                source="code",
                explicit=False,
            )
        ],
    )
    assert store.get_meta(key) == expected
    assert store.revision == revision


def test_resolver_refreshes_current_choices_without_losing_gui_range_or_value() -> None:
    store = ParamStore()
    stale_meta = ParamMeta(
        kind="choice",
        choices=("circle",),
        description="古い候補。",
    )
    current_meta = ParamMeta(
        kind="choice",
        choices=("circle", "rect"),
        description="現在登録されている候補。",
    )
    with parameter_context(store):
        resolve_params(
            op="selector",
            params={"target": "circle"},
            meta={"target": stale_meta},
            site_id="site-1",
            explicit_args=set(),
        )

    with parameter_context(store):
        resolved = resolve_params(
            op="selector",
            params={"target": "circle"},
            meta={"target": current_meta},
            site_id="site-1",
            explicit_args=set(),
        )

    key = ParameterKey(op="selector", site_id="site-1", arg="target")
    assert resolved == {"target": "circle"}
    assert store.get_state(key).ui_value == "circle"  # type: ignore[union-attr]
    assert store.get_meta(key) == current_meta


def test_kind_change_uses_current_code_owned_range() -> None:
    store = ParamStore()
    stale_meta = ParamMeta(kind="float", ui_min=-10.0, ui_max=10.0)
    current_meta = ParamMeta(kind="int", ui_min=0, ui_max=100)

    with parameter_context(store):
        resolve_params(
            op="semantic-op",
            params={"value": 1.5},
            meta={"value": stale_meta},
            site_id="site-1",
            explicit_args={"value"},
        )

    with parameter_context(store):
        resolved = resolve_params(
            op="semantic-op",
            params={"value": 2},
            meta={"value": current_meta},
            site_id="site-1",
            explicit_args={"value"},
        )

    key = ParameterKey(op="semantic-op", site_id="site-1", arg="value")
    assert resolved == {"value": 2}
    assert store.get_meta(key) == current_meta


@pytest.mark.parametrize(
    ("old_value", "old_meta", "current_base", "current_meta"),
    [
        (
            1.5,
            ParamMeta(kind="float"),
            (1.0, 2.0, 3.0),
            ParamMeta(kind="vec3"),
        ),
        (
            (1.0, 2.0, 3.0),
            ParamMeta(kind="vec3"),
            4.5,
            ParamMeta(kind="float"),
        ),
        (
            (0.0, 0.0, 0.0),
            ParamMeta(kind="vec3"),
            False,
            ParamMeta(kind="bool"),
        ),
    ],
)
def test_incompatible_kind_change_falls_back_to_current_code_value(
    old_value: object,
    old_meta: ParamMeta,
    current_base: object,
    current_meta: ParamMeta,
) -> None:
    store = ParamStore()
    key = ParameterKey(op="semantic-op", site_id="site-1", arg="value")

    # 省略引数として観測し、旧 kind の GUI override が有効な状態を作る。
    with parameter_context(store):
        resolve_params(
            op=key.op,
            params={key.arg: old_value},
            meta={key.arg: old_meta},
            site_id=key.site_id,
            explicit_args=set(),
        )
    old_state = store.get_state(key)
    assert old_state is not None
    assert old_state.override is True

    with parameter_context(store):
        resolved = resolve_params(
            op=key.op,
            params={key.arg: current_base},
            meta={key.arg: current_meta},
            site_id=key.site_id,
            explicit_args=set(),
        )

    # 旧 UI 値を新 kind に変換できない場合、ゼロ値ではなく現在の code 値を使う。
    assert resolved == {key.arg: current_base}
    current_state = store.get_state(key)
    assert current_state is not None
    assert current_state.ui_value == current_base
    assert current_state.override is True
    assert store.get_meta(key) == current_meta


def test_loaded_choices_follow_current_code_without_losing_saved_selection() -> None:
    store = ParamStore()
    key = ParameterKey(op="selector", site_id="site-1", arg="target")
    stale_meta = ParamMeta(kind="choice", choices=("circle",))
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base="circle",
                meta=stale_meta,
                effective="circle",
                source="code",
                explicit=False,
            )
        ],
    )
    loaded = loads_param_store_result(dumps_param_store(store)).store
    current_meta = ParamMeta(kind="choice", choices=("circle", "rect"))

    with parameter_context(loaded):
        resolved = resolve_params(
            op="selector",
            params={"target": "circle"},
            meta={"target": current_meta},
            site_id="site-1",
            explicit_args=set(),
        )

    assert resolved == {"target": "circle"}
    assert loaded.get_state(key).ui_value == "circle"  # type: ignore[union-attr]
    assert loaded.get_meta(key) == current_meta


def test_worker_snapshot_records_current_code_owned_meta() -> None:
    store = ParamStore()
    key = ParameterKey(op="semantic-op", site_id="site-1", arg="width")
    stale_meta = replace(
        SEMANTIC_META,
        ui_min=-10.0,
        ui_max=10.0,
        display_name="Stale width",
        description=None,
    )
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=1.5,
                meta=stale_meta,
                effective=1.5,
                source="code",
                explicit=False,
            )
        ],
    )

    with parameter_context_from_snapshot(store_snapshot(store)) as frame_params:
        resolve_params(
            op="semantic-op",
            params={"width": 1.5},
            meta={"width": SEMANTIC_META},
            site_id="site-1",
            explicit_args=set(),
        )

    [record] = frame_params.records
    assert record.meta == replace(
        SEMANTIC_META,
        ui_min=-10.0,
        ui_max=10.0,
    )
