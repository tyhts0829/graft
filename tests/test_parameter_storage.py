import json
import logging
import os
from pathlib import Path

import pytest

import grafix.parameter_storage as storage_module
from grafix.core.parameters import (
    KnownOperationSchemaSnapshot,
    ParamMeta,
    ParamStore,
    ParameterKey,
)
from grafix.core.parameters.codec import (
    PARAM_STORE_SCHEMA_VERSION,
    ParamStoreSchemaError,
    UnsupportedParamStoreSchemaError,
    dumps_param_store,
    loads_param_store_result,
)
from grafix.core.parameters.context import current_frame_params, parameter_context
from grafix.core.parameters.codec_parser import parse_param_store_payload
from grafix.core.parameters.effects import EffectStepTopology
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.parameter_storage import (
    finalize_parameter_session,
    ParamStoreLoadResult,
    param_store_recovery_path,
    read_param_store,
    recover_param_store_session,
    recover_primary_param_store,
    write_param_store,
    write_param_store_recovery,
)
from grafix.export.output_paths import default_param_store_path
from grafix.runtime_config_loader import load_runtime_config
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.variations import (
    create_variation,
    is_parameter_locked,
    list_variations,
    restore_variation,
    set_parameters_locked,
)


def _isolate_config_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))


def _read_store(path: Path) -> ParamStore:
    return read_param_store(path).store


def _make_draw_with_filename(filename: Path):
    code = compile("def draw(t: float):\n    return None\n", str(filename), "exec")
    ns: dict[str, object] = {}
    exec(code, ns)
    return ns["draw"]


def _merge_float_group(
    store: ParamStore,
    *,
    op: str,
    site_id: str,
) -> ParameterKey:
    key = ParameterKey(op=op, site_id=site_id, arg="amount")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.5,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.5,
                source="code",
                explicit=False,
            )
        ],
    )
    return key


def _store_with_float_value(
    value: float,
    *,
    explicit: bool = False,
) -> tuple[ParamStore, ParameterKey]:
    store = ParamStore()
    key = ParameterKey(op="variant", site_id="site", arg="amount")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.1,
                meta=meta,
                effective=0.1,
                source="code",
                explicit=explicit,
            )
        ],
    )
    ok, error = update_state_from_ui(
        store,
        key,
        value,
        meta=meta,
        override=True,
    )
    assert ok and error is None
    return store, key


def test_parsed_param_store_is_deeply_read_only() -> None:
    store, key = _store_with_float_value(0.6)
    parsed = parse_param_store_payload(json.loads(dumps_param_store(store)))

    with pytest.raises(TypeError):
        parsed.states[key] = parsed.states[key]  # type: ignore[index]
    with pytest.raises(TypeError):
        parsed.labels[("line", "site")] = "Line"  # type: ignore[index]
    with pytest.raises(TypeError):
        parsed.ordinals[key.op][key.site_id] = 99  # type: ignore[index]


def _store_with_effect_order() -> ParamStore:
    store = ParamStore()
    assert store._effects_ref().record_chain(
        chain_id="chain-order",
        steps=(
            EffectStepTopology("scale", "scale-site", 1, 0),
            EffectStepTopology("rotate", "rotate-site", 1, 1),
        ),
    )
    assert store._effects_ref().set_order_override(
        "chain-order",
        (("rotate", "rotate-site"), ("scale", "scale-site")),
    )
    store._touch()
    return store


def _store_with_parameter_and_effect_chain() -> tuple[ParamStore, ParameterKey]:
    store, key = _store_with_float_value(0.6)
    assert store._effects_ref().record_chain(
        chain_id="strict-chain",
        steps=(EffectStepTopology("scale", "effect-site", 1, 0),),
    )
    return store, key


def _set_mtime(path: Path, value_ns: int) -> None:
    os.utime(path, ns=(int(value_ns), int(value_ns)))


def test_default_param_store_path_uses_data_dir_and_script_stem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _isolate_config_discovery(tmp_path, monkeypatch)

    def draw(t: float) -> None:
        return None

    path = default_param_store_path(draw, config=load_runtime_config())
    assert path.parts[0] == "data"
    assert path.parts[1] == "output"
    assert path.parts[2] == "param_store"
    assert path.parts[3] == "misc"
    assert path.name == f"{Path(__file__).stem}.json"
    assert path.suffix == ".json"


def test_default_param_store_path_mirrors_sketch_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _isolate_config_discovery(tmp_path, monkeypatch)

    discovered = tmp_path / ".grafix" / "config.yaml"
    discovered.parent.mkdir(parents=True, exist_ok=True)
    discovered.write_text(
        'paths:\n  output_dir: "./out"\n  sketch_dir: "../sketch"\n',
        encoding="utf-8",
    )

    output_root = discovered.parent / "out"
    config = load_runtime_config()

    draw_in_root = _make_draw_with_filename(tmp_path / "sketch" / "readme.py")
    assert default_param_store_path(draw_in_root, config=config) == (
        output_root / "param_store" / "readme.json"
    )
    assert default_param_store_path(draw_in_root, run_id="v1", config=config) == (
        output_root / "param_store" / "readme_v1.json"
    )

    draw_in_subdir = _make_draw_with_filename(tmp_path / "sketch" / "folder1" / "readme.py")
    assert default_param_store_path(draw_in_subdir, config=config) == (
        output_root / "param_store" / "folder1" / "readme.json"
    )
    assert default_param_store_path(
        draw_in_subdir,
        run_id="v1",
        config=config,
    ) == (
        output_root / "param_store" / "folder1" / "readme_v1.json"
    )

    draw_outside = _make_draw_with_filename(tmp_path / "outside.py")
    assert default_param_store_path(draw_outside, config=config) == (
        output_root / "param_store" / "misc" / "outside.json"
    )


