"""interactive.midi.factory をテスト（mido 依存無し）。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import grafix.interactive.midi.factory as factory


class DummyMidiController:
    def __init__(
        self,
        port_name: str,
        *,
        mode: str = "7bit",
        profile_name: str | None = None,
        save_dir: Path | None = None,
    ) -> None:
        self.port_name = port_name
        self.mode = mode
        self.profile_name = profile_name
        self.save_dir = save_dir


def test_none_port_disables_midi() -> None:
    assert factory.create_midi_controller(
        port_name=None, mode="7bit", profile_name="main"
    ) is None


def test_auto_returns_none_when_mido_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mido", None)
    assert factory.create_midi_controller(
        port_name="auto", mode="7bit", profile_name="main"
    ) is None


def test_explicit_port_raises_when_mido_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "mido", None)
    with pytest.raises(RuntimeError):
        factory.create_midi_controller(
            port_name="TX-6 Bluetooth", mode="7bit", profile_name="main"
        )


def test_auto_uses_first_input_name(monkeypatch: pytest.MonkeyPatch) -> None:
    mido = types.ModuleType("mido")
    mido.get_input_names = lambda: ["P1", "P2"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mido", mido)
    monkeypatch.setattr(factory, "MidiController", DummyMidiController)

    ctrl = factory.create_midi_controller(
        port_name="auto", mode="14bit", profile_name="main"
    )
    assert ctrl is not None
    assert ctrl.port_name == "P1"
    assert ctrl.mode == "14bit"
    assert ctrl.profile_name == "main"


def test_explicit_port_creates_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mido", types.ModuleType("mido"))
    monkeypatch.setattr(factory, "MidiController", DummyMidiController)

    ctrl = factory.create_midi_controller(
        port_name="My Port", mode="7bit", profile_name="main"
    )
    assert ctrl is not None
    assert ctrl.port_name == "My Port"
    assert ctrl.mode == "7bit"
    assert ctrl.profile_name == "main"
