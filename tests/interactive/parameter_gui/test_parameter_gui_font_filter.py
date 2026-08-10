from __future__ import annotations

import sys

import pytest

from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui import widgets as widgets_module
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.widgets import (
    _filter_choices_by_query_and,
    _font_choice_labels,
    widget_font_picker,
)


class _Combo:
    def __init__(self, *, opened: bool) -> None:
        self.opened = bool(opened)

    def __enter__(self) -> _Combo:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FontPickerImgui:
    """font picker の入出力だけを記録する pyimgui fake。"""

    def __init__(
        self,
        *,
        popup_open: bool = False,
        filter_value: str | None = None,
        click: str | None = None,
        refresh: bool = False,
    ) -> None:
        self.popup_open = bool(popup_open)
        self.filter_value = filter_value
        self.click = click
        self.refresh = bool(refresh)
        self.filter_inputs: list[str] = []
        self.button_labels: list[str] = []
        self.selectable_labels: list[tuple[str, bool]] = []
        self.text_calls: list[str] = []

    def set_next_item_width(self, _width: float) -> None:
        return None

    def input_text(self, _label: str, current: str) -> tuple[bool, str]:
        self.filter_inputs.append(str(current))
        if self.filter_value is None:
            return False, str(current)
        return self.filter_value != current, self.filter_value

    def begin_combo(self, _label: str, _preview: str) -> _Combo:
        return _Combo(opened=self.popup_open)

    def button(self, label: str) -> bool:
        self.button_labels.append(str(label))
        return self.refresh

    def selectable(self, label: str, selected: bool) -> tuple[bool, bool]:
        self.selectable_labels.append((str(label), bool(selected)))
        clicked = self._visible_label(label) == self.click
        return clicked, clicked

    def set_item_default_focus(self) -> None:
        return None

    def text(self, value: str) -> None:
        self.text_calls.append(str(value))

    @staticmethod
    def _visible_label(label: str) -> str:
        return str(label).split("##", 1)[0]


def _choice(stem: str, rel: str) -> tuple[str, str, bool, str]:
    return (stem, rel, rel.lower().endswith(".ttc"), f"{rel} {stem}".lower())


def _row(*, site_id: str, value: str = "NotoSansJP-Regular.ttf") -> ParameterRow:
    return ParameterRow(
        label="0:font",
        op="text",
        site_id=site_id,
        arg="font",
        kind="font",
        ui_value=value,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=False,
        ordinal=0,
    )


def _visible_selectables(imgui: _FontPickerImgui) -> list[str]:
    return [
        imgui._visible_label(label)
        for label, _selected in imgui.selectable_labels
    ]


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


def test_duplicate_stem_labels_include_relative_value_only_for_duplicates() -> None:
    choices = (
        _choice("Shared", "First/Shared.ttf"),
        _choice("Unique", "Unique.otf"),
        _choice("shared", "Second/shared.ttc"),
    )

    assert _font_choice_labels(choices) == (
        "Shared (First/Shared.ttf)",
        "Unique",
        "shared (Second/shared.ttc)",
    )


def test_closed_font_combos_do_not_build_choices_across_rows_or_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def choices() -> tuple[tuple[str, str, bool, str], ...]:
        nonlocal calls
        calls += 1
        return (_choice("A", "A.ttf"),)

    monkeypatch.setattr(widgets_module, "list_font_choices", choices)
    imgui = _FontPickerImgui(popup_open=False)
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    state = WidgetSessionState()
    rows = (_row(site_id="first"), _row(site_id="second"))

    for _frame in range(60):
        for row in rows:
            assert widget_font_picker(row, state=state)[0] is False

    assert calls == 0
    assert state.font_choices is None


def test_open_font_combos_share_one_choice_snapshot_across_rows_and_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def choices() -> tuple[tuple[str, str, bool, str], ...]:
        nonlocal calls
        calls += 1
        return (_choice("A", "A.ttf"),)

    monkeypatch.setattr(widgets_module, "list_font_choices", choices)
    imgui = _FontPickerImgui(popup_open=True)
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    state = WidgetSessionState()
    rows = (_row(site_id="first"), _row(site_id="second"))

    for _frame in range(60):
        for row in rows:
            widget_font_picker(row, state=state)

    assert calls == 1
    assert state.font_choices == (_choice("A", "A.ttf"),)


