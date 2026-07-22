from __future__ import annotations

import sys
from dataclasses import dataclass

import pytest

from grafix.core.operation_selector import PRIMITIVE_SELECTOR_OP
from grafix.core.parameters import ParameterRow
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.widgets import (
    widget_choice_radio as _widget_choice_radio,
)


@dataclass(frozen=True)
class _Vec2:
    x: float
    y: float

    def __iter__(self):
        yield self.x
        yield self.y

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y)[index]


@dataclass(frozen=True)
class _Style:
    item_spacing: _Vec2
    item_inner_spacing: _Vec2


class _Combo:
    def __init__(self, owner: _ChoiceImgui, *, opened: bool) -> None:
        self._owner = owner
        self.opened = bool(opened)

    def __enter__(self) -> _Combo:
        return self

    def __exit__(self, *_args: object) -> None:
        if self.opened:
            self._owner.end_combo_calls += 1


class _ChoiceImgui:
    """Adaptive choice の描画判断と入出力だけを記録する pyimgui fake。"""

    COMBO_HEIGHT_LARGE = 32

    def __init__(
        self,
        *,
        available_width: float,
        popup_open: bool = True,
        filter_value: str | None = None,
        click: str | None = None,
        char_width: float = 8.0,
        ui_scale: float = 1.0,
    ) -> None:
        self.available_width = float(available_width)
        self.popup_open = bool(popup_open)
        self.filter_value = filter_value
        self.click = click
        self.ui_scale = float(ui_scale)
        self.char_width = float(char_width) * self.ui_scale

        self.radio_labels: list[tuple[str, bool]] = []
        self.combo_calls: list[tuple[str, str, int | None]] = []
        self.filter_calls: list[tuple[str, str, str]] = []
        self.selectable_labels: list[tuple[str, bool]] = []
        self.text_calls: list[str] = []
        self.end_combo_calls = 0

    def get_content_region_available_width(self) -> float:
        return self.available_width

    def calc_text_size(self, text: str) -> _Vec2:
        return _Vec2(len(str(text)) * self.char_width, 16.0)

    def get_frame_height(self) -> float:
        return 20.0 * self.ui_scale

    def get_style(self) -> _Style:
        return _Style(
            item_spacing=_Vec2(6.0 * self.ui_scale, 4.0 * self.ui_scale),
            item_inner_spacing=_Vec2(4.0 * self.ui_scale, 3.0 * self.ui_scale),
        )

    def radio_button(self, label: str, selected: bool) -> bool:
        self.radio_labels.append((str(label), bool(selected)))
        return self._visible_label(label) == self.click

    def same_line(self, *_args: object, **_kwargs: object) -> None:
        return

    def begin_combo(
        self,
        label: str,
        preview: str,
        flags: int | None = None,
    ) -> _Combo:
        self.combo_calls.append((str(label), str(preview), flags))
        return _Combo(self, opened=self.popup_open)

    def input_text_with_hint(
        self,
        label: str,
        hint: str,
        current: str,
        *_args: object,
        **_kwargs: object,
    ) -> tuple[bool, str]:
        self.filter_calls.append((str(label), str(hint), str(current)))
        if self.filter_value is None:
            return False, str(current)
        return str(self.filter_value) != str(current), str(self.filter_value)

    def selectable(self, label: str, selected: bool) -> tuple[bool, bool]:
        self.selectable_labels.append((str(label), bool(selected)))
        clicked = self._visible_label(label) == self.click
        return clicked, clicked

    def set_item_default_focus(self) -> None:
        return

    def set_next_item_width(self, _width: float) -> None:
        return

    def text(self, text: str) -> None:
        self.text_calls.append(str(text))

    def text_disabled(self, text: str) -> None:
        self.text_calls.append(str(text))

    @staticmethod
    def _visible_label(label: str) -> str:
        return str(label).split("##", 1)[0]


def _choice_row(
    *,
    choices: tuple[str, ...],
    value: str,
    site_id: str,
    op: str = "test_choice",
    arg: str = "mode",
) -> ParameterRow:
    return ParameterRow(
        label=f"0:{arg}",
        op=op,
        site_id=site_id,
        arg=arg,
        kind="choice",
        ui_value=value,
        ui_min=None,
        ui_max=None,
        choices=choices,
        cc_key=None,
        override=False,
        ordinal=0,
    )


def _visible_selectables(imgui: _ChoiceImgui) -> list[str]:
    return [
        _ChoiceImgui._visible_label(label)
        for label, _selected in imgui.selectable_labels
    ]


def widget_choice_radio(
    row: ParameterRow,
    *,
    state: WidgetSessionState | None = None,
) -> tuple[bool, str]:
    """各testが所有する一時stateをproduction widgetへ明示注入する。"""

    return _widget_choice_radio(
        row,
        state=WidgetSessionState() if state is None else state,
    )


