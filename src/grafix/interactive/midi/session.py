"""
Purpose:
    MIDI controller、frozen CC値、接続診断を一つのruntime lifetimeへまとめる。
Use when:
    live/frozen/disabled遷移、再接続、frame snapshot、または終了時保存を変更する場合。
Constraints:
    - 接続失敗時も利用可能なlast-good frozen snapshotを明示stateとして保持する。
    - controller交換とdiagnostic更新をsession内で一貫させ、callerへmutable controller stateを渡さない。
Side effects:
    reconnect/close時にMIDI portとsnapshot persistenceを操作する。
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from typing import Literal

from grafix.core.parameters import MidiFrameSnapshot
from grafix.interactive.diagnostics import (
    DiagnosticAction,
    DiagnosticCenter,
    DiagnosticEvent,
)

from .midi_controller import (
    CcSnapshotLoadResult,
    MidiConnectionError,
    MidiController,
    _cc_number,
    shutdown_midi_controller,
)

MidiConnectionState = Literal["disabled", "live", "frozen"]
MidiReconnect = Callable[[], MidiController | None]

_logger = logging.getLogger(__name__)


class MidiSession:
    """MIDI入力と切断時のfrozen snapshotを所有する。

    Parameters
    ----------
    controller
        接続済みcontroller。未接続なら ``None``。
    snapshot_load_result
        controllerの起動結果、または未接続時に使う保存済みCC snapshot。
        ``None`` はMIDI無効を表す。
    reconnect
        再接続時に新しいcontrollerを返す関数。
    diagnostics
        切断・再接続失敗をpublishする共通診断center。
    discard_persisted_snapshot
        controllerが無い状態で永続snapshotだけを空v1へ置換するcallback。
    """

    def __init__(
        self,
        *,
        controller: MidiController | None,
        snapshot_load_result: CcSnapshotLoadResult | None,
        reconnect: MidiReconnect | None = None,
        diagnostics: DiagnosticCenter | None = None,
        discard_persisted_snapshot: Callable[[], None] | None = None,
    ) -> None:
        if snapshot_load_result is not None and not isinstance(
            snapshot_load_result,
            CcSnapshotLoadResult,
        ):
            raise TypeError(
                "snapshot_load_result は CcSnapshotLoadResult または None である必要があります"
            )
        if controller is not None:
            if snapshot_load_result is not controller.snapshot_load_result:
                raise ValueError(
                    "snapshot_load_result は controller の load result である必要があります"
                )
        elif (
            snapshot_load_result is not None
            and discard_persisted_snapshot is None
        ):
            raise ValueError(
                "controller の無い snapshot には discard_persisted_snapshot が必要です"
            )
        if (
            discard_persisted_snapshot is not None
            and not callable(discard_persisted_snapshot)
        ):
            raise TypeError("discard_persisted_snapshot は callable である必要があります")

        frozen_values = (
            None
            if snapshot_load_result is None
            else snapshot_load_result.as_dict()
        )

        self._controller = controller
        self._frozen_values = {} if controller is not None else frozen_values
        self._reconnect = reconnect
        self._diagnostics = diagnostics
        self._discard_persisted_snapshot = discard_persisted_snapshot
        self._last_error: str | None = None
        self._snapshot_load_result = snapshot_load_result
        self._snapshot_load_controller = controller
        self._frozen_save_controller: MidiController | None = None
        self._snapshot_diagnostic: DiagnosticEvent | None = None
        self._connection_diagnostic: DiagnosticEvent | None = None
        if (
            snapshot_load_result is not None
            and snapshot_load_result.diagnostic is not None
        ):
            self._snapshot_diagnostic = self._publish_diagnostic(
                snapshot_load_result.diagnostic
            )

    @property
    def controller(self) -> MidiController | None:
        """現在接続中のcontrollerを返す。"""

        return self._controller

    @property
    def state(self) -> MidiConnectionState:
        """現在の接続状態を返す。"""

        if self._controller is not None:
            return "live"
        if self._frozen_values is not None:
            return "frozen"
        return "disabled"

    @property
    def status_label(self) -> str:
        """toolbarへ常設表示する短い接続状態を返す。"""

        if self.state == "live":
            controller = self._controller
            assert controller is not None
            return f"MIDI LIVE {controller.port_name}"
        if self.state == "frozen":
            return "MIDI FROZEN"
        return "MIDI OFF"

    @property
    def last_error(self) -> str | None:
        """直近のpoll/reconnect errorを返す。"""

        return self._last_error

    @property
    def last_cc_change(self) -> tuple[int, int] | None:
        """接続中controllerの直近CC変更を返す。"""

        controller = self._controller
        return None if controller is None else controller.last_cc_change

    @property
    def can_reconnect(self) -> bool:
        """現在未接続で、再接続 factory がある場合だけ True を返す。"""

        return self._controller is None and self._reconnect is not None

    def value_for_cc(self, cc: int) -> float | None:
        """接続中controllerが保持するCC値をpollせずに返す。"""

        cc_number = _cc_number(cc, name="cc")
        controller = self._controller
        if controller is not None:
            return controller.cc.get(cc_number)
        frozen = self._frozen_values
        if frozen is None:
            return None
        return frozen.get(cc_number)

    def frame_snapshot(self) -> MidiFrameSnapshot | None:
        """pending入力を反映し、このframeで固定する値と由来を返す。"""

        controller = self._controller
        if controller is not None:
            try:
                controller.poll_pending()
                values = controller.snapshot()
            except MidiConnectionError as exc:
                self._freeze_after_error(controller, exc)
            else:
                self._frozen_values = dict(values)
                self._last_error = None
                return MidiFrameSnapshot.from_mapping(values, source="midi_live")

        frozen = self._frozen_values
        if frozen is None:
            return None
        return MidiFrameSnapshot.from_mapping(frozen, source="midi_frozen")

    def _replace_snapshot_load_result(
        self,
        result: CcSnapshotLoadResult,
        *,
        controller: MidiController,
    ) -> None:
        """接続成功時に永続 snapshot と診断の所有世代を交換する。"""

        if controller is not self._controller:
            raise ValueError("controller は現在接続中の instance である必要があります")
        if result is not controller.snapshot_load_result:
            raise ValueError("result は controller の load result である必要があります")

        self._dismiss_snapshot_diagnostic()
        self._snapshot_load_result = result
        self._snapshot_load_controller = controller
        if result.diagnostic is not None:
            self._snapshot_diagnostic = self._publish_diagnostic(result.diagnostic)

    def reconnect(self) -> bool:
        """新しいcontrollerへ再接続し、成功時にTrueを返す。"""

        if self._controller is not None:
            raise RuntimeError("live MIDI controller へは reconnect できません")
        factory = self._reconnect
        if factory is None:
            raise RuntimeError("MIDI reconnect は構成されていません")
        controller = factory()
        if controller is None:
            self._last_error = "MIDI input is unavailable"
            self._publish_reconnect_failure(self._last_error)
            return False
        self._controller = controller
        self._frozen_save_controller = None
        self._last_error = None
        self._dismiss_connection_diagnostic()
        self._replace_snapshot_load_result(
            controller.snapshot_load_result,
            controller=controller,
        )
        return True

    def retry_for_diagnostic(self, event: DiagnosticEvent) -> bool:
        """現在世代の connection 診断だけから再接続する。"""

        if not isinstance(event, DiagnosticEvent):
            raise TypeError("event は DiagnosticEvent である必要があります")
        if event is not self._connection_diagnostic or not self.can_reconnect:
            return False
        return self.reconnect()

    def clear_frozen_snapshot(self) -> None:
        """memory上と永続化先のfrozen値を消去する。"""

        if self._controller is not None:
            raise RuntimeError("live MIDI controller にfrozen snapshotはありません")

        result = self._snapshot_load_result
        if result is not None:
            snapshot_controller = self._snapshot_load_controller
            if snapshot_controller is not None:
                snapshot_controller.discard_persisted_snapshot()
                replacement = snapshot_controller.snapshot_load_result
                replacement_controller: MidiController | None = snapshot_controller
            else:
                discard = self._discard_persisted_snapshot
                assert discard is not None
                discard()
                replacement = CcSnapshotLoadResult(
                    values=(),
                    status="loaded",
                    source=result.source,
                )
                replacement_controller = None
            self._frozen_values = None
            self._dismiss_snapshot_diagnostic()
            self._snapshot_load_result = replacement
            self._snapshot_load_controller = replacement_controller
            self._frozen_save_controller = None
        self._dismiss_connection_diagnostic()

    def discard_for_diagnostic(self, event: DiagnosticEvent) -> bool:
        """現在世代の discard action だけを実行する。"""

        if not isinstance(event, DiagnosticEvent):
            raise TypeError("event は DiagnosticEvent である必要があります")
        if event is self._snapshot_diagnostic:
            result = self._snapshot_load_result
            assert result is not None
            controller = self._snapshot_load_controller
            if controller is not None:
                if result is not controller.snapshot_load_result:
                    return False
                current_controller = self._controller
                if current_controller is not None and current_controller is not controller:
                    return False
                controller.discard_persisted_snapshot()
                if current_controller is controller:
                    self._frozen_values = controller.snapshot()
                replacement = controller.snapshot_load_result
                replacement_controller = controller
            else:
                if self._controller is not None:
                    return False
                discard = self._discard_persisted_snapshot
                assert discard is not None
                discard()
                replacement = CcSnapshotLoadResult(
                    values=(),
                    status="loaded",
                    source=result.source,
                )
                replacement_controller = None
            self._dismiss_snapshot_diagnostic()
            self._snapshot_load_result = replacement
            self._snapshot_load_controller = replacement_controller
            return True

        if event is self._connection_diagnostic:
            if self._controller is not None:
                return False
            self.clear_frozen_snapshot()
            return True
        return False

    def close(self) -> None:
        """接続中controllerの値を保存し、入力portを閉じる。"""

        controller = (
            self._controller
            if self._controller is not None
            else self._frozen_save_controller
        )
        self._controller = None
        self._frozen_save_controller = None
        if controller is None:
            return
        shutdown_midi_controller(
            controller,
            on_snapshot_save_skipped=self._publish_snapshot_save_skipped,
            report_secondary=lambda label: _logger.exception(
                "MIDI shutdown cleanup failed after an earlier error: %s",
                label,
            ),
        )

    def _freeze_after_error(
        self,
        controller: MidiController,
        error: MidiConnectionError,
    ) -> None:
        """poll失敗をfrozen遷移へ変換し、診断をpublishする。"""

        self._frozen_values = controller.snapshot()
        self._controller = None
        self._frozen_save_controller = controller
        try:
            controller.close()
        except Exception:
            _logger.exception("Disconnected MIDI input port could not be closed")
        self._last_error = str(error)
        _logger.warning(
            "MIDI input disconnected; using frozen values: %s",
            error,
        )
        self._replace_connection_diagnostic(
            DiagnosticEvent(
                category="midi",
                severity="error",
                summary="MIDI input disconnected; using frozen values",
                details="".join(
                    traceback.format_exception(
                        type(error),
                        error,
                        error.__traceback__,
                    )
                ),
                source=controller.port_name,
                actions=(
                    DiagnosticAction("retry", "Reconnect"),
                    DiagnosticAction("discard", "Clear frozen snapshot"),
                ),
                dedupe_key="midi-poll-failed",
            )
        )

    def _publish_reconnect_failure(self, details: str) -> None:
        _logger.warning("MIDI reconnect failed: %s", details)
        self._replace_connection_diagnostic(
            DiagnosticEvent(
                category="midi",
                severity="error",
                summary="MIDI reconnect failed",
                details=details,
                actions=(
                    DiagnosticAction("retry", "Reconnect"),
                    DiagnosticAction("discard", "Clear frozen snapshot"),
                ),
                dedupe_key="midi-reconnect-failed",
            )
        )

    def _publish_diagnostic(self, event: DiagnosticEvent) -> DiagnosticEvent:
        """診断を共通面へ publish し、action identity 用の instance を返す。"""

        center = self._diagnostics
        if center is None:
            _logger.warning("%s: %s", event.summary, event.details)
            return event
        return center.publish(event)

    def _dismiss_snapshot_diagnostic(self) -> None:
        event = self._snapshot_diagnostic
        self._snapshot_diagnostic = None
        center = self._diagnostics
        if event is not None and center is not None:
            center.dismiss(event)

    def _replace_connection_diagnostic(self, event: DiagnosticEvent) -> None:
        self._dismiss_connection_diagnostic()
        self._connection_diagnostic = self._publish_diagnostic(event)

    def _dismiss_connection_diagnostic(self) -> None:
        event = self._connection_diagnostic
        self._connection_diagnostic = None
        center = self._diagnostics
        if event is not None and center is not None:
            center.dismiss(event)

    def _publish_snapshot_save_skipped(self, controller: MidiController) -> None:
        result = controller.snapshot_load_result
        self._publish_diagnostic(
            DiagnosticEvent(
                category="midi",
                severity="warning",
                summary="MIDI CC snapshot の自動保存をスキップしました",
                details=f"status={result.status}",
                source=str(result.source),
                dedupe_key=f"midi-snapshot-save-skipped:{result.source}",
            )
        )


__all__ = ["MidiConnectionState", "MidiReconnect", "MidiSession"]