def test_refresh_fonts_is_the_only_way_to_rebuild_open_session_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    catalog = [_choice("A", "A.ttf")]

    def choices() -> tuple[tuple[str, str, bool, str], ...]:
        nonlocal calls
        calls += 1
        return tuple(catalog)

    monkeypatch.setattr(widgets_module, "list_font_choices", choices)
    state = WidgetSessionState()
    row = _row(site_id="refresh")

    first_imgui = _FontPickerImgui(popup_open=True)
    monkeypatch.setitem(sys.modules, "imgui", first_imgui)
    widget_font_picker(row, state=state)
    assert calls == 1
    assert _visible_selectables(first_imgui) == ["A"]

    catalog.append(_choice("B", "Nested/B.ttf"))
    cached_imgui = _FontPickerImgui(popup_open=True)
    monkeypatch.setitem(sys.modules, "imgui", cached_imgui)
    widget_font_picker(row, state=state)
    assert calls == 1
    assert _visible_selectables(cached_imgui) == ["A"]

    refreshed_imgui = _FontPickerImgui(popup_open=True, refresh=True)
    monkeypatch.setitem(sys.modules, "imgui", refreshed_imgui)
    widget_font_picker(row, state=state)
    assert calls == 2
    assert _visible_selectables(refreshed_imgui) == ["A", "B"]
    assert refreshed_imgui.button_labels == ["Refresh fonts"]


def test_font_choice_snapshot_is_not_shared_between_widget_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def choices() -> tuple[tuple[str, str, bool, str], ...]:
        nonlocal calls
        calls += 1
        return (_choice("A", "A.ttf"),)

    monkeypatch.setattr(widgets_module, "list_font_choices", choices)
    imgui = _FontPickerImgui(popup_open=True)
    monkeypatch.setitem(sys.modules, "imgui", imgui)

    widget_font_picker(_row(site_id="same"), state=WidgetSessionState())
    widget_font_picker(_row(site_id="same"), state=WidgetSessionState())

    assert calls == 2


def test_clear_releases_font_choice_snapshot_and_next_open_rebuilds_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def choices() -> tuple[tuple[str, str, bool, str], ...]:
        nonlocal calls
        calls += 1
        return (_choice("A", "A.ttf"),)

    monkeypatch.setattr(widgets_module, "list_font_choices", choices)
    imgui = _FontPickerImgui(popup_open=True)
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    state = WidgetSessionState()
    row = _row(site_id="clear")

    widget_font_picker(row, state=state)
    state.clear()
    widget_font_picker(row, state=state)

    assert calls == 2


def test_nested_font_click_returns_root_relative_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_value = "Supplemental/Kannada MN.ttc"
    monkeypatch.setattr(
        widgets_module,
        "list_font_choices",
        lambda: (_choice("Kannada MN", relative_value),),
    )
    imgui = _FontPickerImgui(popup_open=True, click="Kannada MN")
    monkeypatch.setitem(sys.modules, "imgui", imgui)

    assert widget_font_picker(
        _row(site_id="nested", value="Other.ttf"),
        state=WidgetSessionState(),
    ) == (True, relative_value)


def test_unique_legacy_basename_is_selected_without_rewriting_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_value = "Supplemental/Kannada MN.ttc"
    monkeypatch.setattr(
        widgets_module,
        "list_font_choices",
        lambda: (_choice("Kannada MN", relative_value),),
    )
    imgui = _FontPickerImgui(popup_open=True)
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    legacy_value = "Kannada MN.ttc"

    assert widget_font_picker(
        _row(site_id="legacy", value=legacy_value),
        state=WidgetSessionState(),
    ) == (False, legacy_value)
    assert imgui.selectable_labels == [("Kannada MN##Supplemental/Kannada MN.ttc", True)]


def test_ambiguous_legacy_basename_does_not_select_or_rewrite_a_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = (
        _choice("Shared", "First/Shared.ttf"),
        _choice("Shared", "Second/Shared.ttf"),
    )
    monkeypatch.setattr(widgets_module, "list_font_choices", lambda: choices)
    imgui = _FontPickerImgui(popup_open=True)
    monkeypatch.setitem(sys.modules, "imgui", imgui)

    assert widget_font_picker(
        _row(site_id="ambiguous", value="Shared.ttf"),
        state=WidgetSessionState(),
    ) == (False, "Shared.ttf")
    assert all(not selected for _label, selected in imgui.selectable_labels)


def test_font_filter_isolated_between_widget_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(site_id="same-site-in-two-sessions")
    first_state = WidgetSessionState()
    second_state = WidgetSessionState()

    first_imgui = _FontPickerImgui(filter_value="noto sans")
    monkeypatch.setitem(sys.modules, "imgui", first_imgui)
    widget_font_picker(row, state=first_state)

    second_imgui = _FontPickerImgui()
    monkeypatch.setitem(sys.modules, "imgui", second_imgui)
    widget_font_picker(row, state=second_state)

    reopened_first_imgui = _FontPickerImgui()
    monkeypatch.setitem(sys.modules, "imgui", reopened_first_imgui)
    widget_font_picker(row, state=first_state)

    key = (row.op, row.site_id, row.arg)
    assert first_state.font_filter_by_key == {key: "noto sans"}
    assert second_state.font_filter_by_key == {}
    assert second_imgui.filter_inputs == [""]
    assert reopened_first_imgui.filter_inputs == ["noto sans"]
