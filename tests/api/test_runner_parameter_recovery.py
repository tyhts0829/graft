# ruff: noqa: E402 -- pyglet option must be set before importing runner.

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pyglet
import pytest

pyglet.options["shadow_window"] = False

import grafix.api._runner_application as runner_module
import grafix.interactive.midi.factory as midi_factory_module
import grafix.interactive.runtime.parameter_gui_system as gui_system_module
import grafix.interactive.runtime.parameter_recovery as parameter_recovery_module
import grafix.interactive.runtime.parameter_session as parameter_session_module
import grafix.parameter_storage as parameter_storage_module
from grafix.core.parameters import (
    FrameParamRecord,
    KnownOperationSchemaSnapshot,
    ParamMeta,
    ParamStore,
    ParameterKey,
)
from grafix.core.parameters.autosave import ParamStoreAutosave
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.parameter_storage import (
    param_store_recovery_path,
    read_param_store,
    recover_param_store_session,
    write_param_store,
    write_param_store_recovery,
)
from grafix.core.runtime_config import RuntimeConfigFallback
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.midi.midi_controller import (
    CcSnapshotLoadResult,
    CcSnapshotWriteBlockedError,
    shutdown_midi_controller,
)
from grafix.interactive.runtime.monitor import RuntimeMonitor
from grafix.interactive.diagnostics import DiagnosticAction, DiagnosticEvent

_EMPTY_KNOWN_OPERATIONS = KnownOperationSchemaSnapshot.empty()
_CIRCLE_KNOWN_OPERATIONS = KnownOperationSchemaSnapshot(
    {"circle": frozenset({"radius"})}
)


def test_parameter_session_owns_store_history_and_nonpersistent_finalize() -> None:
    session = parameter_session_module.ParameterSession(
        primary_path=None,
        gui_enabled=True,
        known_operations=_EMPTY_KNOWN_OPERATIONS,
    )

    assert isinstance(session.store, ParamStore)
    assert session.history is not None
    assert session.snapshot_slots is not None
    assert session.autosave is None
    assert session.capture_state().source == "code"
    session.persist(session_completed_cleanly=True, monitor=None)


def test_config_fallback_is_published_to_shared_diagnostic_center(
    tmp_path: Path,
) -> None:
    source = tmp_path / "config.yaml"
    monitor = RuntimeMonitor()
    fallback = RuntimeConfigFallback(
        summary="RuntimeError: unknown key",
        details="traceback with nearest key",
        source=source,
    )

    event = runner_module._publish_runtime_config_fallback(monitor, fallback)

    assert event.category == "config"
    assert event.source == str(source)
    assert event.details == "traceback with nearest key"
    assert tuple(action.action_id for action in event.actions) == ("copy", "open")
    assert monitor.snapshot().diagnostics == (event,)


def _session_with_dirty_explicit_override(
    primary: Path,
) -> tuple[ParamStore, ParameterKey, ParamStoreAutosave]:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site", arg="radius")
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
    autosave = ParamStoreAutosave(
        store,
        param_store_recovery_path(primary),
        save=write_param_store_recovery,
    )
    ok, error = update_state_from_ui(
        store,
        key,
        0.9,
        meta=meta,
        override=True,
    )
    assert ok and error is None
    return store, key, autosave