def test_param_store_file_roundtrip(tmp_path: Path):
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-1", arg="radius")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.5,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.5,
                source="code",
                explicit=False,
            )
        ],
    )

    path = tmp_path / "dummy.json"
    write_param_store(store, path)
    bytes_before = path.read_bytes()
    mtime_before = path.stat().st_mtime_ns
    entries_before = tuple(sorted(item.name for item in tmp_path.iterdir()))
    read_result = read_param_store(path)
    loaded = read_result.store

    snap = store_snapshot(loaded)
    meta, state, ordinal, _label = snap[key]
    assert meta.kind == "float"
    assert meta.ui_min == 0.0
    assert meta.ui_max == 1.0
    assert state.override is True
    assert state.ui_value == 0.5
    assert ordinal == 1
    assert read_result.status == "loaded"
    assert read_result.load_state.provenance == "primary"
    assert_invariants(loaded)
    assert path.read_bytes() == bytes_before
    assert path.stat().st_mtime_ns == mtime_before
    assert tuple(sorted(item.name for item in tmp_path.iterdir())) == entries_before


def test_read_missing_param_store_is_non_mutating(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    entries_before = tuple(tmp_path.iterdir())

    result = read_param_store(path)

    assert result.status == "missing"
    assert store_snapshot(result.store) == {}
    assert tuple(tmp_path.iterdir()) == entries_before


def test_explicit_bool_uses_code_after_normal_load_and_ui_after_recovery() -> None:
    store = ParamStore()
    key = ParameterKey(op="switch", site_id="bool-site", arg="enabled")
    meta = ParamMeta(kind="bool")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=True,
                meta=meta,
                effective=True,
                source="code",
                explicit=True,
            )
        ],
    )
    ok, error = update_state_from_ui(
        store,
        key,
        False,
        meta=meta,
        override=True,
    )
    assert ok and error is None

    normal = loads_param_store_result(dumps_param_store(store)).store
    normal_state = normal.get_state(key)
    assert normal_state is not None
    assert normal_state.ui_value is False
    assert normal_state.override is False

    recovery = loads_param_store_result(
        dumps_param_store(store, preserve_explicit_overrides=True),
        preserve_explicit_overrides=True,
    ).store
    recovery_state = recovery.get_state(key)
    assert recovery_state is not None
    assert recovery_state.ui_value is False
    assert recovery_state.override is True


def test_saved_param_store_declares_current_schema_version() -> None:
    payload = json.loads(dumps_param_store(ParamStore()))

    assert payload["schema_version"] == PARAM_STORE_SCHEMA_VERSION


def test_current_schema_rejects_missing_top_level_section() -> None:
    payload = json.loads(dumps_param_store(ParamStore()))
    payload.pop("states")

    with pytest.raises(ParamStoreSchemaError, match=r"missing=.*'states'"):
        loads_param_store_result(json.dumps(payload))


def test_current_schema_rejects_unknown_top_level_section() -> None:
    payload = json.loads(dumps_param_store(ParamStore()))
    payload["legacy_states"] = []

    with pytest.raises(ParamStoreSchemaError, match=r"unknown=.*'legacy_states'"):
        loads_param_store_result(json.dumps(payload))


def test_current_ui_schema_reports_missing_and_unknown_fields() -> None:
    payload = json.loads(dumps_param_store(ParamStore()))
    payload["ui"].pop("favorite_parameters")
    payload["ui"]["legacy_favorites"] = []

    result = loads_param_store_result(json.dumps(payload))

    assert any(
        issue.section == "ui"
        and "missing fields: favorite_parameters" in issue.reason
        and "unknown fields: legacy_favorites" in issue.reason
        for issue in result.issues
    )


def test_current_state_entry_with_unknown_field_is_dropped() -> None:
    store, key = _store_with_float_value(0.6)
    payload = json.loads(dumps_param_store(store))
    payload["states"][0]["legacy_value"] = 0.6

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.get_state(key) is None
    assert any(
        issue.section == "states"
        and issue.index == 0
        and "unknown fields: legacy_value" in issue.reason
        for issue in result.issues
    )


def test_current_state_entry_does_not_coerce_wrong_ui_value_type() -> None:
    store, key = _store_with_float_value(0.6)
    payload = json.loads(dumps_param_store(store))
    payload["states"][0]["ui_value"] = "0.6"

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.get_state(key) is None
    assert any(
        issue.section == "states"
        and issue.index == 0
        and "ui_value must be a finite number" in issue.reason
        for issue in result.issues
    )


def test_current_state_numeric_overflow_is_diagnosed_as_invalid_input() -> None:
    store, key = _store_with_float_value(0.6)
    payload = json.loads(dumps_param_store(store))
    payload["states"][0]["ui_value"] = 10**400

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.get_state(key) is None
    assert any(
        issue.section == "states"
        and issue.index == 0
        and "ui_value must be a finite number" in issue.reason
        for issue in result.issues
    )


