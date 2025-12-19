from __future__ import annotations

from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui.midi_learn import MidiLearnState
from grafix.interactive.parameter_gui.rules import ui_rules_for_row
from grafix.interactive.parameter_gui.table import _render_cc_cell


class DummyImGui:
    def __init__(self, *, clicked_ids: set[str] | None = None) -> None:
        self._clicked_ids = set(clicked_ids or set())

    def table_set_column_index(self, _index: int) -> None:
        return None

    def button(self, label: str, *_size: float) -> bool:
        if "##" not in label:
            return False
        widget_id = label.split("##", 1)[1]
        return widget_id in self._clicked_ids

    def same_line(self, *_args: float) -> None:
        return None

    def checkbox(self, label: str, value: bool) -> tuple[bool, bool]:
        _ = label
        return False, bool(value)


def _row(*, kind: str, cc_key, override: bool = False) -> ParameterRow:
    return ParameterRow(
        label="1:x",
        op="op",
        site_id="file.py:1:2",
        arg="x",
        kind=kind,
        ui_value=0.0,
        ui_min=0.0,
        ui_max=1.0,
        choices=None,
        cc_key=cc_key,
        override=override,
        ordinal=1,
    )


def test_scalar_learn_assign_and_clear() -> None:
    row = _row(kind="float", cc_key=None)
    state = MidiLearnState()
    rules = ui_rules_for_row(row)

    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn"}),
        row=row,
        rules=rules,
        cc_key=row.cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(10, 7),
    )
    assert changed is False
    assert cc_key is None
    assert state.active_target == ParameterKey(op="op", site_id="file.py:1:2", arg="x")
    assert state.active_component is None
    assert state.last_seen_cc_seq == 10

    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(),
        row=row,
        rules=rules,
        cc_key=cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(11, 64),
    )
    assert changed is True
    assert cc_key == 64
    assert state.active_target is None

    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn"}),
        row=row,
        rules=rules,
        cc_key=cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(11, 64),
    )
    assert changed is True
    assert cc_key is None


def test_vec3_component_learn_and_cancel_and_clear() -> None:
    row = _row(kind="vec3", cc_key=None)
    state = MidiLearnState()
    rules = ui_rules_for_row(row)

    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn_1"}),
        row=row,
        rules=rules,
        cc_key=row.cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(5, 10),
    )
    assert changed is False
    assert cc_key is None
    assert state.active_component == 1
    assert state.last_seen_cc_seq == 5

    # learn 中の同ボタン押下でキャンセル
    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn_1"}),
        row=row,
        rules=rules,
        cc_key=cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(5, 10),
    )
    assert changed is False
    assert cc_key is None
    assert state.active_target is None

    # もう一度 learn してから CC を受信して割当
    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn_1"}),
        row=row,
        rules=rules,
        cc_key=cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(6, 11),
    )
    assert state.active_component == 1

    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(),
        row=row,
        rules=rules,
        cc_key=cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(7, 21),
    )
    assert changed is True
    assert cc_key == (None, 21, None)
    assert state.active_target is None

    # 割当済ボタン押下でクリア（全 None → cc_key=None）
    changed, cc_key, _override = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn_1"}),
        row=row,
        rules=rules,
        cc_key=cc_key,
        override=row.override,
        cc_key_width=30,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(7, 21),
    )
    assert changed is True
    assert cc_key is None

