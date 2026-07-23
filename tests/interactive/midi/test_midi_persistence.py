"""MIDI CC の永続化（終了時保存→次回復元）をテスト。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from grafix import cc
from grafix.core.parameters.context import parameter_context_from_snapshot
from grafix.interactive.midi.midi_controller import MidiController
from grafix.core.parameters import MidiFrameSnapshot


@dataclass(frozen=True, slots=True)
class DummyCcMsg:
    type: str
    control: int
    value: int


class DummyInPort:
    def __init__(self, messages: list[object]) -> None:
        self._messages = list(messages)
        self.closed = False

    def iter_pending(self):
        out = list(self._messages)
        self._messages.clear()
        return out

    def close(self) -> None:
        self.closed = True


def test_snapshot_path_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "main.json"
    ctrl = MidiController(
        "Dummy Port",
        snapshot_path=path,
        mode="7bit",
        inport=DummyInPort([]),
    )
    ctrl.cc = {1: 0.5, 2: 1.0}
    ctrl.save()

    ctrl2 = MidiController(
        "Dummy Port",
        snapshot_path=path,
        mode="7bit",
        inport=DummyInPort([]),
    )
    assert ctrl2.cc == {1: 0.5, 2: 1.0}


def test_restored_values_are_visible_via_cc_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "main.json"
    ctrl = MidiController(
        "Dummy Port",
        snapshot_path=path,
        mode="7bit",
        inport=DummyInPort([]),
    )
    ctrl.cc = {1: 0.25}
    ctrl.save()

    ctrl2 = MidiController(
        "Dummy Port",
        snapshot_path=path,
        mode="7bit",
        inport=DummyInPort([DummyCcMsg(type="control_change", control=2, value=127)]),
    )
    ctrl2.poll_pending()

    snapshot = MidiFrameSnapshot.from_mapping(
        ctrl2.snapshot(),
        source="midi_live",
    )
    with parameter_context_from_snapshot({}, cc_snapshot=snapshot):
        assert cc[1] == 0.25
        assert cc[2] == 1.0
        with pytest.raises(ValueError, match="0\\.\\.127"):
            cc[999]