@pytest.mark.parametrize(
    ("cc_key", "reason_fragment"),
    [
        pytest.param(True, "cc_key", id="bool"),
        pytest.param(-1, "0..127", id="negative"),
        pytest.param(128, "0..127", id="above-range"),
        pytest.param("64", "cc_key", id="string"),
        pytest.param(1.0, "cc_key", id="float"),
        pytest.param([None, None, None], "at least one CC number", id="all-null"),
        pytest.param([1, False, 3], "0..127", id="component-bool"),
        pytest.param([1, -1, 3], "0..127", id="component-negative"),
        pytest.param([1, 128, 3], "0..127", id="component-above-range"),
        pytest.param([1, "2", 3], "0..127", id="component-string"),
    ],
)
def test_current_state_rejects_noncanonical_cc_numbers(
    cc_key: object,
    reason_fragment: str,
) -> None:
    store, key = _store_with_float_value(0.6)
    payload = json.loads(dumps_param_store(store))
    payload["states"][0]["cc_key"] = cc_key

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.get_state(key) is None
    assert any(
        issue.section == "states" and issue.index == 0 and reason_fragment in issue.reason
        for issue in result.issues
    )


@pytest.mark.parametrize(
    ("op", "meta", "ui_value", "cc_key", "reason_fragment"),
    [
        pytest.param(
            "float-op",
            ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
            0.5,
            [1, None, 3],
            "component MIDI CC is not supported for float",
            id="float-component-list",
        ),
        pytest.param(
            "vec3-op",
            ParamMeta(kind="vec3", ui_min=0.0, ui_max=1.0),
            (0.1, 0.2, 0.3),
            1,
            "scalar MIDI CC is not supported for vec3",
            id="vec3-scalar",
        ),
        pytest.param(
            "rgb-op",
            ParamMeta(kind="rgb", ui_min=0, ui_max=255),
            (1, 2, 3),
            1,
            "scalar MIDI CC is not supported for rgb",
            id="rgb-scalar",
        ),
        pytest.param(
            "__style__",
            ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
            0.5,
            1,
            "MIDI CC is not supported for __style__",
            id="style-scalar",
        ),
    ],
)
def test_current_state_rejects_cc_incompatible_with_kind_or_op(
    op: str,
    meta: ParamMeta,
    ui_value: object,
    cc_key: object,
    reason_fragment: str,
) -> None:
    store = ParamStore()
    key = ParameterKey(op=op, site_id="cc-contract", arg="value")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=ui_value,
                meta=meta,
                effective=ui_value,
                source="code",
                explicit=False,
            )
        ],
    )
    payload = json.loads(dumps_param_store(store))
    payload["states"][0]["cc_key"] = cc_key

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.get_state(key) is None
    assert any(
        issue.section == "states" and issue.index == 0 and reason_fragment in issue.reason
        for issue in result.issues
    )


def test_current_schema_reports_and_repairs_missing_parameter_ordinal() -> None:
    store, key = _store_with_float_value(0.6)
    payload = json.loads(dumps_param_store(store))
    del payload["ordinals"][key.op][key.site_id]

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.get_state(key) is not None
    assert result.store.get_ordinal(key.op, key.site_id) == 1
    assert any(
        issue.section == "ordinals" and f"{key.op}/{key.site_id}" in issue.reason
        for issue in result.issues
    )


def test_current_schema_reports_and_repairs_missing_chain_ordinal() -> None:
    payload = json.loads(dumps_param_store(_store_with_effect_order()))
    del payload["chain_ordinals"]["chain-order"]

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.chain_ordinals() == {"chain-order": 1}
    assert result.store._effects_ref().code_order("chain-order") == (
        ("scale", "scale-site"),
        ("rotate", "rotate-site"),
    )
    assert any(
        issue.section == "chain_ordinals" and "chain-order" in issue.reason
        for issue in result.issues
    )


def test_duplicate_parameter_and_chain_ordinals_are_repaired_with_issues() -> None:
    store = ParamStore()
    _merge_float_group(store, op="variant", site_id="a")
    _merge_float_group(store, op="variant", site_id="b")
    assert store._effects_ref().record_chain(
        chain_id="a",
        steps=(EffectStepTopology("scale", "scale-site", 1, 0),),
    )
    assert store._effects_ref().record_chain(
        chain_id="b",
        steps=(EffectStepTopology("rotate", "rotate-site", 1, 0),),
    )
    payload = json.loads(dumps_param_store(store))
    payload["ordinals"]["variant"]["b"] = 1
    payload["chain_ordinals"]["b"] = 1

    result = loads_param_store_result(json.dumps(payload))

    assert result.store.get_ordinal("variant", "a") == 1
    assert result.store.get_ordinal("variant", "b") == 2
    assert result.store.chain_ordinals() == {"a": 1, "b": 2}
    assert any(issue.section == "ordinals" for issue in result.issues)
    assert any(issue.section == "chain_ordinals" for issue in result.issues)


def test_current_schema_roundtrip_preserves_effect_topology_and_gui_order(
    tmp_path: Path,
) -> None:
    store = _store_with_effect_order()
    primary = tmp_path / "effect-order.json"
    recovery = param_store_recovery_path(primary)

    write_param_store(store, primary)
    write_param_store_recovery(store, recovery)

    payload = json.loads(primary.read_text(encoding="utf-8"))
    assert payload["schema_version"] == PARAM_STORE_SCHEMA_VERSION
    assert payload["ui"]["effect_order_overrides"] == [
        {
            "chain_id": "chain-order",
            "steps": [
                {"op": "rotate", "site_id": "rotate-site"},
                {"op": "scale", "site_id": "scale-site"},
            ],
        }
    ]
    assert [item["op"] for item in payload["effect_steps"]] == [
        "scale",
        "rotate",
    ]
    assert [item["n_inputs"] for item in payload["effect_steps"]] == [1, 1]

    for loaded in (_read_store(primary), _read_store(recovery)):
        assert loaded._effects_ref().code_order("chain-order") == (
            ("scale", "scale-site"),
            ("rotate", "rotate-site"),
        )
        assert loaded._effects_ref().effective_order("chain-order") == (
            ("rotate", "rotate-site"),
            ("scale", "scale-site"),
        )


