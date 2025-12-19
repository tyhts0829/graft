# どこで: `src/grafix/interactive/parameter_gui/midi_learn.py`。
# 何を: Parameter GUI の MIDI learn（cc_key 割当）状態を保持する。
# なぜ: GUI の行描画と永続状態（ParamStore）更新を分離しつつ、フレーム間の learn 状態を維持するため。

from __future__ import annotations

from dataclasses import dataclass

from grafix.core.parameters.key import ParameterKey


@dataclass(slots=True)
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

