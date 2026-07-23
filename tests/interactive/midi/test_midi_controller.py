"""interactive.midi.midi_controller をテスト。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from grafix.interactive.midi import MidiSession
from grafix.interactive.midi.midi_controller import (
    MIDI_CC_SNAPSHOT_SCHEMA_VERSION,
    CcSnapshotLoadResult,
    CcSnapshotWriteBlockedError,
    MidiConnectionError,
    MidiController,
    load_cc_snapshot,
    load_frozen_cc_snapshot,
    maybe_load_frozen_cc_snapshot,
    save_cc_snapshot,
)
from grafix.interactive.diagnostics import DiagnosticCenter


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


class DisconnectingInPort:
    def __init__(self, message: object) -> None:
        self._message = message
        self.closed = False

    def iter_pending(self):
        yield self._message
        raise OSError("device disconnected")

    def close(self) -> None:
        self.closed = True


class _StringSubclass(str):
    pass


def _controller(*, tmp_dir: Path, mode: str) -> MidiController:
    return MidiController(
        "Dummy Port",
        snapshot_path=tmp_dir / "test_profile.json",
        mode=mode,
        inport=DummyInPort([]),
    )


@pytest.mark.parametrize("mode", [7, b"7bit", _StringSubclass("7bit")])
def test_controller_rejects_non_exact_string_mode(
    tmp_path: Path,
    mode: object,
) -> None:
    with pytest.raises(TypeError, match="mode.*str"):
        MidiController(
            "Dummy Port",
            snapshot_path=tmp_path / "test_profile.json",
            mode=mode,  # type: ignore[arg-type]
            inport=DummyInPort([]),
        )


def test_controller_rejects_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="mode"):
        MidiController(
            "Dummy Port",
            snapshot_path=tmp_path / "test_profile.json",
            mode="16bit",
            inport=DummyInPort([]),
        )


@pytest.mark.parametrize("port_name", [1, _StringSubclass("Dummy Port")])
def test_controller_rejects_non_exact_string_port_name(
    tmp_path: Path,
    port_name: object,
) -> None:
    with pytest.raises(TypeError, match="port_name.*str"):
        MidiController(
            port_name,  # type: ignore[arg-type]
            snapshot_path=tmp_path / "test_profile.json",
            inport=DummyInPort([]),
        )


def test_controller_rejects_non_path_snapshot_path() -> None:
    with pytest.raises(TypeError, match="snapshot_path.*Path"):
        MidiController(
            "Dummy Port",
            snapshot_path="snapshot.json",  # type: ignore[arg-type]
            inport=DummyInPort([]),
        )


def test_controller_rejects_empty_port_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="空にできません"):
        MidiController(
            "",
            snapshot_path=tmp_path / "test_profile.json",
            inport=DummyInPort([]),
        )


def test_controller_requires_complete_input_port_protocol(tmp_path: Path) -> None:
    class IncompletePort:
        def iter_pending(self) -> list[object]:
            return []

    with pytest.raises(TypeError, match="iter_pending.*close"):
        MidiController(
            "Dummy Port",
            snapshot_path=tmp_path / "test_profile.json",
            inport=IncompletePort(),  # type: ignore[arg-type]
        )


def test_controller_requires_callable_input_port_methods(tmp_path: Path) -> None:
    class InvalidPort:
        iter_pending = 1
        close = 2

    with pytest.raises(TypeError, match="iter_pending.*close"):
        MidiController(
            "Dummy Port",
            snapshot_path=tmp_path / "test_profile.json",
            inport=InvalidPort(),  # type: ignore[arg-type]
        )


def test_controller_closes_owned_input_port_once(tmp_path: Path) -> None:
    inport = DummyInPort([])
    controller = MidiController(
        "Dummy Port",
        snapshot_path=tmp_path / "test_profile.json",
        inport=inport,
    )

    controller.close()
    controller.close()

    assert inport.closed is True


def test_update_7bit_normalizes_to_0_1(tmp_path: Path) -> None:
    ctrl = _controller(tmp_dir=tmp_path, mode="7bit")

    assert ctrl.update(DummyCcMsg(type="control_change", control=64, value=0)) is True
    assert ctrl.cc[64] == 0.0

    assert ctrl.update(DummyCcMsg(type="control_change", control=64, value=127)) is True
    assert ctrl.cc[64] == 1.0


@pytest.mark.parametrize(
    ("control", "value"),
    [
        (True, 1),
        (1.0, 1),
        ("1", 1),
        (-1, 1),
        (128, 1),
        (1, True),
        (1, 1.0),
        (1, "1"),
        (1, -1),
        (1, 128),
    ],
)
def test_update_rejects_noncanonical_external_data_bytes_without_mutation(
    tmp_path: Path,
    control: object,
    value: object,
) -> None:
    controller = _controller(tmp_dir=tmp_path, mode="7bit")
    controller.cc = {7: 0.5}

    with pytest.raises((TypeError, ValueError)):
        controller.update(
            SimpleNamespace(
                type="control_change",
                control=control,
                value=value,
            )
        )
    assert controller.cc == {7: 0.5}
    assert controller.cc_change_seq == 0

    controller.save()
    assert load_cc_snapshot(controller.snapshot_path).as_dict() == {7: 0.5}


@pytest.mark.parametrize(
    "message",
    [
        SimpleNamespace(type="control_change", value=1),
        SimpleNamespace(type="control_change", control=1),
    ],
)
def test_update_rejects_missing_control_change_fields(
    tmp_path: Path,
    message: object,
) -> None:
    controller = _controller(tmp_dir=tmp_path, mode="7bit")

    with pytest.raises(AttributeError):
        controller.update(message)

    assert controller.cc == {}
    assert controller.cc_change_seq == 0


def test_update_ignores_only_non_control_change_messages(tmp_path: Path) -> None:
    controller = _controller(tmp_dir=tmp_path, mode="7bit")

    assert controller.update(SimpleNamespace(type="note_on")) is False
    assert controller.cc == {}


def test_update_rejects_missing_or_non_string_message_type(tmp_path: Path) -> None:
    controller = _controller(tmp_dir=tmp_path, mode="7bit")

    with pytest.raises(AttributeError):
        controller.update(SimpleNamespace())
    with pytest.raises(TypeError, match="message.type"):
        controller.update(SimpleNamespace(type=1))

    assert controller.cc == {}
    assert controller.cc_change_seq == 0


@pytest.mark.parametrize(
    ("control", "value"),
    [(True, 1), (1.0, 1), (-1, 1), (128, 1), (1, True), (1, 128)],
)
def test_update_cc_rejects_noncanonical_data_bytes_without_mutation(
    tmp_path: Path,
    control: object,
    value: object,
) -> None:
    controller = _controller(tmp_dir=tmp_path, mode="7bit")
    controller.cc = {7: 0.5}

    with pytest.raises((TypeError, ValueError)):
        controller.update_cc(
            control=control,  # type: ignore[arg-type]
            value=value,  # type: ignore[arg-type]
        )

    assert controller.cc == {7: 0.5}
    assert controller.cc_change_seq == 0


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
        snapshot_path=tmp_path / "test_profile.json",
        mode="7bit",
        inport=inport,
    )

    assert ctrl.poll_pending() == 2
    assert ctrl.cc[1] == 1.0
    assert ctrl.cc[2] == 0.0


def test_poll_pending_wraps_port_pending_acquisition_failure(
    tmp_path: Path,
) -> None:
    error = OSError("port disconnected")

    class FailingInPort:
        def iter_pending(self) -> list[object]:
            raise error

        def close(self) -> None:
            return None

    controller = MidiController(
        "Dummy Port",
        snapshot_path=tmp_path / "test_profile.json",
        inport=FailingInPort(),
    )

    with pytest.raises(MidiConnectionError) as exc_info:
        controller.poll_pending()

    assert exc_info.value.__cause__ is error


def test_poll_pending_wraps_iterator_failure_after_partial_update(
    tmp_path: Path,
) -> None:
    error = OSError("iterator disconnected")

    def pending_messages():
        yield DummyCcMsg(type="control_change", control=7, value=127)
        raise error

    class FailingInPort:
        def iter_pending(self):
            return pending_messages()

        def close(self) -> None:
            return None

    controller = MidiController(
        "Dummy Port",
        snapshot_path=tmp_path / "test_profile.json",
        inport=FailingInPort(),
    )

    with pytest.raises(MidiConnectionError) as exc_info:
        controller.poll_pending()

    assert exc_info.value.__cause__ is error
    assert controller.cc == {7: 1.0}


def test_poll_pending_does_not_reclassify_message_validation_error(
    tmp_path: Path,
) -> None:
    inport = DummyInPort(
        [SimpleNamespace(type="control_change", control=True, value=1)]
    )
    controller = MidiController(
        "Dummy Port",
        snapshot_path=tmp_path / "test_profile.json",
        inport=inport,
    )

    with pytest.raises(TypeError, match="message.control"):
        controller.poll_pending()

    assert controller.inport is inport
    assert inport.closed is False
    assert controller.cc == {}


def test_poll_pending_has_no_partial_drain_argument(tmp_path: Path) -> None:
    ctrl = _controller(tmp_dir=tmp_path, mode="7bit")

    with pytest.raises(TypeError, match="max_messages"):
        ctrl.poll_pending(max_messages=1)  # type: ignore[call-arg]


def test_snapshot_reload_is_not_a_public_controller_mutation_surface(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_dir=tmp_path, mode="7bit")

    assert not hasattr(controller, "load")


def test_save_load_roundtrip(tmp_path: Path) -> None:
    ctrl = _controller(tmp_dir=tmp_path, mode="7bit")
    ctrl.cc = {64: 0.5, 1: 1.0}
    ctrl.save()

    ctrl2 = _controller(tmp_dir=tmp_path, mode="7bit")
    assert ctrl2.cc == ctrl.cc

    payload = json.loads(ctrl.snapshot_path.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": MIDI_CC_SNAPSHOT_SCHEMA_VERSION,
        "values": [
            {"cc": 1, "value": 1.0},
            {"cc": 64, "value": 0.5},
        ],
    }


def test_controller_load_and_save_preserve_exact_injected_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nested" / "MIDI 名前 !.snapshot"
    save_cc_snapshot({3: 0.25}, path)

    controller = MidiController(
        "Dummy Port",
        snapshot_path=path,
        inport=DummyInPort([]),
    )

    assert controller.snapshot_path is path
    assert controller.snapshot_load_result.source is path
    assert controller.cc == {3: 0.25}
    controller.cc[4] = 0.75
    controller.save()
    assert load_cc_snapshot(path).as_dict() == {3: 0.25, 4: 0.75}


def test_missing_snapshot_is_the_only_empty_non_error_result(tmp_path: Path) -> None:
    result = load_cc_snapshot(tmp_path / "missing.json")

    assert result.status == "missing"
    assert result.values == ()
    assert result.diagnostic is None


def test_snapshot_load_result_requires_keyword_arguments(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        CcSnapshotLoadResult(  # type: ignore[misc]
            (),
            "missing",
            tmp_path / "missing.json",
        )


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"1": 0.25}, "old"),
        ({"schema_version": 0, "values": []}, "old"),
        ({"schema_version": 2, "values": []}, "future"),
        ([], "corrupt"),
        ({"schema_version": "1", "values": []}, "corrupt"),
        ({"schema_version": 1, "values": [], "extra": None}, "corrupt"),
        ({"schema_version": 1, "values": {}}, "corrupt"),
        (
            {
                "schema_version": 1,
                "values": [
                    {"cc": 1, "value": 0.25},
                    {"cc": 1, "value": 0.5},
                ],
            },
            "corrupt",
        ),
        (
            {
                "schema_version": 1,
                "values": [
                    {"cc": 2, "value": 0.25},
                    {"cc": 1, "value": 0.5},
                ],
            },
            "corrupt",
        ),
        ({"schema_version": 1, "values": [{"cc": True, "value": 0.25}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": "1", "value": 0.25}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 128, "value": 0.25}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": True}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": 0}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": 1}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": "0.25"}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": float("nan")}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": 10**1000}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": 1.1}]}, "corrupt"),
        ({"schema_version": 1, "values": [{"cc": 1, "value": 0.25, "extra": 1}]}, "corrupt"),
    ],
)
def test_snapshot_rejects_old_future_and_partially_invalid_payloads(
    tmp_path: Path,
    payload: object,
    status: str,
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = load_cc_snapshot(path)

    assert result.status == status
    assert result.values == ()
    assert result.diagnostic is not None
    assert result.diagnostic.category == "midi"
    assert [action.action_id for action in result.diagnostic.actions] == ["discard"]


@pytest.mark.parametrize("contents", [b"{", b"\xff"])
def test_snapshot_rejects_invalid_json_and_utf8(
    tmp_path: Path,
    contents: bytes,
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_bytes(contents)

    result = load_cc_snapshot(path)

    assert result.status == "corrupt"
    assert result.values == ()
    assert result.diagnostic is not None


@pytest.mark.parametrize(
    "payload",
    [
        '{"schema_version": 1, "schema_version": 1, "values": []}',
        (
            '{"schema_version": 1, "values": '
            '[{"cc": 1, "cc": 2, "value": 0.5}]}'
        ),
    ],
)
def test_snapshot_rejects_duplicate_json_object_keys(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(payload, encoding="utf-8")

    result = load_cc_snapshot(path)

    assert result.status == "corrupt"
    assert result.values == ()
    assert result.diagnostic is not None


@pytest.mark.parametrize(
    "payload",
    [
        "[" * 10_000 + "0" + "]" * 10_000,
        (
            '{"schema_version": 1, "values": [{"cc": 1, "value": '
            + "1" * 5_000
            + "}]}"
        ),
    ],
)
def test_snapshot_classifies_json_parser_limits_as_corrupt(
    tmp_path: Path,
    payload: str,
) -> None:
    path = tmp_path / "snapshot.json"
    path.write_text(payload, encoding="utf-8")

    result = load_cc_snapshot(path)

    assert result.status == "corrupt"
    assert result.values == ()
    assert result.diagnostic is not None


def test_snapshot_reports_io_error_instead_of_treating_it_as_missing(
    tmp_path: Path,
) -> None:
    result = load_cc_snapshot(tmp_path)

    assert result.status == "corrupt"
    assert result.values == ()
    assert result.diagnostic is not None


@pytest.mark.parametrize(
    "snapshot",
    [
        {True: 0.5},
        {128: 0.5},
        {1: True},
        {1: 0},
        {1: 1},
        {1: "0.5"},
        {1: float("inf")},
        {1: 10**1000},
        {1: -0.1},
    ],
)
def test_snapshot_writer_rejects_noncanonical_values(
    tmp_path: Path,
    snapshot: dict[object, object],
) -> None:
    with pytest.raises((TypeError, ValueError)):
        save_cc_snapshot(snapshot, tmp_path / "snapshot.json")  # type: ignore[arg-type]


def test_rejected_snapshot_is_not_overwritten_until_explicit_discard(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.json"
    original = '{"1": 0.25}\n'
    path.write_text(original, encoding="utf-8")
    controller = MidiController(
        "Dummy Port",
        snapshot_path=path,
        inport=DummyInPort([]),
    )

    assert controller.snapshot_load_result.status == "old"
    assert controller.cc == {}
    controller.cc = {2: 0.5}
    with pytest.raises(CcSnapshotWriteBlockedError, match="自動保存"):
        controller.save()
    assert path.read_text(encoding="utf-8") == original

    controller.discard_persisted_snapshot()
    assert controller.cc == {2: 0.5}
    assert load_cc_snapshot(path).as_dict() == {}
    controller.save()

    assert load_cc_snapshot(path).as_dict() == {2: 0.5}


def test_session_shutdown_preserves_rejected_snapshot_and_closes_port(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.json"
    original = '{"1": 0.25}\n'
    path.write_text(original, encoding="utf-8")
    inport = DummyInPort([])
    controller = MidiController(
        "Dummy Port",
        snapshot_path=path,
        inport=inport,
    )
    center = DiagnosticCenter()
    session = MidiSession(
        controller=controller,
        snapshot_load_result=controller.snapshot_load_result,
        diagnostics=center,
    )

    session.close()

    assert path.read_text(encoding="utf-8") == original
    assert inport.closed is True
    assert any("自動保存をスキップ" in event.summary for event in center.snapshot())


def test_session_shutdown_saves_latest_cc_after_port_disconnect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.json"
    inport = DisconnectingInPort(
        DummyCcMsg(type="control_change", control=7, value=127)
    )
    controller = MidiController(
        "Dummy Port",
        snapshot_path=path,
        inport=inport,
    )
    session = MidiSession(
        controller=controller,
        snapshot_load_result=controller.snapshot_load_result,
    )

    snapshot = session.frame_snapshot()
    assert snapshot is not None
    assert snapshot.source == "midi_frozen"
    assert snapshot[7] == 1.0
    session.close()

    assert inport.closed is True
    assert load_cc_snapshot(path).as_dict() == {7: 1.0}


def test_clear_after_disconnect_does_not_restore_live_values_on_shutdown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "snapshot.json"
    controller = MidiController(
        "Dummy Port",
        snapshot_path=path,
        inport=DisconnectingInPort(
            DummyCcMsg(type="control_change", control=7, value=127)
        ),
    )
    session = MidiSession(
        controller=controller,
        snapshot_load_result=controller.snapshot_load_result,
    )
    assert session.frame_snapshot() is not None

    session.clear_frozen_snapshot()
    session.close()

    assert load_cc_snapshot(path).as_dict() == {}


def test_maybe_load_frozen_cc_snapshot_returns_none_when_midi_disabled(tmp_path: Path) -> None:
    assert (
        maybe_load_frozen_cc_snapshot(
            port_name=None,
            controller=None,
            snapshot_path=tmp_path / "main.json",
        )
        is None
    )


def test_maybe_load_frozen_cc_snapshot_returns_none_when_controller_present(
    tmp_path: Path,
) -> None:
    ctrl = MidiController(
        "Dummy Port",
        snapshot_path=tmp_path / "main.json",
        mode="7bit",
        inport=DummyInPort([]),
    )
    assert (
        maybe_load_frozen_cc_snapshot(
            port_name="auto",
            controller=ctrl,
            snapshot_path=tmp_path / "main.json",
        )
        is None
    )


def test_maybe_load_frozen_cc_snapshot_loads_when_no_controller(tmp_path: Path) -> None:
    path = tmp_path / "main.json"
    save_cc_snapshot({1: 0.25, 2: 1.0}, path)

    result = maybe_load_frozen_cc_snapshot(
        port_name="auto",
        controller=None,
        snapshot_path=path,
    )
    assert result is not None
    assert result.status == "loaded"
    assert result.as_dict() == {1: 0.25, 2: 1.0}


def test_frozen_load_helpers_reject_non_path_before_branching() -> None:
    with pytest.raises(TypeError, match="snapshot_path.*Path"):
        load_cc_snapshot("main.json")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="snapshot_path.*Path"):
        save_cc_snapshot({}, "main.json")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="snapshot_path.*Path"):
        load_frozen_cc_snapshot(snapshot_path="main.json")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="snapshot_path.*Path"):
        maybe_load_frozen_cc_snapshot(
            port_name=None,
            controller=None,
            snapshot_path="main.json",  # type: ignore[arg-type]
        )