@pytest.mark.parametrize("schema_version", [None, 1, 2, 3])
def test_non_current_schema_is_rejected_without_mutating_or_quarantining_file(
    tmp_path: Path,
    schema_version: int | None,
) -> None:
    payload = json.loads(dumps_param_store(_store_with_effect_order()))
    if schema_version is None:
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version
    original_payload = json.dumps(payload)

    with pytest.raises(UnsupportedParamStoreSchemaError) as exc_info:
        loads_param_store_result(original_payload)
    assert exc_info.value.found_version == schema_version
    assert json.dumps(payload) == original_payload

    path = tmp_path / "unsupported.json"
    path.write_text(original_payload, encoding="utf-8")
    with pytest.raises(UnsupportedParamStoreSchemaError):
        _read_store(path)

    assert path.read_text(encoding="utf-8") == original_payload
    assert list(tmp_path.glob("unsupported.json.corrupt-*")) == []


def test_malformed_effect_order_entry_is_diagnosed_and_dropped() -> None:
    payload = json.loads(dumps_param_store(_store_with_effect_order()))
    payload["ui"]["effect_order_overrides"].append(
        {
            "chain_id": "broken-chain",
            "steps": [
                {"op": "scale", "site_id": ""},
                {"op": "scale", "site_id": ""},
            ],
        }
    )

    result = loads_param_store_result(json.dumps(payload))

    assert [issue.section for issue in result.issues] == ["ui.effect_order_overrides"]
    assert result.store._effects_ref().order_overrides() == {
        "chain-order": (
            ("rotate", "rotate-site"),
            ("scale", "scale-site"),
        )
    }


def test_order_without_saved_topology_is_diagnosed_and_dropped() -> None:
    payload = json.loads(dumps_param_store(ParamStore()))
    payload["ui"]["effect_order_overrides"] = [
        {
            "chain_id": "late-chain",
            "steps": [
                {"op": "rotate", "site_id": "rotate-site"},
                {"op": "scale", "site_id": "scale-site"},
            ],
        }
    ]
    result = loads_param_store_result(json.dumps(payload))
    loaded = result.store

    assert len(result.issues) == 1
    assert result.issues[0].section == "ui.effect_order_overrides"
    assert "topology is missing" in result.issues[0].reason
    assert loaded._effects_ref().order_overrides() == {}

    assert loaded._effects_ref().record_chain(
        chain_id="late-chain",
        steps=(
            EffectStepTopology("scale", "scale-site", 1, 0),
            EffectStepTopology("rotate", "rotate-site", 1, 1),
        ),
    )

    assert loaded._effects_ref().effective_order("late-chain") == (
        ("scale", "scale-site"),
        ("rotate", "rotate-site"),
    )


@pytest.mark.parametrize(
    ("corruption", "reason_fragment"),
    [
        ("incomplete", "exact permutation"),
        ("duplicate_topology", "duplicate step identity"),
        ("noncontiguous_topology", "contiguous from 0"),
        ("missing_n_inputs", "missing fields: n_inputs"),
        ("multi_input", "multi-input"),
    ],
)
def test_effect_order_incompatible_with_saved_topology_is_diagnosed_and_dropped(
    corruption: str,
    reason_fragment: str,
) -> None:
    payload = json.loads(dumps_param_store(_store_with_effect_order()))
    if corruption == "incomplete":
        payload["ui"]["effect_order_overrides"][0]["steps"].pop()
    elif corruption == "duplicate_topology":
        payload["effect_steps"][1]["op"] = payload["effect_steps"][0]["op"]
        payload["effect_steps"][1]["site_id"] = payload["effect_steps"][0]["site_id"]
    elif corruption == "noncontiguous_topology":
        payload["effect_steps"][1]["step_index"] = 2
    elif corruption == "missing_n_inputs":
        payload["effect_steps"][1].pop("n_inputs")
    else:
        payload["effect_steps"][0]["n_inputs"] = 2

    result = loads_param_store_result(json.dumps(payload))

    assert any(reason_fragment in issue.reason for issue in result.issues)
    assert result.store.effect_order_overrides() == {}
    assert_invariants(result.store)


def test_future_schema_is_rejected_without_quarantine_or_empty_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "future.json"
    payload = {"schema_version": PARAM_STORE_SCHEMA_VERSION + 1}
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UnsupportedParamStoreSchemaError) as exc_info:
        _read_store(path)

    assert exc_info.value.found_version == PARAM_STORE_SCHEMA_VERSION + 1
    assert path.read_text(encoding="utf-8") == json.dumps(payload)
    assert list(tmp_path.glob("future.json.corrupt-*")) == []