def test_short_choices_that_fit_use_inline_radio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imgui = _ChoiceImgui(available_width=1_000.0)
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=("alpha", "beta", "gamma"),
        value="beta",
        site_id="short-wide",
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == "beta"
    assert [label.split("##", 1)[0] for label, _ in imgui.radio_labels] == [
        "alpha",
        "beta",
        "gamma",
    ]
    assert imgui.combo_calls == []


@pytest.mark.parametrize(
    ("choices", "available_width", "site_id"),
    [
        (("a", "b"), 1.0, "short-narrow"),
        (
            ("an exceptionally long choice label", "short"),
            100.0,
            "long-label",
        ),
        (("a", "b", "c", "d", "e"), 10_000.0, "five-choices"),
    ],
)
def test_narrow_long_or_five_choices_use_combo(
    monkeypatch: pytest.MonkeyPatch,
    choices: tuple[str, ...],
    available_width: float,
    site_id: str,
) -> None:
    imgui = _ChoiceImgui(
        available_width=available_width,
        popup_open=False,
    )
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=choices,
        value=choices[-1],
        site_id=site_id,
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == choices[-1]
    assert imgui.radio_labels == []
    assert imgui.combo_calls == [("##value", choices[-1], imgui.COMBO_HEIGHT_LARGE)]


@pytest.mark.parametrize(
    ("logical_width", "expects_radio"),
    [
        (210.0, True),
        (190.0, False),
    ],
)
def test_radio_combo_decision_is_stable_at_retina_scale(
    monkeypatch: pytest.MonkeyPatch,
    logical_width: float,
    expects_radio: bool,
) -> None:
    row = _choice_row(
        choices=("alpha", "beta", "gamma"),
        value="beta",
        site_id=f"retina-{logical_width}",
    )
    outcomes: list[bool] = []

    for scale in (1.0, 2.0):
        imgui = _ChoiceImgui(
            available_width=logical_width * scale,
            popup_open=False,
            ui_scale=scale,
        )
        monkeypatch.setitem(sys.modules, "imgui", imgui)

        changed, value = widget_choice_radio(row)

        assert changed is False
        assert value == "beta"
        outcomes.append(bool(imgui.radio_labels))

    assert outcomes == [expects_radio, expects_radio]


@pytest.mark.parametrize("available_width", [0.0, -10.0])
def test_nonpositive_known_width_uses_combo(
    monkeypatch: pytest.MonkeyPatch,
    available_width: float,
) -> None:
    imgui = _ChoiceImgui(
        available_width=available_width,
        popup_open=False,
    )
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=("a", "b"),
        value="b",
        site_id=f"nonpositive-width-{available_width}",
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == "b"
    assert imgui.radio_labels == []
    assert imgui.combo_calls


def test_combo_only_reports_change_for_explicit_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = ("a", "b", "c", "d", "e")
    row = _choice_row(
        choices=choices,
        value="b",
        site_id="combo-change-contract",
    )

    closed_imgui = _ChoiceImgui(
        available_width=10_000.0,
        popup_open=False,
    )
    monkeypatch.setitem(sys.modules, "imgui", closed_imgui)
    assert widget_choice_radio(row) == (False, "b")

    open_imgui = _ChoiceImgui(available_width=10_000.0)
    monkeypatch.setitem(sys.modules, "imgui", open_imgui)
    assert widget_choice_radio(row) == (False, "b")

    selecting_imgui = _ChoiceImgui(
        available_width=10_000.0,
        click="d",
    )
    monkeypatch.setitem(sys.modules, "imgui", selecting_imgui)
    assert widget_choice_radio(row) == (True, "d")


def test_filter_edit_alone_does_not_change_choice_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = tuple(f"choice-{index}" for index in range(8))
    imgui = _ChoiceImgui(
        available_width=10_000.0,
        filter_value="choice-1",
    )
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=choices,
        value="choice-3",
        site_id="filter-only-is-not-value-change",
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == "choice-3"
    assert imgui.filter_calls


def test_choice_filter_isolated_between_two_widget_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = tuple(f"choice-{index}" for index in range(8))
    row = _choice_row(
        choices=choices,
        value="choice-3",
        site_id="same-site-in-two-sessions",
    )
    first_state = WidgetSessionState()
    second_state = WidgetSessionState()

    first_imgui = _ChoiceImgui(
        available_width=10_000.0,
        filter_value="choice-1",
    )
    monkeypatch.setitem(sys.modules, "imgui", first_imgui)
    widget_choice_radio(row, state=first_state)

    second_imgui = _ChoiceImgui(available_width=10_000.0)
    monkeypatch.setitem(sys.modules, "imgui", second_imgui)
    widget_choice_radio(row, state=second_state)

    reopened_first_imgui = _ChoiceImgui(available_width=10_000.0)
    monkeypatch.setitem(sys.modules, "imgui", reopened_first_imgui)
    widget_choice_radio(row, state=first_state)

    key = (row.op, row.site_id, row.arg)
    assert first_state.choice_filter_by_key == {key: "choice-1"}
    assert second_state.choice_filter_by_key == {}
    assert second_imgui.filter_calls == [
        ("##choice_filter", "Filter choices", ""),
    ]
    assert reopened_first_imgui.filter_calls == [
        ("##choice_filter", "Filter choices", "choice-1"),
    ]


