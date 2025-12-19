"""MIDI CC の永続化（終了時保存→次回復元）をテスト。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grafix import cc
from grafix.core.parameters.context import parameter_context_from_snapshot
from grafix.interactive.midi.midi_controller import MidiController


@dataclass(frozen=True, slots=True)
class DummyCcMsg:
    type: str
    control: int
    value: int


class DummyInPort:
    def __init__(self, messages: list[object]) -> None:
        self._messages = list(messages)

    def iter_pending(self):
        out = list(self._messages)
        self._messages.clear()
        return out


def test_persistence_path_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "main.json"
    ctrl = MidiController(
        "Dummy Port",
        mode="7bit",
        persistence_path=path,
        inport=DummyInPort([]),
    )
    ctrl.cc = {1: 0.5, 2: 1.0}
    ctrl.save()

    ctrl2 = MidiController(
        "Dummy Port",
        mode="7bit",
        persistence_path=path,
        inport=DummyInPort([]),
    )
    assert ctrl2.cc == {1: 0.5, 2: 1.0}


def test_restored_values_are_visible_via_cc_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "main.json"
    ctrl = MidiController(
        "Dummy Port",
        mode="7bit",
        persistence_path=path,
        inport=DummyInPort([]),
    )
    ctrl.cc = {1: 0.25}
    ctrl.save()

    ctrl2 = MidiController(
        "Dummy Port",
        mode="7bit",
        persistence_path=path,
        inport=DummyInPort([DummyCcMsg(type="control_change", control=2, value=127)]),
    )
    ctrl2.poll_pending()

    with parameter_context_from_snapshot({}, cc_snapshot=ctrl2.snapshot()):
        assert cc[1] == 0.25
        assert cc[2] == 1.0
        assert cc[999] == 0.0

