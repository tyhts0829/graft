from __future__ import annotations

import pytest

from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui.midi_learn import (
    MidiLearnCommand,
    MidiLearnState,
    transition_midi_learn,
)
from grafix.interactive.parameter_gui.rules import ui_rules_for_row
from grafix.interactive.parameter_gui.table import _render_cc_cell


class DummyImGui:
    COLOR_BUTTON = 0
    COLOR_BUTTON_HOVERED = 1
    COLOR_BUTTON_ACTIVE = 2
    COLOR_TEXT = 3

    def __init__(self, *, clicked_ids: set[str] | None = None) -> None:
        self._clicked_ids = set(clicked_ids or set())
        self.buttons: list[str] = []

    def table_set_column_index(self, _index: int) -> None:
        return None

    def get_content_region_available_width(self) -> float:
        return 165.0

    def calc_text_size(self, _text: str) -> tuple[float, float]:
        return 28.0, 14.0

    def button(self, label: str, *_size: float) -> bool:
        self.buttons.append(str(label))
        if "##" not in label:
            return False
        widget_id = label.split("##", 1)[1]
        return widget_id in self._clicked_ids

    def same_line(self, *_args: float) -> None:
        return None

    def checkbox(self, label: str, value: bool) -> tuple[bool, bool]:
        _ = label
        return False, bool(value)

    def is_item_hovered(self) -> bool:
        return False

    def is_item_focused(self) -> bool:
        return False

    def set_tooltip(self, _text: str) -> None:
        return None


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


@pytest.mark.parametrize("component", (None, 1))
def test_midi_learn_transition_is_shared_by_scalar_and_vec3_components(
    component: int | None,
) -> None:
    target = ParameterKey(op="op", site_id="site", arg="value")

    entered = transition_midi_learn(
        MidiLearnState(),
        target=target,
        component=component,
        current_cc=None,
        last_cc_change=(10, 7),
        clicked=True,
    )
    assert entered.state == MidiLearnState(
        active_target=target,
        active_component=component,
        last_seen_cc_seq=10,
    )
    assert entered.active is True
    assert entered.command is None

    stale = transition_midi_learn(
        entered.state,
        target=target,
        component=component,
        current_cc=None,
        last_cc_change=(10, 99),
        clicked=False,
    )
    assert stale == entered

    learned = transition_midi_learn(
        stale.state,
        target=target,
        component=component,
        current_cc=stale.current_cc,
        last_cc_change=(11, 64),
        clicked=False,
    )
    assert learned.state == MidiLearnState(last_seen_cc_seq=11)
    assert learned.current_cc == 64
    assert learned.active is False
    assert learned.command == MidiLearnCommand(component=component, cc=64)

    removed = transition_midi_learn(
        learned.state,
        target=target,
        component=component,
        current_cc=learned.current_cc,
        last_cc_change=(11, 64),
        clicked=True,
    )
    assert removed.current_cc is None
    assert removed.command == MidiLearnCommand(component=component, cc=None)


@pytest.mark.parametrize("component", (None, 2))
def test_midi_learn_transition_cancels_active_target_without_cc_command(
    component: int | None,
) -> None:
    target = ParameterKey(op="op", site_id="site", arg="value")
    state = MidiLearnState(
        active_target=target,
        active_component=component,
        last_seen_cc_seq=4,
    )

    cancelled = transition_midi_learn(
        state,
        target=target,
        component=component,
        current_cc=None,
        last_cc_change=(4, 12),
        clicked=True,
    )

    assert cancelled.state == MidiLearnState(last_seen_cc_seq=4)
    assert cancelled.current_cc is None
    assert cancelled.active is False
    assert cancelled.command is None


def test_scalar_learn_assign_and_clear() -> None:
    row = _row(kind="float", cc_key=None)
    state = MidiLearnState()
    rules = ui_rules_for_row(row)

    changed, cc_key, state = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn"}),
        row=row,
        rules=rules,
        cc_key=row.cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(10, 7),
    )
    assert changed is False
    assert cc_key is None
    assert state.active_target == ParameterKey(op="op", site_id="file.py:1:2", arg="x")
    assert state.active_component is None
    assert state.last_seen_cc_seq == 10

    waiting_imgui = DummyImGui()
    changed, cc_key, state = _render_cc_cell(
        waiting_imgui,
        row=row,
        rules=rules,
        cc_key=cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(10, 7),
    )
    assert changed is False
    assert waiting_imgui.buttons == ["V...##cc_learn"]

    changed, cc_key, state = _render_cc_cell(
        DummyImGui(),
        row=row,
        rules=rules,
        cc_key=cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(11, 64),
    )
    assert changed is True
    assert cc_key == 64
    assert state.active_target is None

    changed, cc_key, state = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn"}),
        row=row,
        rules=rules,
        cc_key=cc_key,
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

    waiting_imgui = DummyImGui(clicked_ids={"cc_learn_1"})
    changed, cc_key, state = _render_cc_cell(
        waiting_imgui,
        row=row,
        rules=rules,
        cc_key=row.cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(5, 10),
    )
    assert changed is False
    assert cc_key is None
    assert state.active_component == 1
    assert state.last_seen_cc_seq == 5

    # learn 中の同ボタン押下でキャンセル
    waiting_imgui = DummyImGui(clicked_ids={"cc_learn_1"})
    changed, cc_key, state = _render_cc_cell(
        waiting_imgui,
        row=row,
        rules=rules,
        cc_key=cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(5, 10),
    )
    assert changed is False
    assert cc_key is None
    assert state.active_target is None
    assert [label.split("##", 1)[0] for label in waiting_imgui.buttons] == [
        "X",
        "Y...",
        "Z",
    ]

    # もう一度 learn してから CC を受信して割当
    changed, cc_key, state = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn_1"}),
        row=row,
        rules=rules,
        cc_key=cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(6, 11),
    )
    assert state.active_component == 1

    changed, cc_key, state = _render_cc_cell(
        DummyImGui(),
        row=row,
        rules=rules,
        cc_key=cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(7, 21),
    )
    assert changed is True
    assert cc_key == (None, 21, None)
    assert state.active_target is None

    # 割当済ボタン押下でクリア（全 None → cc_key=None）
    changed, cc_key, state = _render_cc_cell(
        DummyImGui(clicked_ids={"cc_learn_1"}),
        row=row,
        rules=rules,
        cc_key=cc_key,
        width_spacer=4,
        midi_learn_state=state,
        midi_last_cc_change=(7, 21),
    )
    assert changed is True
    assert cc_key is None
