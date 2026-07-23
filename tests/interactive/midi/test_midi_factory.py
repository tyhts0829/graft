"""interactive.midi.factory の明示 path と required mido 境界。"""

from __future__ import annotations

import inspect
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import grafix.interactive.midi.factory as factory
from grafix.interactive.midi.midi_controller import (
    CcSnapshotLoadResult,
    MidiController,
    load_cc_snapshot,
    load_frozen_cc_snapshot,
    maybe_load_frozen_cc_snapshot,
    save_cc_snapshot,
)


class _StringSubclass(str):
    pass


class DummyMidiController:
    def __init__(
        self,
        port_name: str,
        *,
        snapshot_path: Path,
        mode: str = "7bit",
    ) -> None:
        self.port_name = port_name
        self.mode = mode
        self.snapshot_path = snapshot_path


@pytest.mark.parametrize(
    "target",
    [
        MidiController,
        factory.create_midi_controller,
        factory.create_midi_session,
        load_cc_snapshot,
        load_frozen_cc_snapshot,
        maybe_load_frozen_cc_snapshot,
        save_cc_snapshot,
    ],
)
def test_midi_persistence_boundaries_require_only_explicit_snapshot_path(
    target: Callable[..., object],
) -> None:
    parameters = inspect.signature(target).parameters

    assert parameters["snapshot_path"].default is inspect.Parameter.empty
    assert not {"profile_name", "save_dir", "persistence_path"} & set(parameters)


def test_none_port_disables_midi(tmp_path: Path) -> None:
    assert (
        factory.create_midi_controller(
            port_name=None,
            mode="7bit",
            snapshot_path=tmp_path / "main.json",
        )
        is None
    )


def test_session_factory_owns_disabled_session(tmp_path: Path) -> None:
    session = factory.create_midi_session(
        port_name=None,
        mode="7bit",
        snapshot_path=tmp_path / "main.json",
    )

    assert session.state == "disabled"
    assert session.can_reconnect is False
    session.close()


def test_session_factory_transfers_live_controller_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "main.json"
    calls: list[str] = []
    received_paths: list[Path] = []

    class Controller:
        snapshot_load_result = CcSnapshotLoadResult(
            values=(),
            status="missing",
            source=path,
        )
        port_name = "P1"

        def save(self) -> None:
            calls.append("save")

        def close(self) -> None:
            calls.append("close")

    controller = Controller()

    def create_controller(**kwargs: object) -> Controller:
        received_paths.append(cast(Path, kwargs["snapshot_path"]))
        return controller

    monkeypatch.setattr(factory, "create_midi_controller", create_controller)

    session = factory.create_midi_session(
        port_name="P1",
        mode="7bit",
        snapshot_path=path,
    )

    assert session.state == "live"
    assert session.controller is controller
    assert received_paths == [path]
    assert received_paths[0] is path
    session.close()
    assert calls == ["save", "close"]


def test_reconnect_reuses_the_same_snapshot_path_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "main.json"
    received_paths: list[Path] = []

    class Controller:
        snapshot_load_result = CcSnapshotLoadResult(
            values=(),
            status="missing",
            source=path,
        )
        port_name = "P1"

        def poll_pending(self) -> int:
            return 0

        def snapshot(self) -> dict[int, float]:
            return {}

        def save(self) -> None:
            return None

        def close(self) -> None:
            return None

    results: list[Controller | None] = [None, Controller()]

    def create_controller(**kwargs: object) -> Controller | None:
        received_paths.append(cast(Path, kwargs["snapshot_path"]))
        return results.pop(0)

    monkeypatch.setattr(factory, "create_midi_controller", create_controller)

    session = factory.create_midi_session(
        port_name="P1",
        mode="7bit",
        snapshot_path=path,
    )

    assert session.state == "frozen"
    assert session.reconnect() is True
    assert session.state == "live"
    assert received_paths == [path, path]
    assert all(received is path for received in received_paths)
    session.close()


def test_frozen_discard_updates_only_the_injected_snapshot_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    save_cc_snapshot({1: 0.25}, first_path)
    save_cc_snapshot({2: 0.75}, second_path)
    monkeypatch.setattr(
        factory,
        "create_midi_controller",
        lambda **_kwargs: None,
    )

    first = factory.create_midi_session(
        port_name="P1",
        mode="7bit",
        snapshot_path=first_path,
    )
    second = factory.create_midi_session(
        port_name="P2",
        mode="7bit",
        snapshot_path=second_path,
    )

    assert first.value_for_cc(1) == 0.25
    assert second.value_for_cc(2) == 0.75
    first.clear_frozen_snapshot()
    assert load_cc_snapshot(first_path).as_dict() == {}
    assert load_cc_snapshot(second_path).as_dict() == {2: 0.75}
    assert second.value_for_cc(2) == 0.75
    first.close()
    second.close()


