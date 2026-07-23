"""Parameter store の load、recovery、history、autosave、finalize session。"""

from __future__ import annotations

import logging
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from grafix.core.operation_catalog import OperationCatalog
from grafix.core.parameters import (
    KnownOperationSchemaSnapshot,
    ParameterCaptureState,
    ParameterLoadState,
    ParamSnapshotSlots,
    ParamStore,
    ParamStoreAutosave,
    ParamStoreHistory,
)
from grafix.core.parameters.source import ParameterLoadMode
from grafix.core.preset_catalog import PresetCatalog
from grafix.interactive.diagnostics import DiagnosticAction, DiagnosticEvent
from grafix.interactive.runtime.parameter_recovery import (
    ParamStoreRecoverySession,
    param_store_load_diagnostic_events,
    recovered_session_diagnostic,
)
from grafix.parameter_storage import (
    finalize_parameter_session,
    ParamStoreLoadResult,
    param_store_recovery_path,
    recover_param_store_session,
    write_param_store_recovery,
)

_logger = logging.getLogger(__name__)


def known_operation_schema_snapshot(
    operations: OperationCatalog,
    presets: PresetCatalog,
) -> KnownOperationSchemaSnapshot:
    """session catalog を parameter finalization 用の中立 snapshot へ射影する。"""

    if type(operations) is not OperationCatalog:
        raise TypeError("operations は exact OperationCatalog である必要があります")
    if type(presets) is not PresetCatalog:
        raise TypeError("presets は exact PresetCatalog である必要があります")
    args_by_op: dict[str, frozenset[str]] = {}
    for entry in operations.entries():
        args = frozenset(entry.schema.meta)
        previous = args_by_op.setdefault(entry.name, args)
        if previous != args:
            raise ValueError(
                f"operation {entry.name!r} は kind ごとに異なる parameter schema を持ちます"
            )
    for declaration in presets.declarations():
        args_by_op[declaration.display_op] = frozenset(declaration.schema.meta)
    return KnownOperationSchemaSnapshot(args_by_op)


def _persist_param_store_on_shutdown(
    *,
    store: ParamStore,
    primary_path: Path | None,
    autosave: ParamStoreAutosave | None,
    session_completed_cleanly: bool,
    known_operations: KnownOperationSchemaSnapshot,
    monitor: Any | None = None,
) -> None:
    """session 終了時に recovery を確定し、正常終了だけ primary へ昇格する。"""

    # まず live override 付き recovery を確定する。以下の primary
    # finalize 中に障害が起き、未完了になっても復帰できる。
    try:
        if autosave is not None:
            autosave.flush()
        # code-first の primary へ確定し recovery を消すのは、
        # event loop が正常に制御を返した場合だけ。例外終了では
        # recovery を残し、次回起動時に live override を戻せるようにする。
        if primary_path is not None and session_completed_cleanly:
            finalize_parameter_session(
                store,
                primary_path,
                known_operations=known_operations,
            )
    except Exception as exc:
        if monitor is not None:
            source = autosave.path if autosave is not None else primary_path
            monitor.publish_diagnostic(
                DiagnosticEvent(
                    category="save",
                    severity="error",
                    summary="Parameter save failed during shutdown",
                    details="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    source=None if source is None else str(source),
                    actions=(DiagnosticAction("copy", "Copy details"),),
                    dedupe_key=f"parameter-shutdown-save:{type(exc).__name__}:{exc}",
                )
            )
        raise


def _diagnostic_source_path(source: str) -> Path:
    """`path:line` または path の診断 source を既存 file として解決する。"""

    raw = str(source).strip()
    path_text, separator, line_text = raw.rpartition(":")
    if separator and line_text.isdigit() and path_text:
        raw = path_text
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Diagnostic source does not exist: {path}")
    return path.resolve()