def test_read_and_recovery_apis_return_one_load_result_contract(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.json"

    results = (
        read_param_store(path),
        recover_primary_param_store(path),
        recover_param_store_session(path),
    )

    assert all(type(result) is ParamStoreLoadResult for result in results)
    assert all(result.status == "missing" for result in results)
    assert all(result.load_state.provenance == "primary" for result in results)
    assert all(result.load_state.diagnostics == () for result in results)


def test_partial_corruption_read_is_non_mutating_and_reports_dropped_entry(
    tmp_path: Path,
) -> None:
    store, key = _store_with_float_value(0.6)
    payload_obj = json.loads(dumps_param_store(store))
    payload_obj["states"].append(
        {
            "op": "broken",
            "arg": "amount",
            "ui_value": 0.9,
        }
    )
    original_payload = json.dumps(payload_obj)
    path = tmp_path / "partial.json"
    path.write_text(original_payload, encoding="utf-8")
    mtime_before = path.stat().st_mtime_ns
    entries_before = tuple(sorted(item.name for item in tmp_path.iterdir()))

    result = read_param_store(path)
    loaded = result.store

    state = loaded.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.6)
    assert result.status == "partial"
    assert result.load_state.provenance == "primary"
    assert path.read_text(encoding="utf-8") == original_payload
    assert path.stat().st_mtime_ns == mtime_before
    assert tuple(sorted(item.name for item in tmp_path.iterdir())) == entries_before
    assert len(result.load_state.diagnostics) == 1
    diagnostic = result.load_state.diagnostics[0]
    assert diagnostic.code == "partial_read"
    assert diagnostic.backup_path is None
    assert "states[1]" in diagnostic.details


def test_repaired_partial_primary_survives_restart_before_any_user_change(
    tmp_path: Path,
) -> None:
    store, key = _store_with_float_value(0.6)
    payload_obj = json.loads(dumps_param_store(store))
    payload_obj["states"].append(
        {
            "op": "broken",
            "arg": "amount",
            "ui_value": 0.9,
        }
    )
    primary = tmp_path / "partial.json"
    primary.write_text(json.dumps(payload_obj), encoding="utf-8")
    recovery = param_store_recovery_path(primary)

    first_launch = recover_param_store_session(primary)

    assert first_launch.status == "partial"
    first_state = first_launch.store.get_state(key)
    assert first_state is not None
    assert first_state.ui_value == pytest.approx(0.6)
    assert first_launch.load_state.provenance == "quarantined"
    assert not primary.exists()
    assert recovery.is_file()

    # user operation/finalize を一度も行わず異常終了し、同じ path から再起動する。
    restarted = recover_param_store_session(primary)

    assert restarted.status == "loaded"
    restarted_state = restarted.store.get_state(key)
    assert restarted_state is not None
    assert restarted_state.ui_value == pytest.approx(0.6)
    assert restarted.load_state.provenance == "session_recovery"
    assert recovery.is_file()


def test_missing_writer_ordinals_are_quarantined_and_recovered(
    tmp_path: Path,
) -> None:
    store, key = _store_with_parameter_and_effect_chain()
    payload = json.loads(dumps_param_store(store))
    del payload["ordinals"][key.op][key.site_id]
    del payload["chain_ordinals"]["strict-chain"]
    original_payload = json.dumps(payload)
    primary = tmp_path / "missing-ordinals.json"
    primary.write_text(original_payload, encoding="utf-8")
    recovery = param_store_recovery_path(primary)

    first_launch = recover_param_store_session(primary)

    assert first_launch.store.get_state(key) is not None
    assert first_launch.store.get_ordinal(key.op, key.site_id) == 1
    assert first_launch.store.chain_ordinals() == {"strict-chain": 1}
    assert first_launch.load_state.provenance == "quarantined"
    assert not primary.exists()
    backups = list(tmp_path.glob("missing-ordinals.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original_payload
    assert recovery.is_file()
    assert len(first_launch.load_state.diagnostics) == 1
    diagnostic = first_launch.load_state.diagnostics[0]
    assert diagnostic.code == "partial_quarantine"
    assert f"{key.op}/{key.site_id}" in diagnostic.details
    assert "strict-chain" in diagnostic.details

    restarted = recover_param_store_session(primary)

    assert restarted.store.get_state(key) is not None
    assert restarted.store.get_ordinal(key.op, key.site_id) == 1
    assert restarted.store.chain_ordinals() == {"strict-chain": 1}
    assert restarted.load_state.provenance == "session_recovery"
    assert restarted.load_state.diagnostics == ()
    assert recovery.is_file()


def test_repaired_recovery_save_failure_rolls_quarantine_back_to_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _key = _store_with_float_value(0.6)
    payload_obj = json.loads(dumps_param_store(store))
    payload_obj["states"].append(
        {
            "op": "broken",
            "arg": "amount",
            "ui_value": 0.9,
        }
    )
    primary = tmp_path / "partial.json"
    original_payload = json.dumps(payload_obj)
    primary.write_text(original_payload, encoding="utf-8")

    def fail_recovery_save(_store: ParamStore, _path: Path) -> None:
        raise OSError("recovery save failed")

    monkeypatch.setattr(
        storage_module,
        "write_param_store_recovery",
        fail_recovery_save,
    )

    with pytest.raises(OSError, match="recovery save failed"):
        recover_param_store_session(primary)

    assert primary.read_text(encoding="utf-8") == original_payload
    assert list(tmp_path.glob("partial.json.corrupt-*")) == []
    assert not param_store_recovery_path(primary).exists()


def test_quarantine_rename_failure_keeps_primary_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = tmp_path / "broken.json"
    original_payload = "{broken-json"
    primary.write_text(original_payload, encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("quarantine unavailable")

    monkeypatch.setattr(storage_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="quarantine unavailable"):
        recover_param_store_session(primary)

    assert primary.read_text(encoding="utf-8") == original_payload
    assert list(tmp_path.glob("broken.json.corrupt-*")) == []
    assert not param_store_recovery_path(primary).exists()


def test_param_store_file_roundtrip_includes_named_variations(tmp_path: Path) -> None:
    store, key = _store_with_float_value(0.3)
    create_variation(
        store,
        "print candidate",
        note="first pass",
        seed=41,
        t=1.25,
        created_at=100.0,
    )
    ok, error = update_state_from_ui(
        store,
        key,
        0.9,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
        override=True,
    )
    assert ok and error is None

    path = tmp_path / "store.json"
    write_param_store(store, path)
    loaded = _read_store(path)

    assert [variation.name for variation in list_variations(loaded)] == ["print candidate"]
    assert restore_variation(loaded, "print candidate") is True
    restored = loaded.get_state(key)
    assert restored is not None
    assert restored.ui_value == pytest.approx(0.3)


def test_session_recovery_preserves_live_explicit_override_until_clean_exit(
    tmp_path: Path,
) -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site-1", arg="radius")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.25,
                meta=meta,
                effective=0.25,
                source="code",
                explicit=True,
            )
        ],
    )
    ok, error = update_state_from_ui(
        store,
        key,
        0.9,
        meta=meta,
        override=True,
    )
    assert ok and error is None

    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    write_param_store_recovery(store, recovery)

    recovered = recover_param_store_session(primary)
    recovered_state = recovered.store.get_state(key)
    assert recovered_state is not None
    assert recovered_state.ui_value == pytest.approx(0.9)
    assert recovered_state.override is True

    finalize_parameter_session(
        recovered.store,
        primary,
        known_operations=KnownOperationSchemaSnapshot.empty(),
    )
    assert not recovery.exists()
    clean_state = _read_store(primary).get_state(key)
    assert clean_state is not None
    assert clean_state.ui_value == pytest.approx(0.9)
    assert clean_state.override is False