def _parameter_session_with_pending_recovery(
    primary: Path,
    *,
    include_obsolete: bool = False,
) -> tuple[parameter_session_module.ParameterSession, ParameterKey, ParameterKey | None]:
    primary_store = ParamStore()
    key = ParameterKey(op="circle", site_id="site", arg="radius")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        primary_store,
        (
            FrameParamRecord(
                key=key,
                base=0.25,
                meta=meta,
                effective=0.25,
                source="code",
                explicit=True,
            ),
        ),
    )
    write_param_store(primary_store, primary)
    recovered_store, recovered_key, autosave = _session_with_dirty_explicit_override(
        primary
    )
    assert recovered_key == key
    obsolete: ParameterKey | None = None
    if include_obsolete:
        obsolete = ParameterKey(op="circle", site_id="site", arg="obsolete")
        merge_frame_params(
            recovered_store,
            (
                FrameParamRecord(
                    key=obsolete,
                    base=0.4,
                    meta=meta,
                    effective=0.4,
                    source="code",
                    explicit=True,
                ),
            ),
        )
    autosave.flush()
    session = parameter_session_module.ParameterSession(
        primary_path=primary,
        gui_enabled=False,
        known_operations=(
            _CIRCLE_KNOWN_OPERATIONS
            if include_obsolete
            else _EMPTY_KNOWN_OPERATIONS
        ),
    )
    assert session.load_state.provenance == "session_recovery"
    return session, key, obsolete


def _recovery_action(
    session: parameter_session_module.ParameterSession,
    action_id: str,
) -> tuple[RuntimeMonitor, DiagnosticEvent, DiagnosticAction]:
    monitor = RuntimeMonitor()
    recovery = session.install_diagnostic_actions(monitor)
    assert recovery is not None
    event = next(
        item
        for item in monitor.snapshot().diagnostics
        if item.summary == "Recovered session"
    )
    action = next(item for item in event.actions if item.action_id == action_id)
    return monitor, event, action