@pytest.mark.parametrize("mode", [7, b"7bit", _StringSubclass("7bit")])
def test_factory_rejects_non_exact_string_mode(
    tmp_path: Path,
    mode: object,
) -> None:
    with pytest.raises(TypeError, match="mode.*str"):
        factory.create_midi_controller(
            port_name=None,
            mode=mode,  # type: ignore[arg-type]
            snapshot_path=tmp_path / "main.json",
        )


def test_factory_rejects_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode"):
        factory.create_midi_controller(
            port_name=None,
            mode="16bit",
            snapshot_path=tmp_path / "main.json",
        )


@pytest.mark.parametrize(
    "factory_call",
    [
        pytest.param(factory.create_midi_controller, id="controller"),
        pytest.param(factory.create_midi_session, id="session"),
    ],
)
def test_factory_rejects_non_path_snapshot_path(
    factory_call: object,
) -> None:
    with pytest.raises(TypeError, match="snapshot_path.*Path"):
        factory_call(  # type: ignore[operator]
            port_name=None,
            mode="7bit",
            snapshot_path="main.json",
        )


def test_auto_propagates_missing_required_mido(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "mido", None)
    with pytest.raises(ModuleNotFoundError):
        factory.create_midi_controller(
            port_name="auto",
            mode="7bit",
            snapshot_path=tmp_path / "main.json",
        )


def test_explicit_port_propagates_missing_required_mido(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "mido", None)
    with pytest.raises(ModuleNotFoundError):
        factory.create_midi_controller(
            port_name="TX-6 Bluetooth",
            mode="7bit",
            snapshot_path=tmp_path / "main.json",
        )


def test_auto_propagates_mido_backend_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mido = types.ModuleType("mido")

    def fail() -> list[str]:
        raise RuntimeError("backend failed")

    mido.get_input_names = fail  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mido", mido)

    with pytest.raises(RuntimeError, match="backend failed"):
        factory.create_midi_controller(
            port_name="auto",
            mode="7bit",
            snapshot_path=tmp_path / "main.json",
        )


def test_auto_uses_first_input_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "main.json"
    mido = types.ModuleType("mido")
    mido.get_input_names = lambda: ["P1", "P2"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mido", mido)
    monkeypatch.setattr(factory, "MidiController", DummyMidiController)

    ctrl = factory.create_midi_controller(
        port_name="auto",
        mode="14bit",
        snapshot_path=path,
    )

    assert ctrl is not None
    assert ctrl.port_name == "P1"
    assert ctrl.mode == "14bit"
    assert ctrl.snapshot_path is path


def test_explicit_port_creates_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "main.json"
    monkeypatch.setitem(sys.modules, "mido", types.ModuleType("mido"))
    monkeypatch.setattr(factory, "MidiController", DummyMidiController)

    ctrl = factory.create_midi_controller(
        port_name="My Port",
        mode="7bit",
        snapshot_path=path,
    )

    assert ctrl is not None
    assert ctrl.port_name == "My Port"
    assert ctrl.mode == "7bit"
    assert ctrl.snapshot_path is path


def test_auto_uses_priority_inputs_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "main.json"
    mido = types.ModuleType("mido")
    mido.get_input_names = lambda: ["P1", "P2"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mido", mido)
    monkeypatch.setattr(factory, "MidiController", DummyMidiController)

    ctrl = factory.create_midi_controller(
        port_name="auto",
        mode="7bit",
        snapshot_path=path,
        priority_inputs=(
            ("Missing", "7bit"),
            ("P2", "14bit"),
        ),
    )

    assert ctrl is not None
    assert ctrl.port_name == "P2"
    assert ctrl.mode == "14bit"
    assert ctrl.snapshot_path is path


def test_auto_does_not_fall_back_when_explicit_priorities_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mido = types.ModuleType("mido")
    mido.get_input_names = lambda: ["P1", "P2"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mido", mido)
    monkeypatch.setattr(factory, "MidiController", DummyMidiController)

    assert (
        factory.create_midi_controller(
            port_name="auto",
            mode="7bit",
            snapshot_path=tmp_path / "main.json",
            priority_inputs=(("Missing", "14bit"),),
        )
        is None
    )


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"priority_inputs": []}, TypeError),
        ({"priority_inputs": (["P1", "7bit"],)}, TypeError),
        ({"priority_inputs": (("P1", "16bit"),)}, ValueError),
        ({"port_name": 1}, TypeError),
        ({"port_name": _StringSubclass("auto")}, TypeError),
        ({"port_name": ""}, ValueError),
    ],
)
def test_factory_rejects_noncanonical_connection_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, object],
    error: type[Exception],
) -> None:
    monkeypatch.setitem(sys.modules, "mido", types.ModuleType("mido"))
    values: dict[str, object] = {
        "port_name": "P1",
        "mode": "7bit",
        "snapshot_path": tmp_path / "main.json",
    }
    values.update(kwargs)
    with pytest.raises(error):
        factory.create_midi_controller(**values)  # type: ignore[arg-type]