def test_newer_session_recovery_wins_over_primary(tmp_path: Path) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, key = _store_with_float_value(0.2)
    recovery_store, _ = _store_with_float_value(0.8)
    write_param_store(primary_store, primary)
    write_param_store_recovery(recovery_store, recovery)
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)

    loaded = recover_param_store_session(primary)

    assert loaded.status == "loaded"
    state = loaded.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.8)
    assert loaded.load_state.provenance == "session_recovery"


def test_session_recovery_includes_named_variations(tmp_path: Path) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, _key = _store_with_float_value(0.2)
    recovery_store, key = _store_with_float_value(0.4)
    create_variation(
        recovery_store,
        "live candidate",
        note="not finalized yet",
        created_at=100.0,
    )
    ok, error = update_state_from_ui(
        recovery_store,
        key,
        0.8,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
        override=True,
    )
    assert ok and error is None
    write_param_store(primary_store, primary)
    write_param_store_recovery(recovery_store, recovery)
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)

    loaded = recover_param_store_session(primary)

    assert [variation.name for variation in list_variations(loaded.store)] == [
        "live candidate"
    ]
    assert restore_variation(loaded.store, "live candidate") is True
    state = loaded.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.4)


def test_session_recovery_roundtrip_preserves_parameter_locks(tmp_path: Path) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, _ = _store_with_float_value(0.2)
    recovery_store, key = _store_with_float_value(0.4)
    assert set_parameters_locked(recovery_store, [key], locked=True) == (key,)
    write_param_store(primary_store, primary)
    write_param_store_recovery(recovery_store, recovery)
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)

    loaded = recover_param_store_session(primary)

    assert loaded.load_state.provenance == "session_recovery"
    assert is_parameter_locked(loaded.store, key) is True
    state = loaded.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.4)


def test_primary_wins_when_it_is_newer_than_session_recovery(tmp_path: Path) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, key = _store_with_float_value(0.2)
    recovery_store, _ = _store_with_float_value(0.8)
    write_param_store(primary_store, primary)
    write_param_store_recovery(recovery_store, recovery)
    _set_mtime(recovery, 1_700_000_000_000_000_000)
    _set_mtime(primary, 1_700_000_001_000_000_000)

    loaded = recover_param_store_session(primary)

    state = loaded.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.2)
    # loader はデータを勝手に消さず、clean finalize に cleanup を任せる。
    assert recovery.exists()


def test_future_schema_recovery_is_rejected_without_fallback(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, _key = _store_with_float_value(0.2)
    write_param_store(primary_store, primary)
    recovery_payload = {"schema_version": PARAM_STORE_SCHEMA_VERSION + 1}
    recovery.write_text(json.dumps(recovery_payload), encoding="utf-8")
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)

    with pytest.raises(UnsupportedParamStoreSchemaError):
        recover_param_store_session(primary)

    assert primary.exists()
    assert recovery.read_text(encoding="utf-8") == json.dumps(recovery_payload)
    assert list(tmp_path.glob("store.session.json.corrupt-*")) == []


