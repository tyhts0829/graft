# どこで: `src/grafix/interactive/parameter_gui/midi_learn.py`。
# 何を: Parameter GUI の MIDI learn（cc_key 割当）状態を保持する。
# なぜ: GUI の行描画と永続状態（ParamStore）更新を分離しつつ、フレーム間の learn 状態を維持するため。

from __future__ import annotations

from dataclasses import dataclass, replace

from grafix.core.parameters.key import ParameterKey


@dataclass(frozen=True, slots=True)
class MidiLearnState:
    """MIDI learn の状態。

    Notes
    -----
    - Learn は同時に 1 件のみ。
    - active_component は vec3/rgb の成分（0/1/2）。scalar の場合は None。
    """

    active_target: ParameterKey | None = None
    active_component: int | None = None
    last_seen_cc_seq: int = 0


@dataclass(frozen=True, slots=True)
class MidiLearnCommand:
    """一つの scalar/vec3 component へ適用する CC 割当変更。"""

    component: int | None
    cc: int | None


@dataclass(frozen=True, slots=True)
class MidiLearnTransition:
    """MIDI learn 入力を一度進めた結果。"""

    state: MidiLearnState | None
    current_cc: int | None
    active: bool
    command: MidiLearnCommand | None = None


def transition_midi_learn(
    state: MidiLearnState | None,
    *,
    target: ParameterKey,
    component: int | None,
    current_cc: int | None,
    last_cc_change: tuple[int, int] | None,
    clicked: bool,
) -> MidiLearnTransition:
    """一つの MIDI button に対する受信/click transition を純粋に計算する。

    ``current_cc`` は scalar 値または vec3 の対象成分だけを受け取る。返された
    ``command`` を複合 ``cc_key`` へ反映する責務は renderer adapter に残す。
    """

    next_state = state
    next_cc = None if current_cc is None else int(current_cc)
    active = bool(
        next_state is not None
        and next_state.active_target == target
        and next_state.active_component == component
    )
    command: MidiLearnCommand | None = None

    if active and next_state is not None and last_cc_change is not None:
        sequence, learned_cc = last_cc_change
        if int(sequence) > int(next_state.last_seen_cc_seq):
            next_cc = int(learned_cc)
            next_state = replace(
                next_state,
                active_target=None,
                active_component=None,
                last_seen_cc_seq=int(sequence),
            )
            active = False
            command = MidiLearnCommand(component=component, cc=next_cc)

    if clicked:
        if next_state is not None and next_state.active_target is not None and not active:
            next_state = replace(
                next_state,
                active_target=None,
                active_component=None,
            )

        if active:
            if next_state is not None:
                next_state = replace(
                    next_state,
                    active_target=None,
                    active_component=None,
                )
            active = False
        elif next_cc is not None:
            next_cc = None
            command = MidiLearnCommand(component=component, cc=None)
        elif next_state is not None:
            next_state = replace(
                next_state,
                active_target=target,
                active_component=component,
                last_seen_cc_seq=(0 if last_cc_change is None else int(last_cc_change[0])),
            )
            active = True

    return MidiLearnTransition(
        state=next_state,
        current_cc=next_cc,
        active=active,
        command=command,
    )


__all__ = [
    "MidiLearnCommand",
    "MidiLearnState",
    "MidiLearnTransition",
    "transition_midi_learn",
]
