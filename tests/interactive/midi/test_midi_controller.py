"""interactive.midi.midi_controller をテスト。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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


def _controller(*, tmp_dir: Path, mode: str) -> MidiController:
    return MidiController(
        "Dummy Port",
        mode=mode,
        profile_name="test_profile",
        save_dir=tmp_dir,
        inport=DummyInPort([]),
    )


def test_update_7bit_normalizes_to_0_1(tmp_path: Path) -> None:
    ctrl = _controller(tmp_dir=tmp_path, mode="7bit")

    assert ctrl.update(DummyCcMsg(type="control_change", control=64, value=0)) is True
    assert ctrl.cc[64] == 0.0

    assert ctrl.update(DummyCcMsg(type="control_change", control=64, value=127)) is True
    assert ctrl.cc[64] == 1.0


def test_update_14bit_requires_msb_then_lsb(tmp_path: Path) -> None:
    ctrl = _controller(tmp_dir=tmp_path, mode="14bit")

    # LSB のみでは更新されない
    assert ctrl.update(DummyCcMsg(type="control_change", control=32, value=0)) is False
    assert 0 not in ctrl.cc

    # MSB を受け取る（まだ更新しない）
    assert ctrl.update(DummyCcMsg(type="control_change", control=0, value=127)) is False
    assert 0 not in ctrl.cc

    # LSB が来たら更新される（(127<<7)|127 == 16383）
    assert ctrl.update(DummyCcMsg(type="control_change", control=32, value=127)) is True
    assert ctrl.cc[0] == 1.0


def test_update_14bit_min_is_0(tmp_path: Path) -> None:
    ctrl = _controller(tmp_dir=tmp_path, mode="14bit")

    assert ctrl.update(DummyCcMsg(type="control_change", control=0, value=0)) is False
    assert ctrl.update(DummyCcMsg(type="control_change", control=32, value=0)) is True
    assert ctrl.cc[0] == 0.0


def test_poll_pending_counts_updates(tmp_path: Path) -> None:
    inport = DummyInPort(
        [
            DummyCcMsg(type="control_change", control=1, value=127),
            DummyCcMsg(type="note_on", control=0, value=0),
            DummyCcMsg(type="control_change", control=2, value=0),
        ]
    )
    ctrl = MidiController(
        "Dummy Port",
        mode="7bit",
        profile_name="test_profile",
        save_dir=tmp_path,
        inport=inport,
    )

    assert ctrl.poll_pending() == 2
    assert ctrl.cc[1] == 1.0
    assert ctrl.cc[2] == 0.0


def test_save_load_roundtrip(tmp_path: Path) -> None:
    ctrl = _controller(tmp_dir=tmp_path, mode="7bit")
    ctrl.cc = {64: 0.5, 1: 1.0}
    ctrl.save()

    ctrl2 = _controller(tmp_dir=tmp_path, mode="7bit")
    assert ctrl2.cc == ctrl.cc