def test_future_primary_is_not_overwritten_by_newer_compatible_recovery(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    future_payload = {"schema_version": PARAM_STORE_SCHEMA_VERSION + 1}
    primary.write_text(json.dumps(future_payload), encoding="utf-8")
    recovery_store, _key = _store_with_float_value(0.8)
    write_param_store_recovery(recovery_store, recovery)
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)

    with pytest.raises(UnsupportedParamStoreSchemaError):
        recover_param_store_session(primary)

    assert primary.read_text(encoding="utf-8") == json.dumps(future_payload)
    assert recovery.exists()


def test_corrupt_newer_recovery_is_quarantined_and_primary_is_loaded(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, key = _store_with_float_value(0.3)
    write_param_store(primary_store, primary)
    recovery.write_text("{broken recovery", encoding="utf-8")
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)

    with caplog.at_level(logging.WARNING):
        loaded = recover_param_store_session(primary)

    assert loaded.status == "invalid"
    state = loaded.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.3)
    assert not recovery.exists()
    backups = list(tmp_path.glob("store.session.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken recovery"
    assert "session recovery を退避" in caplog.text
    assert loaded.load_state.provenance == "quarantined"
    assert loaded.load_state.diagnostics[0].code == "recovery_quarantine"


def test_recovery_read_errors_are_not_misclassified_as_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, _key = _store_with_float_value(0.3)
    recovery_store, _ = _store_with_float_value(0.8)
    write_param_store(primary_store, primary)
    write_param_store_recovery(recovery_store, recovery)
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)
    original_read_text = Path.read_text

    def fail_recovery_read(self: Path, *, encoding: str) -> str:
        if self == recovery:
            raise PermissionError(self)
        return original_read_text(self, encoding=encoding)

    monkeypatch.setattr(Path, "read_text", fail_recovery_read)

    with pytest.raises(PermissionError):
        recover_param_store_session(primary)
    assert recovery.exists()
    assert list(tmp_path.glob("store.session.json.corrupt-*")) == []


def test_unexpected_recovery_decoder_error_propagates_without_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    primary_store, _key = _store_with_float_value(0.3)
    recovery_store, _ = _store_with_float_value(0.8)
    write_param_store(primary_store, primary)
    write_param_store_recovery(recovery_store, recovery)
    primary_before = primary.read_bytes()
    recovery_before = recovery.read_bytes()
    _set_mtime(primary, 1_700_000_000_000_000_000)
    _set_mtime(recovery, 1_700_000_001_000_000_000)

    def fail_decode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected decoder failure")

    monkeypatch.setattr(
        storage_module,
        "loads_param_store_result",
        fail_decode,
    )

    with pytest.raises(RuntimeError, match="unexpected decoder failure"):
        recover_param_store_session(primary)

    assert primary.read_bytes() == primary_before
    assert recovery.read_bytes() == recovery_before
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_finalize_keeps_recovery_when_primary_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    store, _key = _store_with_float_value(0.8, explicit=True)
    write_param_store_recovery(store, recovery)
    recovery_before = recovery.read_bytes()

    def fail_primary_save(_store: ParamStore, _path: Path) -> None:
        raise OSError("primary unavailable")

    monkeypatch.setattr(storage_module, "write_param_store", fail_primary_save)

    with pytest.raises(OSError, match="primary unavailable"):
        finalize_parameter_session(
            store,
            primary,
            known_operations=KnownOperationSchemaSnapshot.empty(),
        )
    assert recovery.read_bytes() == recovery_before
    assert not primary.exists()


def test_finalize_unlink_failure_keeps_committed_primary_and_retryable_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    store, key = _store_with_float_value(0.8, explicit=True)
    write_param_store_recovery(store, recovery)
    recovery_before = recovery.read_bytes()
    original_unlink = Path.unlink

    def fail_recovery_unlink(self: Path, *, missing_ok: bool = False) -> None:
        if self == recovery:
            raise OSError("recovery unlink failed")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_recovery_unlink)
    with caplog.at_level(logging.ERROR):
        finalize_parameter_session(
            store,
            primary,
            known_operations=KnownOperationSchemaSnapshot.empty(),
        )

    assert read_param_store(primary).store.get_state(key) is not None
    assert recovery.read_bytes() == recovery_before
    assert "session recovery を削除できませんでした" in caplog.text

    monkeypatch.setattr(Path, "unlink", original_unlink)
    finalize_parameter_session(
        store,
        primary,
        known_operations=KnownOperationSchemaSnapshot.empty(),
    )
    assert primary.is_file()
    assert not recovery.exists()


def test_write_param_store_keeps_loaded_group_before_first_frame(tmp_path: Path):
    path = tmp_path / "store.json"
    original = ParamStore()
    key = _merge_float_group(original, op="custom", site_id="loaded")
    write_param_store(original, path)

    loaded = _read_store(path)
    assert key in store_snapshot(loaded)

    # run 開始後、1 frame も成功しないまま終了した場合を再現する。
    write_param_store(loaded, path)

    assert key in store_snapshot(_read_store(path))


def test_write_param_store_keeps_loaded_group_hidden_by_condition(tmp_path: Path):
    path = tmp_path / "store.json"
    original = ParamStore()
    visible_key = _merge_float_group(original, op="branch", site_id="visible")
    hidden_key = _merge_float_group(original, op="branch", site_id="hidden")
    write_param_store(original, path)

    loaded = _read_store(path)
    # この run では条件分岐の片側だけが実行された状態。
    _merge_float_group(loaded, op="branch", site_id="visible")
    write_param_store(loaded, path)

    reloaded = _read_store(path)
    snapshot = store_snapshot(reloaded)
    assert visible_key in snapshot
    assert hidden_key in snapshot