def test_abnormal_shutdown_flushes_recovery_without_finalizing_primary(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    store, key, autosave = _session_with_dirty_explicit_override(primary)

    parameter_session_module._persist_param_store_on_shutdown(
        store=store,
        primary_path=primary,
        autosave=autosave,
        session_completed_cleanly=False,
        known_operations=_EMPTY_KNOWN_OPERATIONS,
    )

    assert not primary.exists()
    assert recovery.exists()
    recovered = recover_param_store_session(primary).store.get_state(key)
    assert recovered is not None
    assert recovered.ui_value == pytest.approx(0.9)
    assert recovered.override is True


def test_clean_shutdown_promotes_primary_and_removes_recovery(tmp_path: Path) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    store, key, autosave = _session_with_dirty_explicit_override(primary)

    parameter_session_module._persist_param_store_on_shutdown(
        store=store,
        primary_path=primary,
        autosave=autosave,
        session_completed_cleanly=True,
        known_operations=_EMPTY_KNOWN_OPERATIONS,
    )

    assert primary.exists()
    assert not recovery.exists()
    finalized = read_param_store(primary).store.get_state(key)
    assert finalized is not None
    assert finalized.ui_value == pytest.approx(0.9)
    assert finalized.override is False


def test_recovered_session_actions_are_wired_to_shared_diagnostic_center(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "store.json"
    recovery = param_store_recovery_path(primary)
    store, key, autosave = _session_with_dirty_explicit_override(primary)
    autosave.flush()
    parameter_session = parameter_session_module.ParameterSession(
        primary_path=primary,
        gui_enabled=False,
        known_operations=_EMPTY_KNOWN_OPERATIONS,
    )
    monitor = RuntimeMonitor()

    recovery_session = parameter_session.install_diagnostic_actions(
        monitor,
        open_source=lambda _source: None,
    )

    assert recovery_session is not None
    snapshot = monitor.snapshot()
    assert snapshot.recovered_session is True
    recovered_event = next(
        event for event in snapshot.diagnostics if event.summary == "Recovered session"
    )
    assert tuple(action.action_id for action in recovered_event.actions) == (
        "keep",
        "discard",
        "compare",
    )

    compare = next(action for action in recovered_event.actions if action.action_id == "compare")
    assert monitor.diagnostic_center.dispatch_action(recovered_event, compare)
    assert any(
        event.summary == "Recovered session comparison" for event in monitor.snapshot().diagnostics
    )

    keep = next(action for action in recovered_event.actions if action.action_id == "keep")
    assert monitor.diagnostic_center.dispatch_action(recovered_event, keep)
    assert monitor.snapshot().recovered_session is False
    assert not recovery.exists()
    assert parameter_session.load_state.provenance == "primary"
    kept = read_param_store(primary).store.get_state(key)
    assert kept is not None
    assert kept.ui_value == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("action_id", "expected_value"),
    (("keep", 0.9), ("discard", 0.25)),
)
def test_parameter_session_adopts_recovery_decision_load_state(
    tmp_path: Path,
    action_id: str,
    expected_value: float,
) -> None:
    primary = tmp_path / "store.json"
    primary_store = ParamStore()
    key = ParameterKey(op="circle", site_id="site", arg="radius")
    merge_frame_params(
        primary_store,
        (
            FrameParamRecord(
                key=key,
                base=0.25,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.25,
                source="code",
                explicit=True,
            ),
        ),
    )
    write_param_store(primary_store, primary)
    store, key, autosave = _session_with_dirty_explicit_override(primary)
    autosave.flush()
    session = parameter_session_module.ParameterSession(
        primary_path=primary,
        gui_enabled=False,
        known_operations=_EMPTY_KNOWN_OPERATIONS,
    )
    assert session.load_state.provenance == "session_recovery"
    assert session.capture_state().source == "recovery"
    provenance = CaptureProvenanceBuilder(
        lambda _t: None,
        config=runtime_config(),
        parameter_state=session.capture_state,
        parameter_store_path=primary,
    )
    recovered_frame = provenance.frame(
        session.store,
        t=0.0,
        frame_index=0,
        quality="final",
        origin="interactive",
    )
    monitor = RuntimeMonitor()
    recovery = session.install_diagnostic_actions(monitor)
    assert recovery is not None
    event = next(
        item
        for item in monitor.snapshot().diagnostics
        if item.summary == "Recovered session"
    )
    action = next(item for item in event.actions if item.action_id == action_id)

    assert monitor.diagnostic_center.dispatch_action(event, action)

    assert session.load_state.provenance == "primary"
    assert session.load_state.diagnostics == ()
    assert session.capture_state().source == "saved"
    state = session.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(expected_value)
    decided_frame = provenance.frame(
        session.store,
        t=1.0,
        frame_index=1,
        quality="final",
        origin="interactive",
    )
    assert recovered_frame.session.parameter_load_provenance == "session_recovery"
    assert recovered_frame.session.parameter_source == "recovery"
    assert decided_frame.session.parameter_load_provenance == (
        session.load_state.provenance
    )
    assert decided_frame.session.parameter_source == "saved"
    assert decided_frame.frame.parameters.revision == session.store.revision


def test_recovery_keep_samples_schema_when_action_is_dispatched(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "store.json"
    session, radius, new_arg = _parameter_session_with_pending_recovery(
        primary,
        include_obsolete=True,
    )
    assert new_arg is not None
    monitor, event, action = _recovery_action(session, "keep")

    accepted_schema = KnownOperationSchemaSnapshot(
        {"circle": frozenset({"obsolete"})}
    )
    session.replace_known_operations(accepted_schema)

    assert monitor.diagnostic_center.dispatch_action(event, action)
    assert session.load_state.provenance == "primary"
    assert session.store.get_state(radius) is None
    assert session.store.get_state(new_arg) is not None
    persisted = read_param_store(primary).store
    assert persisted.get_state(radius) is None
    assert persisted.get_state(new_arg) is not None


def test_parameter_session_keep_write_failure_preserves_one_recovery_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = tmp_path / "store.json"
    recovery_path = param_store_recovery_path(primary)
    session, key, obsolete = _parameter_session_with_pending_recovery(
        primary,
        include_obsolete=True,
    )
    assert obsolete is not None
    load_state_before = session.load_state
    primary_before = primary.read_bytes()
    recovery_before = recovery_path.read_bytes()
    monitor, event, action = _recovery_action(session, "keep")

    def fail_primary_write(_store: ParamStore, _path: Path) -> None:
        raise OSError("primary unavailable")

    monkeypatch.setattr(
        parameter_storage_module,
        "write_param_store",
        fail_primary_write,
    )

    assert monitor.diagnostic_center.dispatch_action(event, action) is False

    assert session.load_state is load_state_before
    assert session.load_state.provenance == "session_recovery"
    assert session.store.get_state(obsolete) is not None
    state = session.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.9)
    assert primary.read_bytes() == primary_before
    assert recovery_path.read_bytes() == recovery_before


def test_parameter_session_keep_unlink_failure_adopts_committed_primary_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = tmp_path / "store.json"
    recovery_path = param_store_recovery_path(primary)
    session, key, _obsolete = _parameter_session_with_pending_recovery(primary)
    monitor, event, action = _recovery_action(session, "keep")

    def fail_recovery_cleanup(_path: Path) -> None:
        raise OSError("recovery unlink failed")

    monkeypatch.setattr(
        parameter_storage_module,
        "discard_param_store_recovery",
        fail_recovery_cleanup,
    )

    assert monitor.diagnostic_center.dispatch_action(event, action) is True

    assert session.load_state.provenance == "primary"
    state = session.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.9)
    assert recovery_path.exists()
    restarted = recover_param_store_session(primary)
    assert restarted.load_state.provenance == "primary"
    restarted_state = restarted.store.get_state(key)
    assert restarted_state is not None
    assert restarted_state.ui_value == pytest.approx(0.9)


