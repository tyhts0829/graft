"""ParamStore session recovery を診断 action から解決する。"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from grafix.core.parameters.codec import dumps_param_store
from grafix.core.parameters.known_operations import KnownOperationSchemaSnapshot
from grafix.core.parameters.runtime import (
    ParameterLoadState,
    ParamStoreLoadDiagnostic,
)
from grafix.core.parameters.store import ParamStore
from grafix.interactive.diagnostics import DiagnosticAction, DiagnosticEvent
from grafix.parameter_storage import (
    discard_param_store_recovery,
    finalize_parameter_session,
    ParamStoreLoadResult,
    param_store_recovery_path,
    read_param_store,
    recover_primary_param_store,
)


def param_store_load_diagnostic_events(
    diagnostics: tuple[ParamStoreLoadDiagnostic, ...],
    *,
    primary_path: Path,
) -> tuple[DiagnosticEvent, ...]:
    """ParamStore load 時の recovery/quarantine 情報を共通診断へ変換する。"""

    events: list[DiagnosticEvent] = []
    for item in diagnostics:
        source = item.backup_path if item.backup_path is not None else primary_path
        actions: list[DiagnosticAction] = []
        if item.details:
            actions.append(DiagnosticAction("copy", "Copy details"))
        if item.backup_path is not None:
            actions.append(DiagnosticAction("open", "Open backup"))
        events.append(
            DiagnosticEvent(
                category="recovery",
                severity="warning",
                summary=item.summary,
                details=item.details,
                source=str(source),
                actions=tuple(actions),
                dedupe_key=f"param-load:{item.code}:{source}",
            )
        )
    return tuple(events)


def recovered_session_diagnostic(primary_path: Path) -> DiagnosticEvent:
    """未完了 session を復元したことと判断 action を表す。"""

    recovery_path = param_store_recovery_path(primary_path)
    return DiagnosticEvent(
        category="recovery",
        severity="warning",
        summary="Recovered session",
        details=(
            "Grafix restored parameter changes from an unfinished session. "
            "Keep accepts them, Discard restores the primary save, and Compare "
            "shows the current difference."
        ),
        source=str(recovery_path),
        actions=(
            DiagnosticAction("keep", "Keep"),
            DiagnosticAction("discard", "Discard"),
            DiagnosticAction("compare", "Compare"),
        ),
        dedupe_key=f"recovered-session:{recovery_path}",
    )


@dataclass(slots=True)
class ParamStoreRecoverySession:
    """現在 store と primary/recovery file の判断操作を所有する。"""

    store: ParamStore
    primary_path: Path
    known_operations: KnownOperationSchemaSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.store, ParamStore):
            raise TypeError("store は ParamStore である必要があります")
        if not isinstance(self.primary_path, Path):
            raise TypeError("primary_path は Path である必要があります")
        if type(self.known_operations) is not KnownOperationSchemaSnapshot:
            raise TypeError(
                "known_operations は exact KnownOperationSchemaSnapshot である必要があります"
            )

    @property
    def recovery_path(self) -> Path:
        return param_store_recovery_path(self.primary_path)

    def keep(self) -> ParamStoreLoadResult:
        """復元済みの現在状態を primary として確定する。"""

        candidate = ParamStore()
        candidate.replace_contents_from(self.store)
        finalize_parameter_session(
            candidate,
            self.primary_path,
            known_operations=self.known_operations,
        )
        return ParamStoreLoadResult(
            store=candidate,
            status="loaded",
            load_state=ParameterLoadState(),
        )

    def discard(self) -> ParamStoreLoadResult:
        """primary の detached result を作り、recovery journal を破棄する。"""

        loaded = recover_primary_param_store(self.primary_path)
        discard_param_store_recovery(self.primary_path)
        return loaded

    def compare_diagnostic(self) -> DiagnosticEvent:
        """primary と現在の recovered state の unified diff 診断を返す。"""

        primary = read_param_store(self.primary_path).store
        primary_text = dumps_param_store(primary).splitlines(keepends=True)
        recovered_text = dumps_param_store(
            self.store,
            preserve_explicit_overrides=True,
        ).splitlines(keepends=True)
        details = "".join(
            difflib.unified_diff(
                primary_text,
                recovered_text,
                fromfile=str(self.primary_path),
                tofile=str(self.recovery_path),
            )
        )
        if not details:
            details = "Primary and recovered parameter states are identical."
        return DiagnosticEvent(
            category="recovery",
            severity="info",
            summary="Recovered session comparison",
            details=details,
            source=str(self.recovery_path),
            actions=(DiagnosticAction("copy", "Copy comparison"),),
            dedupe_key=f"recovery-compare:{self.recovery_path}:{self.store.revision}",
        )


__all__ = [
    "ParamStoreRecoverySession",
    "param_store_load_diagnostic_events",
    "recovered_session_diagnostic",
]
