"""
Purpose:
    runnerのMIDI設定を、live/frozen/disabled状態を持つMidiSession構築へ接続する。
Use when:
    port自動選択、MIDI mode、再接続factory、またはsnapshot path注入を変更する場合。
Constraints:
    - load/save/reconnectで使うexact snapshot pathをcallerから受け、ambient configで再構築しない。
    - backend/import errorを単なる未接続へ読み替えない。
Side effects:
    OSのMIDI portを列挙・openし、CC snapshotを読み得る。
"""

from __future__ import annotations

import logging
from pathlib import Path

from grafix.core.value_validation import (
    exact_string,
    exact_string_choice,
)
from grafix.interactive.diagnostics import DiagnosticCenter

from .midi_controller import (
    MidiController,
    _require_snapshot_path,
    maybe_load_frozen_cc_snapshot,
    save_cc_snapshot,
    shutdown_midi_controller,
)
from .session import MidiSession

# Runner/CLI 側の設定値で使う特別な文字列（自動接続の合図）。
_AUTO_MIDI_PORT = "auto"
_logger = logging.getLogger(__name__)


def create_midi_controller(
    *,
    port_name: str | None,
    mode: str,
    snapshot_path: Path,
    priority_inputs: tuple[tuple[str, str], ...] = (),
) -> MidiController | None:
    """設定値に従って `MidiController` を生成する。

    Parameters
    ----------
    port_name
        MIDI 入力ポート名。`None` なら MIDI 無効。`"auto"` なら利用可能な入力ポートから自動選択する。
    mode
        `"7bit"` または `"14bit"`（`MidiController` の `mode`）。
    snapshot_path
        composition root がこの session 用に一度だけ確定した永続化ファイルパス。
    priority_inputs
        `("port_name", "mode")` の候補リスト。`port_name="auto"` のときのみ参照する。

        - 先頭から順に「存在するポート + 指定 mode」で接続を試す。
        - 候補の `port_name` に `"auto"` を含めると「先頭ポート + その mode」を強制できる。
        - 候補が指定され、どれも利用できなければ MIDI は無効になる。
        - 候補を指定しない通常の ``"auto"`` は先頭ポートを使う。

    Returns
    -------
    MidiController | None
        接続できた場合は `MidiController`。MIDI 無効または自動接続に失敗した場合は `None`。

    Raises
    ------
    TypeError
        設定値が canonical な型でない場合。
    ValueError
        空名や未対応 mode を指定した場合。
    ImportError
        required dependency の `mido` を import できない場合。
    InvalidPortError
        明示指定した入力ポートが存在しない場合。

    Notes
    -----
    `MidiController` の生成時に、入力ポートの open と CC スナップショットの load が行われる。
    """

    mode_value = exact_string_choice(
        mode,
        name="mode",
        choices=("7bit", "14bit"),
    )
    path = _require_snapshot_path(snapshot_path)
    if type(priority_inputs) is not tuple:
        raise TypeError(
            "priority_inputs は (port_name, mode) の tuple である必要があります"
        )
    priorities: list[tuple[str, str]] = []
    for index, candidate in enumerate(priority_inputs):
        if type(candidate) is not tuple or len(candidate) != 2:
            raise TypeError(
                f"priority_inputs[{index}] は (port_name, mode) tuple である必要があります"
            )
        candidate_port = exact_string(
            candidate[0],
            name=f"priority_inputs[{index}].port_name",
        )
        if not candidate_port:
            raise ValueError(
                f"priority_inputs[{index}].port_name は空にできません"
            )
        candidate_mode = exact_string_choice(
            candidate[1],
            name=f"priority_inputs[{index}].mode",
            choices=("7bit", "14bit"),
        )
        priorities.append((candidate_port, candidate_mode))

    if port_name is None:
        # ユーザーが明示的に MIDI を無効化したケース。
        return None

    port = exact_string(port_name, name="port_name")
    if not port:
        raise ValueError("port_name は空にできません")

    if port == _AUTO_MIDI_PORT:
        import mido  # type: ignore

        # 以降で何度も参照するので一度 list 化して固定する。
        names = list(mido.get_input_names())  # type: ignore
        for candidate_port_name, candidate_mode in priorities:
            if candidate_port_name == _AUTO_MIDI_PORT:
                # "auto" を候補に含めることで「先頭ポートを、この mode で使う」を表現できる。
                if not names:
                    continue
                return MidiController(
                    names[0],
                    snapshot_path=path,
                    mode=candidate_mode,
                )
            if candidate_port_name in names:
                return MidiController(
                    candidate_port_name,
                    snapshot_path=path,
                    mode=candidate_mode,
                )

        if priorities or not names:
            return None
        return MidiController(
            names[0],
            snapshot_path=path,
            mode=mode_value,
        )

    return MidiController(
        port,
        snapshot_path=path,
        mode=mode_value,
    )


def create_midi_session(
    *,
    port_name: str | None,
    mode: str,
    snapshot_path: Path,
    priority_inputs: tuple[tuple[str, str], ...] = (),
    diagnostics: DiagnosticCenter | None = None,
) -> MidiSession:
    """controller/frozen snapshot/reconnect を一つの所有 session に組み立てる。"""

    path = _require_snapshot_path(snapshot_path)
    controller = create_midi_controller(
        port_name=port_name,
        mode=mode,
        snapshot_path=path,
        priority_inputs=priority_inputs,
    )
    try:
        frozen_result = maybe_load_frozen_cc_snapshot(
            port_name=port_name,
            controller=controller,
            snapshot_path=path,
        )

        def reconnect() -> MidiController | None:
            return create_midi_controller(
                port_name=port_name,
                mode=mode,
                snapshot_path=path,
                priority_inputs=priority_inputs,
            )

        snapshot_result = (
            controller.snapshot_load_result
            if controller is not None
            else frozen_result
        )
        return MidiSession(
            controller=controller,
            snapshot_load_result=snapshot_result,
            reconnect=None if port_name is None else reconnect,
            diagnostics=diagnostics,
            discard_persisted_snapshot=lambda: save_cc_snapshot({}, path),
        )
    except BaseException:
        if controller is not None:
            shutdown_midi_controller(
                controller,
                on_snapshot_save_skipped=lambda blocked: _logger.warning(
                    "MIDI CC snapshot auto-save skipped during acquisition: "
                    "status=%s, source=%s",
                    blocked.snapshot_load_result.status,
                    blocked.snapshot_load_result.source,
                ),
                report_secondary=lambda label: _logger.exception(
                    "MIDI acquisition cleanup failed after an earlier error: %s",
                    label,
                ),
            )
        raise