def _open_diagnostic_source(source: str) -> None:
    """診断 source を platform の既定 application で開く。"""

    path = _diagnostic_source_path(source)
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(  # noqa: S603 -- validated local file without shell expansion.
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class ParameterSession:
    """一 interactive session の parameter state と永続化 resource を所有する。"""

    def __init__(
        self,
        *,
        primary_path: Path | None,
        gui_enabled: bool,
        known_operations: KnownOperationSchemaSnapshot,
    ) -> None:
        if type(known_operations) is not KnownOperationSchemaSnapshot:
            raise TypeError(
                "known_operations は exact KnownOperationSchemaSnapshot である必要があります"
            )
        self.primary_path = primary_path
        self.known_operations = known_operations
        if primary_path is None:
            self.store = ParamStore()
            self._load_state = ParameterLoadState()
        else:
            loaded = recover_param_store_session(primary_path)
            self.store = loaded.store
            self._load_state = loaded.load_state
        self.history = ParamStoreHistory(self.store) if gui_enabled else None
        self.snapshot_slots = ParamSnapshotSlots(self.store) if gui_enabled else None
        self.autosave = (
            ParamStoreAutosave(
                self.store,
                param_store_recovery_path(primary_path),
                save=write_param_store_recovery,
            )
            if primary_path is not None
            else None
        )

    def replace_known_operations(
        self,
        known_operations: KnownOperationSchemaSnapshot,
    ) -> None:
        """成功した authoring generation の parameter schema へ置き換える。

        source reload の candidate が失敗した場合は呼ばれない。したがって終了時の
        prune/finalize は、常に最後に採用された immutable catalog generation を使う。
        GUI の有無には依存しない。
        """

        if type(known_operations) is not KnownOperationSchemaSnapshot:
            raise TypeError(
                "known_operations は exact KnownOperationSchemaSnapshot である必要があります"
            )
        self.known_operations = known_operations

    @property
    def load_state(self) -> ParameterLoadState:
        """現在の session が採用している load metadata を返す。"""

        return self._load_state

    def _adopt_load_result(self, loaded: ParamStoreLoadResult) -> None:
        """recovery 判断後の detached contents と load state を一度に採用する。"""

        if type(loaded) is not ParamStoreLoadResult:
            raise TypeError("loaded は exact ParamStoreLoadResult である必要があります")
        if loaded.store is self.store:
            raise ValueError("load result は detached ParamStore を指す必要があります")
        self.store.replace_contents_from(loaded.store)
        self._load_state = loaded.load_state

    def capture_state(self) -> ParameterCaptureState:
        """同じ load state sample から capture 用 source/provenance pair を返す。"""

        load_state = self._load_state
        source: ParameterLoadMode
        if self.primary_path is None:
            source = "code"
        elif load_state.provenance == "session_recovery":
            source = "recovery"
        else:
            source = "saved"
        return ParameterCaptureState(
            source=source,
            load_provenance=load_state.provenance,
        )

    def install_diagnostic_actions(
        self,
        monitor: Any,
        *,
        open_source: Callable[[str], None] = _open_diagnostic_source,
    ) -> ParamStoreRecoverySession | None:
        """save/recovery/open action と既存 load diagnostics を monitor へ配線する。"""

        center = monitor.diagnostic_center

        def open_event(event: DiagnosticEvent) -> None:
            if event.source is None:
                raise ValueError("Diagnostic has no source to open")
            open_source(event.source)

        center.register_action("open", open_event)

        autosave = self.autosave
        if autosave is not None:

            def retry_autosave(event: DiagnosticEvent) -> None:
                try:
                    autosave.flush()
                finally:
                    monitor.set_autosave(
                        status=autosave.status,
                        error=autosave.last_error,
                        source=str(autosave.path),
                    )
                center.dismiss(event)

            center.register_action("retry", retry_autosave, category="save")

        primary_path = self.primary_path
        if primary_path is None:
            return None

        load_state = self._load_state
        for event in param_store_load_diagnostic_events(
            load_state.diagnostics,
            primary_path=primary_path,
        ):
            monitor.publish_diagnostic(event)

        if load_state.provenance != "session_recovery":
            return None

        recovery = ParamStoreRecoverySession(self.store, primary_path)
        monitor.publish_diagnostic(recovered_session_diagnostic(primary_path))
        monitor.set_recovered_session(True)

        def finish_decision(event: DiagnosticEvent) -> None:
            if autosave is not None:
                autosave.mark_clean()
                monitor.set_autosave(
                    status=autosave.status,
                    error=autosave.last_error,
                    source=str(autosave.path),
                )
            if self.history is not None:
                self.history.clear()
            if self.snapshot_slots is not None:
                self.snapshot_slots.clear()
            monitor.set_recovered_session(False)
            center.dismiss(event)

        def keep(event: DiagnosticEvent) -> None:
            # action の install 時ではなく dispatch 時の accepted schema を使う。
            known_operations = self.known_operations
            self._adopt_load_result(
                recovery.keep(known_operations=known_operations)
            )
            finish_decision(event)

        def discard(event: DiagnosticEvent) -> None:
            loaded = recovery.discard()
            self._adopt_load_result(loaded)
            finish_decision(event)
            for diagnostic in param_store_load_diagnostic_events(
                loaded.load_state.diagnostics,
                primary_path=primary_path,
            ):
                monitor.publish_diagnostic(diagnostic)

        def compare(_event: DiagnosticEvent) -> None:
            monitor.publish_diagnostic(recovery.compare_diagnostic())

        center.register_action("keep", keep, category="recovery")
        center.register_action("discard", discard, category="recovery")
        center.register_action("compare", compare, category="recovery")
        return recovery

    def persist(
        self,
        *,
        session_completed_cleanly: bool,
        monitor: Any | None,
    ) -> None:
        """終了状態に応じ recovery flush または primary finalize を行う。"""

        _persist_param_store_on_shutdown(
            store=self.store,
            primary_path=self.primary_path,
            autosave=self.autosave,
            session_completed_cleanly=session_completed_cleanly,
            known_operations=self.known_operations,
            monitor=monitor,
        )


__all__ = ["ParameterSession", "known_operation_schema_snapshot"]
