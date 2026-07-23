from __future__ import annotations

import sys
from types import SimpleNamespace

from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.group_blocks import (
    GroupBlockLayout,
    GroupBlockLayoutItem,
)
from grafix.interactive.parameter_gui.grouping import GroupType
from grafix.interactive.parameter_gui.midi_learn import MidiLearnState
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
import grafix.interactive.parameter_gui.table as table_module
from grafix.interactive.parameter_gui.table import TableRenderInput


class _PopupContext:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def __enter__(self) -> SimpleNamespace:
        self._events.append("popup_enter")
        return SimpleNamespace(opened=True)

    def __exit__(self, *_args: object) -> None:
        self._events.append("popup_exit")


class _RecordingImGui:
    ALWAYS = 1
    COLOR_HEADER = 2
    COLOR_HEADER_HOVERED = 3
    COLOR_HEADER_ACTIVE = 4
    TREE_NODE_DEFAULT_OPEN = 8
    TREE_NODE_ALLOW_ITEM_OVERLAP = 16
    TABLE_SIZING_FIXED_FIT = 32
    TABLE_ROW_BACKGROUND = 64
    TABLE_BORDERS_INNER_VERTICAL = 128
    INPUT_TEXT_READ_ONLY = 256
    INPUT_TEXT_AUTO_SELECT_ALL = 512
    KEY_C = 67

    def __init__(self) -> None:
        self.events: list[str] = []

    def push_id(self, _value: str) -> None:
        self.events.append("push_id")

    def pop_id(self) -> None:
        self.events.append("pop_id")

    def set_next_item_open(self, _opened: bool, _condition: int) -> None:
        self.events.append("set_next_item_open")

    def push_style_color(self, *_args: object) -> None:
        self.events.append("push_style_color")

    def pop_style_color(self, _count: int) -> None:
        self.events.append("pop_style_color")

    def collapsing_header(self, *_args: object, **_kwargs: object) -> tuple[bool, bool]:
        self.events.append("collapsing_header")
        return True, True

    def set_item_allow_overlap(self) -> None:
        self.events.append("set_item_allow_overlap")

    def calc_text_size(self, text: str) -> tuple[float, float]:
        self.events.append(f"calc_text_size:{text}")
        return float(len(text)), 10.0

    def get_window_width(self) -> float:
        self.events.append("get_window_width")
        return 1_000.0

    def same_line(self, *_args: object, **_kwargs: object) -> None:
        self.events.append("same_line")

    def text_disabled(self, text: str) -> None:
        self.events.append(f"text_disabled:{text}")

    def small_button(self, label: str) -> bool:
        self.events.append(f"small_button:{label}")
        return label == "Code"

    def begin_table(self, *_args: object) -> SimpleNamespace:
        self.events.append("begin_table")
        return SimpleNamespace(opened=True)

    def table_headers_row(self) -> None:
        self.events.append("table_headers_row")

    def end_table(self) -> None:
        self.events.append("end_table")

    def open_popup(self, _name: str) -> None:
        self.events.append("open_popup")

    def set_next_window_position(self, *_args: object, **_kwargs: object) -> None:
        self.events.append("set_next_window_position")

    def set_next_window_size(self, *_args: object, **_kwargs: object) -> None:
        self.events.append("set_next_window_size")

    def begin_popup_modal(self, _name: str) -> _PopupContext:
        self.events.append("begin_popup_modal")
        return _PopupContext(self.events)

    def button(self, label: str) -> bool:
        self.events.append(f"button:{label}")
        return False

    def set_keyboard_focus_here(self) -> None:
        self.events.append("set_keyboard_focus_here")

    def get_content_region_available(self) -> tuple[float, float]:
        self.events.append("get_content_region_available")
        return 600.0, 400.0

    def input_text_multiline(self, *_args: object, **_kwargs: object) -> tuple[bool, str]:
        self.events.append("input_text_multiline")
        return False, ""

    def is_item_focused(self) -> bool:
        self.events.append("is_item_focused")
        return False

    def is_item_active(self) -> bool:
        self.events.append("is_item_active")
        return False


def _row() -> ParameterRow:
    return ParameterRow(
        label="line",
        op="line",
        site_id="site",
        arg="length",
        kind="float",
        ui_value=1.0,
        ui_min=0.0,
        ui_max=2.0,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=1,
    )


def test_render_parameter_table_preserves_section_call_order_and_exact_edits(
    monkeypatch,
) -> None:
    imgui = _RecordingImGui()
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    monkeypatch.setattr(
        table_module,
        "_setup_parameter_table_columns",
        lambda *_args, **_kwargs: imgui.events.append("setup_columns"),
    )
    monkeypatch.setattr(
        table_module,
        "_snippet_popup_geometry",
        lambda *_args, **_kwargs: (
            imgui.events.append("snippet_geometry") or (10.0, 20.0, 300.0, 200.0)
        ),
    )
    monkeypatch.setattr(
        table_module,
        "snippet_for_block",
        lambda *_args, **_kwargs: imgui.events.append("snippet_for_block") or "code\n",
    )

    row = _row()
    learn_state = MidiLearnState()

    def render_row(current: ParameterRow, **kwargs: object):
        imgui.events.append("render_row")
        return False, current, kwargs["midi_learn_state"]

    monkeypatch.setattr(table_module, "render_parameter_row_4cols", render_row)
    block = GroupBlockLayout(
        group_id=(GroupType.PRIMITIVE, ("line", 1)),
        header_id="primitive:line#1",
        header="line",
        items=(GroupBlockLayoutItem(row_index=0, visible_label="Length"),),
    )
    widget_state = WidgetSessionState()

    edits = table_module.render_parameter_table(
        TableRenderInput(
            group_layout=(block,),
            model_rows=(row,),
            catalog=current_parameter_gui_catalog(),
            collapsed_headers=frozenset(),
            midi_learn_state=learn_state,
        ),
        widget_state=widget_state,
    )

    assert imgui.events == [
        "push_id",
        "set_next_item_open",
        "push_style_color",
        "push_style_color",
        "push_style_color",
        "collapsing_header",
        "set_item_allow_overlap",
        "pop_style_color",
        "calc_text_size:Code",
        "calc_text_size:1 parameters",
        "get_window_width",
        "same_line",
        "text_disabled:1 parameters",
        "same_line",
        "small_button:Code",
        "snippet_for_block",
        "begin_table",
        "setup_columns",
        "table_headers_row",
        "render_row",
        "end_table",
        "pop_id",
        "open_popup",
        "snippet_geometry",
        "set_next_window_position",
        "set_next_window_size",
        "begin_popup_modal",
        "popup_enter",
        "button:Close",
        "same_line",
        "button:Copy",
        "same_line",
        "text_disabled:macOS Cmd+A→Cmd+C / Win/Linux Ctrl+A→Ctrl+C",
        "set_keyboard_focus_here",
        "get_content_region_available",
        "input_text_multiline",
        "is_item_focused",
        "is_item_active",
        "popup_exit",
    ]
    assert edits.rows == (row,)
    assert edits.collapsed_headers == frozenset()
    assert edits.midi_learn_state is learn_state
    assert edits.effect_order_commands == ()
    assert widget_state.snippet_popup_text == "code\n"
    assert widget_state.snippet_popup_focus_next is False