def test_selecting_a_filtered_choice_clears_temporary_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = tuple(f"choice-{index}" for index in range(8))
    row = _choice_row(
        choices=choices,
        value="choice-3",
        site_id="filter-cleared-after-selection",
    )
    selecting_imgui = _ChoiceImgui(
        available_width=10_000.0,
        filter_value="choice-1",
        click="choice-1",
    )
    monkeypatch.setitem(sys.modules, "imgui", selecting_imgui)
    widget_state = WidgetSessionState()

    assert widget_choice_radio(row, state=widget_state) == (True, "choice-1")

    reopened_imgui = _ChoiceImgui(available_width=10_000.0)
    monkeypatch.setitem(sys.modules, "imgui", reopened_imgui)
    widget_choice_radio(row, state=widget_state)

    assert reopened_imgui.filter_calls == [
        ("##choice_filter", "Filter choices", ""),
    ]


def test_large_combo_filter_is_case_insensitive_and_uses_and_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = (
        "Alpha One",
        "Alpha Two",
        "Beta One",
        "ALPHA middle ONE",
        "Gamma",
        "Delta",
        "Epsilon",
        "Zeta",
    )
    imgui = _ChoiceImgui(
        available_width=10_000.0,
        filter_value="ALPHA one",
    )
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=choices,
        value="Beta One",
        site_id="filter-and-casefold",
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == "Beta One"
    assert imgui.filter_calls == [
        ("##choice_filter", "Filter choices", ""),
    ]
    assert _visible_selectables(imgui) == ["Alpha One", "ALPHA middle ONE"]
    assert imgui.combo_calls == [
        ("##value", "Beta One", imgui.COMBO_HEIGHT_LARGE),
    ]


def test_large_combo_filter_reports_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = tuple(f"choice-{index}" for index in range(8))
    imgui = _ChoiceImgui(
        available_width=10_000.0,
        filter_value="does not exist",
    )
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=choices,
        value=choices[3],
        site_id="filter-no-match",
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == choices[3]
    assert _visible_selectables(imgui) == []
    assert imgui.text_calls == ["No match"]
    assert imgui.end_combo_calls == 1


def test_normal_stale_choice_is_preserved_as_unavailable_in_combo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    choices = ("a", "b", "c", "d", "e")
    imgui = _ChoiceImgui(
        available_width=10_000.0,
        popup_open=False,
    )
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=choices,
        value="removed",
        site_id="normal-stale",
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == "removed"
    assert imgui.combo_calls == [
        ("##value", "removed (unavailable)", imgui.COMBO_HEIGHT_LARGE)
    ]


def test_normal_stale_choice_forces_unavailable_combo_instead_of_radio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imgui = _ChoiceImgui(available_width=1_000.0)
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=("a", "b"),
        value="removed",
        site_id="normal-stale-radio",
    )

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == "removed"
    assert imgui.radio_labels == []
    assert imgui.combo_calls == [
        ("##value", "removed (unavailable)", imgui.COMBO_HEIGHT_LARGE)
    ]


def test_empty_choice_list_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imgui = _ChoiceImgui(available_width=1_000.0)
    monkeypatch.setitem(sys.modules, "imgui", imgui)
    row = _choice_row(
        choices=(),
        value="",
        site_id="empty-choices",
    )

    with pytest.raises(ValueError, match="non-empty choices"):
        widget_choice_radio(row)


def test_selector_stale_target_is_preserved_until_explicit_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _choice_row(
        choices=("circle", "rect"),
        value="removed_target",
        site_id="selector-stale",
        op=PRIMITIVE_SELECTOR_OP,
        arg="target",
    )
    idle_imgui = _ChoiceImgui(
        available_width=10_000.0,
        popup_open=False,
    )
    monkeypatch.setitem(sys.modules, "imgui", idle_imgui)

    changed, value = widget_choice_radio(row)

    assert changed is False
    assert value == "removed_target"
    assert idle_imgui.radio_labels == []
    assert idle_imgui.combo_calls == [
        (
            "##value",
            "removed_target (unavailable)",
            idle_imgui.COMBO_HEIGHT_LARGE,
        ),
    ]

    selecting_imgui = _ChoiceImgui(
        available_width=10_000.0,
        click="circle",
    )
    monkeypatch.setitem(sys.modules, "imgui", selecting_imgui)

    changed, value = widget_choice_radio(row)

    assert changed is True
    assert value == "circle"
    assert _visible_selectables(selecting_imgui) == ["circle", "rect"]
    assert selecting_imgui.end_combo_calls == 1