def test_parameter_session_discard_unlink_failure_preserves_one_recovery_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary = tmp_path / "store.json"
    recovery_path = param_store_recovery_path(primary)
    session, key, _obsolete = _parameter_session_with_pending_recovery(primary)
    load_state_before = session.load_state
    primary_before = primary.read_bytes()
    recovery_before = recovery_path.read_bytes()
    monitor, event, action = _recovery_action(session, "discard")

    def fail_recovery_cleanup(_path: Path) -> None:
        raise OSError("recovery unlink failed")

    monkeypatch.setattr(
        parameter_recovery_module,
        "discard_param_store_recovery",
        fail_recovery_cleanup,
    )

    assert monitor.diagnostic_center.dispatch_action(event, action) is False

    assert session.load_state is load_state_before
    assert session.load_state.provenance == "session_recovery"
    state = session.store.get_state(key)
    assert state is not None
    assert state.ui_value == pytest.approx(0.9)
    assert primary.read_bytes() == primary_before
    assert recovery_path.read_bytes() == recovery_before


def test_retry_action_retries_autosave_and_clears_failure(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "store.json"
    store, _key, autosave = _session_with_dirty_explicit_override(primary)
    attempts = 0

    def flaky_save(current: ParamStore, path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("disk full")
        write_param_store_recovery(current, path)

    autosave._save = flaky_save
    with pytest.raises(OSError, match="disk full"):
        autosave.flush()

    monitor = RuntimeMonitor()
    monitor.set_autosave(
        status=autosave.status,
        error=autosave.last_error,
        source=str(autosave.path),
    )
    session = parameter_session_module.ParameterSession(
        primary_path=None,
        gui_enabled=False,
        known_operations=_EMPTY_KNOWN_OPERATIONS,
    )
    session.autosave = autosave
    session.install_diagnostic_actions(
        monitor,
        open_source=lambda _source: None,
    )
    failed = next(event for event in monitor.snapshot().diagnostics if event.category == "save")
    retry = next(action for action in failed.actions if action.action_id == "retry")

    assert monitor.diagnostic_center.dispatch_action(failed, retry)
    assert attempts == 2
    assert monitor.snapshot().autosave_status == "clean"
    assert failed not in monitor.snapshot().diagnostics


def test_shutdown_save_failure_is_published_to_shared_center(tmp_path: Path) -> None:
    primary = tmp_path / "store.json"
    store, _key, autosave = _session_with_dirty_explicit_override(primary)

    def fail_save(_store: ParamStore, _path: Path) -> None:
        raise OSError("read-only volume")

    autosave._save = fail_save
    monitor = RuntimeMonitor()

    with pytest.raises(OSError, match="read-only volume"):
        parameter_session_module._persist_param_store_on_shutdown(
            store=store,
            primary_path=primary,
            autosave=autosave,
            session_completed_cleanly=False,
            known_operations=_EMPTY_KNOWN_OPERATIONS,
            monitor=monitor,
        )

    diagnostic = monitor.snapshot().diagnostics[-1]
    assert diagnostic.category == "save"
    assert diagnostic.summary == "Parameter save failed during shutdown"
    assert "OSError: read-only volume" in diagnostic.details


def test_open_action_uses_runner_source_handler(tmp_path: Path) -> None:
    source = tmp_path / "sketch.py"
    source.write_text("pass\n", encoding="utf-8")
    opened: list[str] = []
    monitor = RuntimeMonitor()
    session = parameter_session_module.ParameterSession(
        primary_path=None,
        gui_enabled=False,
        known_operations=_EMPTY_KNOWN_OPERATIONS,
    )
    session.install_diagnostic_actions(
        monitor,
        open_source=opened.append,
    )
    action = DiagnosticAction("open", "Open source")
    event = monitor.publish_diagnostic(
        DiagnosticEvent(
            category="scene",
            severity="error",
            summary="draw failed",
            source=f"{source}:10",
            actions=(action,),
        )
    )

    assert monitor.diagnostic_center.dispatch_action(event, action)
    assert opened == [f"{source}:10"]


def test_diagnostic_source_path_accepts_file_line_suffix(tmp_path: Path) -> None:
    source = tmp_path / "sketch.py"
    source.write_text("pass\n", encoding="utf-8")

    assert parameter_session_module._diagnostic_source_path(f"{source}:42") == source.resolve()


def test_cleanup_steps_continue_after_failure_and_raise_the_first_error() -> None:
    calls: list[str] = []
    first_error = RuntimeError("GUI close failed")

    def fail_gui_close() -> None:
        calls.append("gui")
        raise first_error

    def close_draw_window() -> None:
        calls.append("draw")

    with pytest.raises(RuntimeError, match="GUI close failed") as exc_info:
        runner_module._run_cleanup_steps(
            [
                ("close GUI", fail_gui_close),
                ("close draw window", close_draw_window),
            ]
        )

    assert exc_info.value is first_error
    assert calls == ["gui", "draw"]


def test_cleanup_steps_preserve_the_session_error_over_cleanup_errors() -> None:
    calls: list[str] = []
    session_error = ValueError("draw loop failed")

    def fail_cleanup() -> None:
        calls.append("cleanup")
        raise RuntimeError("secondary cleanup failure")

    def final_cleanup() -> None:
        calls.append("final")

    with pytest.raises(ValueError, match="draw loop failed") as exc_info:
        runner_module._run_cleanup_steps(
            [
                ("failing cleanup", fail_cleanup),
                ("final cleanup", final_cleanup),
            ],
            initial_error=session_error,
        )

    assert exc_info.value is session_error
    assert calls == ["cleanup", "final"]


def test_cleanup_steps_catch_base_exceptions_and_keep_the_first_identity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    class CleanupFault(BaseException):
        pass

    first_error = CleanupFault("first")

    def fail_first() -> None:
        calls.append("first")
        raise first_error

    def fail_second() -> None:
        calls.append("second")
        raise CleanupFault("second")

    def finish() -> None:
        calls.append("finish")

    with pytest.raises(CleanupFault) as exc_info:
        runner_module._run_cleanup_steps(
            [
                ("first", fail_first),
                ("second", fail_second),
                ("finish", finish),
            ]
        )

    assert exc_info.value is first_error
    assert calls == ["first", "second", "finish"]
    logged = [record.getMessage() for record in caplog.records]
    assert all("first" not in message for message in logged)
    assert any("second" in message for message in logged)


def test_midi_save_failure_still_closes_the_owned_input_port() -> None:
    calls: list[str] = []
    save_error = RuntimeError("snapshot write failed")

    class Midi:
        def save(self) -> None:
            calls.append("save")
            raise save_error

        def close(self) -> None:
            calls.append("close")

    with pytest.raises(RuntimeError, match="snapshot write failed") as exc_info:
        shutdown_midi_controller(
            cast(Any, Midi()),
            on_snapshot_save_skipped=lambda _controller: None,
        )

    assert exc_info.value is save_error
    assert calls == ["save", "close"]


def test_midi_base_exception_still_closes_the_owned_input_port() -> None:
    calls: list[str] = []

    class CleanupFault(BaseException):
        pass

    save_error = CleanupFault("snapshot write aborted")

    class Midi:
        def save(self) -> None:
            calls.append("save")
            raise save_error

        def close(self) -> None:
            calls.append("close")

    with pytest.raises(CleanupFault) as exc_info:
        shutdown_midi_controller(
            cast(Any, Midi()),
            on_snapshot_save_skipped=lambda _controller: None,
        )

    assert exc_info.value is save_error
    assert calls == ["save", "close"]


def test_unowned_rejected_midi_snapshot_uses_shutdown_skip_policy(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    diagnostic = DiagnosticEvent(
        category="midi",
        severity="warning",
        summary="old MIDI snapshot",
    )

    class Midi:
        snapshot_load_result = CcSnapshotLoadResult(
            values=(),
            status="old",
            source=tmp_path / "snapshot.json",
            diagnostic=diagnostic,
        )

        def save(self) -> None:
            calls.append("save")
            raise CcSnapshotWriteBlockedError("blocked")

        def close(self) -> None:
            calls.append("close")

    midi = cast(Any, Midi())
    skipped: list[object] = []
    shutdown_midi_controller(
        midi,
        on_snapshot_save_skipped=skipped.append,
    )

    assert calls == ["save", "close"]
    assert skipped == [midi]


def test_acquisition_failure_closes_midi_created_before_draw_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Midi:
        def save(self) -> None:
            calls.append("save midi")

        def close(self) -> None:
            calls.append("close midi")

    midi = cast(Any, Midi())
    monkeypatch.setattr(
        midi_factory_module,
        "create_midi_controller",
        lambda **_kwargs: midi,
    )

    def fail_after_midi(**_kwargs: object) -> None:
        raise RuntimeError("frozen snapshot load failed")

    monkeypatch.setattr(
        midi_factory_module,
        "maybe_load_frozen_cc_snapshot",
        fail_after_midi,
    )

    with pytest.raises(RuntimeError, match="frozen snapshot load failed"):
        midi_factory_module.create_midi_session(
            port_name="auto",
            mode="7bit",
            snapshot_path=tmp_path / "midi.json",
        )

    assert calls == ["save midi", "close midi"]


def test_failure_after_draw_window_construction_runs_registered_closer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Midi:
        def __init__(self) -> None:
            self.snapshot_load_result = CcSnapshotLoadResult(
                values=(),
                status="missing",
                source=tmp_path / "midi.json",
            )

        def save(self) -> None:
            calls.append("save midi")

        def close(self) -> None:
            calls.append("close midi")

    midi = cast(Any, Midi())

    class Window:
        def get_requested_size(self) -> tuple[int, int]:
            return 800, 800

        def get_location(self) -> tuple[int, int]:
            return 0, 0

        def set_location(self, *_args: object) -> None:
            raise RuntimeError("window placement failed")

    class DrawWindow:
        window = Window()

        def __init__(self, *_args: object, **kwargs: object) -> None:
            calls.append("create draw")
            self.authoring_definitions = kwargs["definitions"]

        def close(self) -> None:
            calls.append("close draw")
            midi.save()
            midi.close()

    effective_config = runtime_config()
    monkeypatch.setattr(
        runner_module,
        "runtime_config_with_fallback",
        lambda _path: (effective_config, None),
    )
    monkeypatch.setattr(
        runner_module,
        "output_path_for_draw",
        lambda **_kwargs: tmp_path / "midi.json",
    )
    monkeypatch.setattr(
        runner_module,
        "default_param_store_path",
        lambda *_args, **_kwargs: tmp_path / "params.json",
    )
    monkeypatch.setattr(
        runner_module,
        "create_midi_session",
        lambda **_kwargs: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(runner_module, "DrawWindowSystem", DrawWindow)

    with pytest.raises(RuntimeError, match="window placement failed"):
        runner_module._run_interactive_application(
            lambda _t: None,
            parameter_gui=False,
            parameter_persistence=False,
        )

    assert calls == ["create draw", "close draw", "save midi", "close midi"]


def test_gui_construction_failure_closes_completed_draw_system_and_midi_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Midi:
        def __init__(self) -> None:
            self.snapshot_load_result = CcSnapshotLoadResult(
                values=(),
                status="missing",
                source=tmp_path / "midi.json",
            )

        def save(self) -> None:
            calls.append("save midi")

        def close(self) -> None:
            calls.append("close midi")

    midi = cast(Any, Midi())

    class Window:
        def get_requested_size(self) -> tuple[int, int]:
            return 800, 800

        def get_location(self) -> tuple[int, int]:
            return 0, 0

        def set_location(self, *_args: object) -> None:
            calls.append("place draw")

    class DrawWindow:
        window = Window()
        transport = object()
        is_recording = False
        capture_service = object()

        def __init__(self, *_args: object, **kwargs: object) -> None:
            calls.append("create draw")
            self.authoring_definitions = kwargs["definitions"]

        def close(self) -> None:
            calls.append("close draw")
            midi.save()
            midi.close()

        def draw_frame(self) -> None:
            pass

        def final_capture_frame(self) -> None:
            return None

        def record_parameter_revision_created(
            self,
            _revision: int,
            _timestamp_ns: int,
            _domain: str,
        ) -> None:
            return None

    effective_config = runtime_config()

    class FailedGUI:
        def __init__(self, **_kwargs: object) -> None:
            assert callable(_kwargs["variation_thumbnail_capture"])
            assert _kwargs["effective_config"] is effective_config
            calls.append("create gui")
            raise RuntimeError("GUI construction failed")

    monkeypatch.setattr(
        runner_module,
        "runtime_config_with_fallback",
        lambda _path: (effective_config, None),
    )
    monkeypatch.setattr(
        runner_module,
        "output_path_for_draw",
        lambda **_kwargs: tmp_path / "midi.json",
    )
    monkeypatch.setattr(
        runner_module,
        "default_param_store_path",
        lambda *_args, **_kwargs: tmp_path / "params.json",
    )
    monkeypatch.setattr(
        runner_module,
        "create_midi_session",
        lambda **_kwargs: SimpleNamespace(close=lambda: None),
    )
    monkeypatch.setattr(runner_module, "DrawWindowSystem", DrawWindow)
    monkeypatch.setattr(gui_system_module, "ParameterGUIWindowSystem", FailedGUI)

    with pytest.raises(RuntimeError, match="GUI construction failed"):
        runner_module._run_interactive_application(
            lambda _t: None,
            parameter_gui=True,
            parameter_persistence=False,
        )

    assert calls == [
        "create draw",
        "place draw",
        "create gui",
        "close draw",
        "save midi",
        "close midi",
    ]
