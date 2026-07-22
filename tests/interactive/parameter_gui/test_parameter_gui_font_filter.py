from __future__ import annotations

import sys

import pytest

from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui import widgets as widgets_module
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.widgets import _filter_choices_by_query_and
from grafix.interactive.parameter_gui.widgets import widget_font_picker


class _ClosedCombo:
    opened = False

    def __enter__(self) -> _ClosedCombo:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FontFilterImgui:
    def __init__(self, *, filter_value: str | None = None) -> None:
        self.filter_value = filter_value
        self.filter_inputs: list[str] = []

    def set_next_item_width(self, _width: float) -> None:
        return None

    def input_text(self, _label: str, current: str) -> tuple[bool, str]:
        self.filter_inputs.append(str(current))
        if self.filter_value is None:
            return False, str(current)
        return self.filter_value != current, self.filter_value

    def begin_combo(self, _label: str, _preview: str) -> _ClosedCombo:
        return _ClosedCombo()


def _choice(stem: str, rel: str):
    return (stem, rel, rel.lower().endswith(".ttc"), f"{rel} {stem}".lower())


def test_filter_choices_by_query_and_matches_all_tokens_case_insensitive():
    choices = (
        _choice("NotoSansJP-Regular", "NotoSansJP-Regular.otf"),
        _choice("SFNS", "SFNS.ttf"),
        _choice("NotoSerifJP-Regular", "NotoSerifJP-Regular.otf"),
    )

    out = _filter_choices_by_query_and(choices, query="noto sans")
    assert [item[1] for item in out] == ["NotoSansJP-Regular.otf"]

    out2 = _filter_choices_by_query_and(choices, query="SANS NOTO")
    assert [item[1] for item in out2] == ["NotoSansJP-Regular.otf"]

    out3 = _filter_choices_by_query_and(choices, query="noto jp")
    assert [item[1] for item in out3] == [
        "NotoSansJP-Regular.otf",
        "NotoSerifJP-Regular.otf",
    ]


def test_filter_choices_by_query_and_empty_query_returns_all():
    choices = (
        _choice("A", "A.ttf"),
        _choice("B", "B.otf"),
    )
    out = _filter_choices_by_query_and(choices, query="")
    assert [item[1] for item in out] == ["A.ttf", "B.otf"]


def test_font_filter_isolated_between_widget_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = ParameterRow(
        label="0:font",
        op="text",
        site_id="same-site-in-two-sessions",
        arg="font",
        kind="font",
        ui_value="NotoSansJP-Regular.ttf",
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=False,
        ordinal=0,
    )
    monkeypatch.setattr(
        widgets_module,
        "list_font_choices",
        lambda: (
            _choice("NotoSansJP-Regular", "NotoSansJP-Regular.ttf"),
            _choice("NotoSerifJP-Regular", "NotoSerifJP-Regular.ttf"),
        ),
    )
    first_state = WidgetSessionState()
    second_state = WidgetSessionState()

    first_imgui = _FontFilterImgui(filter_value="noto sans")
    monkeypatch.setitem(sys.modules, "imgui", first_imgui)
    widget_font_picker(row, state=first_state)

    second_imgui = _FontFilterImgui()
    monkeypatch.setitem(sys.modules, "imgui", second_imgui)
    widget_font_picker(row, state=second_state)

    reopened_first_imgui = _FontFilterImgui()
    monkeypatch.setitem(sys.modules, "imgui", reopened_first_imgui)
    widget_font_picker(row, state=first_state)

    key = (row.op, row.site_id, row.arg)
    assert first_state.font_filter_by_key == {key: "noto sans"}
    assert second_state.font_filter_by_key == {}
    assert second_imgui.filter_inputs == [""]
    assert reopened_first_imgui.filter_inputs == ["noto sans"]
