from __future__ import annotations

from pathlib import Path

import pytest

from grafix.core.parameters import (
    FrameParamRecord,
    KnownOperationSchemaSnapshot,
    ParamMeta,
    ParamStore,
    ParameterKey,
    favorite_parameter_keys,
    locked_parameter_keys,
    set_parameters_favorite,
    set_parameters_locked,
)
from grafix.core.parameters.codec import dumps_param_store
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.runtime import ParamStoreLoadDiagnostic
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.runtime.parameter_recovery import (
    ParamStoreRecoverySession,
    param_store_load_diagnostic_events,
    recovered_session_diagnostic,
)
from grafix.parameter_storage import (
    param_store_recovery_path,
    read_param_store,
    recover_param_store_session,
    write_param_store,
    write_param_store_recovery,
)

_KNOWN_OPERATIONS = KnownOperationSchemaSnapshot({"circle": frozenset({"radius"})})


def _store(value: float) -> tuple[ParamStore, ParameterKey]:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="main", arg="radius")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.2,
                meta=meta,
                effective=0.2,
                source="code",
                explicit=False,
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


def _recovered_session(
    tmp_path: Path,
) -> tuple[Path, Path, ParamStore, ParameterKey]:
    primary_path = tmp_path / "params.json"
    recovery_path = param_store_recovery_path(primary_path)
    primary, key = _store(0.2)
    recovered, _ = _store(0.8)
    write_param_store(primary, primary_path)
    write_param_store_recovery(recovered, recovery_path)
    loaded = recover_param_store_session(primary_path)
    assert loaded.load_provenance == "session_recovery"
    return primary_path, recovery_path, loaded, key


def test_recovered_session_diagnostic_has_decision_actions(tmp_path: Path) -> None:
    event = recovered_session_diagnostic(tmp_path / "params.json")

    assert event.summary == "Recovered session"
    assert tuple(action.action_id for action in event.actions) == (
        "keep",
        "discard",
        "compare",
    )


def test_recovery_session_rejects_implicit_path_coercion() -> None:
    with pytest.raises(TypeError, match="primary_path"):
        ParamStoreRecoverySession(
            ParamStore(),
            "params.json",  # type: ignore[arg-type]
            _KNOWN_OPERATIONS,
        )


def test_keep_promotes_recovered_state_and_removes_journal(tmp_path: Path) -> None:
    primary_path, recovery_path, store, key = _recovered_session(tmp_path)
    session = ParamStoreRecoverySession(store, primary_path, _KNOWN_OPERATIONS)

    session.keep()

    assert not recovery_path.exists()
    assert store.load_provenance == "primary"
    state = read_param_store(primary_path).store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.8)


def test_discard_restores_primary_in_place_and_removes_journal(tmp_path: Path) -> None:
    primary_path, recovery_path, store, key = _recovered_session(tmp_path)
    identity = id(store)
    session = ParamStoreRecoverySession(store, primary_path, _KNOWN_OPERATIONS)

    diagnostics = session.discard()

    assert id(store) == identity
    assert diagnostics == ()
    assert not recovery_path.exists()
    assert store.load_provenance == "primary"
    state = store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("primary_marked", "recovery_marked"),
    ((True, False), (False, True)),
)
def test_discard_exactly_restores_primary_lock_and_favorite_state(
    tmp_path: Path,
    *,
    primary_marked: bool,
    recovery_marked: bool,
) -> None:
    primary_path = tmp_path / "params.json"
    recovery_path = param_store_recovery_path(primary_path)
    primary, key = _store(0.2)
    recovered, _ = _store(0.8)
    set_parameters_locked(primary, (key,), locked=primary_marked)
    set_parameters_favorite(primary, (key,), favorite=primary_marked)
    set_parameters_locked(recovered, (key,), locked=recovery_marked)
    set_parameters_favorite(recovered, (key,), favorite=recovery_marked)
    write_param_store(primary, primary_path)
    expected_primary = dumps_param_store(read_param_store(primary_path).store)
    write_param_store_recovery(recovered, recovery_path)
    loaded = recover_param_store_session(primary_path)
    assert bool(locked_parameter_keys(loaded)) is recovery_marked
    assert bool(favorite_parameter_keys(loaded)) is recovery_marked

    ParamStoreRecoverySession(
        loaded,
        primary_path,
        _KNOWN_OPERATIONS,
    ).discard()

    assert bool(locked_parameter_keys(loaded)) is primary_marked
    assert bool(favorite_parameter_keys(loaded)) is primary_marked
    assert dumps_param_store(loaded) == expected_primary
    assert not recovery_path.exists()


def test_compare_returns_copyable_unified_diff(tmp_path: Path) -> None:
    primary_path, _recovery_path, store, _key = _recovered_session(tmp_path)
    session = ParamStoreRecoverySession(store, primary_path, _KNOWN_OPERATIONS)

    event = session.compare_diagnostic()

    assert event.summary == "Recovered session comparison"
    assert "--- " in event.details
    assert "+++ " in event.details
    assert event.actions[0].action_id == "copy"


def test_recovery_failure_is_converted_to_shared_center_event(tmp_path: Path) -> None:
    backup = tmp_path / "params.session.json.corrupt"
    store = ParamStore()
    store._runtime_ref().load_diagnostics = (
        ParamStoreLoadDiagnostic(
            code="recovery_quarantine",
            summary="Recovery was quarantined",
            details="invalid JSON",
            backup_path=backup,
        ),
    )

    events = param_store_load_diagnostic_events(
        store,
        primary_path=tmp_path / "params.json",
    )

    assert len(events) == 1
    assert events[0].category == "recovery"
    assert events[0].summary == "Recovery was quarantined"
    assert events[0].source == str(backup)
    assert tuple(action.action_id for action in events[0].actions) == (
        "copy",
        "open",
    )