def test_write_param_store_keeps_loaded_groups_after_failed_frame(tmp_path: Path):
    path = tmp_path / "store.json"
    original = ParamStore()
    reached_key = _merge_float_group(original, op="failed", site_id="reached")
    later_key = _merge_float_group(original, op="failed", site_id="later")
    write_param_store(original, path)

    loaded = _read_store(path)
    with pytest.raises(RuntimeError, match="draw failed"):
        with parameter_context(loaded):
            frame_params = current_frame_params()
            assert frame_params is not None
            frame_params.record(
                key=reached_key,
                base=0.5,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.5,
                source="code",
                explicit=False,
            )
            # `later_key` を評価する前に draw が失敗した状態。
            raise RuntimeError("draw failed")
    write_param_store(loaded, path)

    snapshot = store_snapshot(_read_store(path))
    assert reached_key in snapshot
    assert later_key in snapshot


def test_broken_json_read_is_non_mutating(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
):
    path = tmp_path / "broken.json"
    broken_payload = "{broken-json"
    path.write_text(broken_payload, encoding="utf-8")
    mtime_before = path.stat().st_mtime_ns
    entries_before = tuple(sorted(item.name for item in tmp_path.iterdir()))

    with caplog.at_level(logging.WARNING):
        result = read_param_store(path)
    loaded = result.store

    assert store_snapshot(loaded) == {}
    assert_invariants(loaded)
    assert result.status == "invalid"
    assert isinstance(result.error, json.JSONDecodeError)
    assert result.load_state.provenance == "primary"
    assert result.load_state.diagnostics[0].code == "load_error"
    assert path.read_text(encoding="utf-8") == broken_payload
    assert path.stat().st_mtime_ns == mtime_before
    assert tuple(sorted(item.name for item in tmp_path.iterdir())) == entries_before
    assert "非変更で読み込みました" in caplog.text


def test_explicit_recovery_quarantines_broken_primary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "broken.json"
    broken_payload = "{broken-json"
    path.write_text(broken_payload, encoding="utf-8")

    loaded = recover_param_store_session(path)

    assert loaded.status == "invalid"
    assert store_snapshot(loaded.store) == {}
    assert loaded.load_state.provenance == "quarantined"
    assert loaded.load_state.diagnostics[0].code == "load_quarantine"
    assert not path.exists()
    backups = list(tmp_path.glob("broken.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == broken_payload


def test_read_param_store_does_not_hide_read_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "store.json"
    path.write_text("{}", encoding="utf-8")

    def fail_read_text(self: Path, *, encoding: str) -> str:
        raise PermissionError(self)

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    with pytest.raises(PermissionError):
        _read_store(path)


def test_unexpected_primary_decoder_error_propagates_without_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "store.json"
    store, _key = _store_with_float_value(0.3)
    write_param_store(store, path)
    original = path.read_bytes()

    def fail_decode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected decoder failure")

    monkeypatch.setattr(
        storage_module,
        "loads_param_store_result",
        fail_decode,
    )

    with pytest.raises(RuntimeError, match="unexpected decoder failure"):
        _read_store(path)

    assert path.read_bytes() == original
    assert list(tmp_path.glob("store.json.corrupt-*")) == []


def test_write_param_store_keeps_existing_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "store.json"
    path.write_text("original\n", encoding="utf-8")

    def fail_replace(
        src: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        dst: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        raise OSError(f"replace failed: {src} -> {dst}")

    monkeypatch.setattr("grafix.file_io.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        write_param_store(ParamStore(), path)

    assert path.read_text(encoding="utf-8") == "original\n"
    assert list(tmp_path.glob(".store.json.*.tmp")) == []


def test_direct_save_does_not_prune_unknown_argument(tmp_path: Path) -> None:
    store = ParamStore()
    known = ParameterKey(op="line", site_id="site-1", arg="length")
    unknown = ParameterKey(op="line", site_id="site-1", arg="__unknown__")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=known,
                base=1.0,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=2.0),
                effective=1.0,
                source="code",
                explicit=False,
            ),
            FrameParamRecord(
                key=unknown,
                base=0.1,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.1,
                source="code",
                explicit=True,
            ),
        ],
    )

    path = tmp_path / "store.json"
    write_param_store(store, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    for section in ["states", "meta", "explicit"]:
        assert any(
            it.get("op") == "line" and it.get("arg") == "__unknown__"
            for it in payload.get(section, [])
        )


def test_session_finalize_prunes_with_injected_schema_snapshot(tmp_path: Path) -> None:
    store = ParamStore()
    known = ParameterKey(op="line", site_id="site-1", arg="length")
    unknown = ParameterKey(op="line", site_id="site-1", arg="__unknown__")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=known,
                base=1.0,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=2.0),
                effective=1.0,
                source="code",
                explicit=False,
            ),
            FrameParamRecord(
                key=unknown,
                base=0.1,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.1,
                source="code",
                explicit=True,
            ),
        ],
    )

    path = tmp_path / "store.json"
    finalize_parameter_session(
        store,
        path,
        known_operations=KnownOperationSchemaSnapshot({"line": frozenset({"length"})}),
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    for section in ["states", "meta", "explicit"]:
        assert not any(
            it.get("op") == "line" and it.get("arg") == "__unknown__"
            for it in payload.get(section, [])
        )

    assert any(it.get("op") == "line" and it.get("arg") == "length" for it in payload["states"])
