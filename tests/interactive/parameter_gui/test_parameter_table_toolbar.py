from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from grafix.core.parameters import FrameParamRecord, ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.parameter_gui.gui import ParameterGUI
from grafix.interactive.midi import MidiSession
from grafix.interactive.diagnostics import DiagnosticCenter


class _Popup:
    def __init__(self, *, opened: bool) -> None:
        self.opened = bool(opened)

    def __enter__(self) -> _Popup:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Imgui:
    COLOR_TEXT = 0

    def __init__(
        self,
        *,
        click_clear: bool,
        click_undo: bool = False,
        query: str = "",
        click_filter: bool = False,
        clicked_filter_ids: set[str] | None = None,
        clicked_button_ids: set[str] | None = None,
    ) -> None:
        self.click_clear = bool(click_clear)
        self.click_undo = bool(click_undo)
        self.query = str(query)
        self.click_filter = bool(click_filter)
        self.clicked_filter_ids = set(clicked_filter_ids or set())
        self.clicked_button_ids = set(clicked_button_ids or set())
        self.checkbox_labels: list[str] = []
        self.menu_enabled: list[bool] = []
        self.menu_enabled_by_id: dict[str, bool] = {}
        self.opened_popups: list[str] = []
        self.disabled_text: list[str] = []
        self.button_labels: list[str] = []
        self.layout_events: list[str] = []

    def text_disabled(self, text: str) -> None:
        self.disabled_text.append(str(text))

    def align_text_to_frame_padding(self) -> None:
        pass

    def text(self, _text: str) -> None:
        pass

    def same_line(self, position: float = 0.0, spacing: float = -1.0) -> None:
        self.layout_events.append(f"same_line:{float(position)}:{float(spacing)}")

    def input_text_with_hint(
        self,
        _label: str,
        _hint: str,
        value: str,
    ) -> tuple[bool, str]:
        return self.query != str(value), self.query

    def set_next_item_width(self, _width: float) -> None:
        pass

    def checkbox(self, label: str, _value: bool) -> tuple[bool, bool]:
        self.checkbox_labels.append(label)
        return True, True

    def button(self, label: str, width: float = 0.0, height: float = 0.0) -> bool:
        self.button_labels.append(str(label))
        widget_id = label.rpartition("##")[2]
        self.layout_events.append(f"button:{widget_id}")
        if widget_id == "midi_menu":
            return True
        if widget_id == "parameter_filter_menu":
            return self.click_filter
        if widget_id == "midi_clear_notice_undo":
            return self.click_undo
        return widget_id in self.clicked_button_ids

    def open_popup(self, label: str) -> None:
        self.opened_popups.append(label)

    def begin_popup(self, label: str) -> _Popup:
        return _Popup(opened=label in self.opened_popups)

    def menu_item(
        self,
        label: str,
        _shortcut: str | None = None,
        selected: bool = False,
        enabled: bool = True,
    ) -> tuple[bool, bool]:
        widget_id = label.rpartition("##")[2]
        self.menu_enabled_by_id[widget_id] = bool(enabled)
        if widget_id == "clear_midi_assigns":
            self.menu_enabled.append(bool(enabled))
            return self.click_clear and bool(enabled), bool(selected)
        return widget_id in self.clicked_filter_ids and bool(enabled), bool(selected)

    def separator(self) -> None:
        pass

    def push_style_color(self, *_args: object) -> None:
        pass

    def pop_style_color(self, _count: int = 1) -> None:
        pass


def _setup_with_mapping(
    gui: ParameterGUI,
) -> tuple[Any, ParamStore, ParameterKey]:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site", arg="radius")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.25,
                meta=meta,
                effective=0.25,
                source="code",
                explicit=False,
            )
        ],
    )
    ok, error = update_state_from_ui(
        store,
        key,
        0.5,
        meta=meta,
        override=True,
        cc_key=12,
    )
    assert ok and error is None

    gui_state = cast(Any, gui)
    gui_state._store = store
    gui_state._session.show_inactive_parameters = False
    gui_state._session.midi_clear_notice = None
    gui_state._history = None
    gui_state._session.midi_learn = SimpleNamespace(
        active_target="target",
        active_component=0,
    )
    return gui_state, store, key


def test_table_toolbar_names_filter_and_moves_clear_into_midi_menu(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, store, key = _setup_with_mapping(initialized_parameter_gui)
    imgui = _Imgui(click_clear=True)
    gui._imgui = imgui

    assert gui._render_parameter_table_toolbar() is True

    assert imgui.checkbox_labels == ["Show inactive##show_inactive_params"]
    assert imgui.opened_popups == ["MIDI mappings##midi_menu_popup"]
    assert imgui.menu_enabled == [True]
    assert gui._session.show_inactive_parameters is True
    assert gui._session.midi_learn.active_target is None
    assert gui._session.midi_learn.active_component is None
    notice = gui._session.midi_clear_notice
    assert notice is not None and notice.message == "MIDI mappings cleared"
    assert imgui.button_labels.index("MIDI##midi_menu") < imgui.button_labels.index(
        "Expand all##parameter_groups_expand_all"
    )
    state = store.get_state(key)
    assert state is not None and state.cc_key is None


def test_midi_menu_follows_filters_without_manual_right_alignment(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, _store, _key = _setup_with_mapping(initialized_parameter_gui)
    imgui = _Imgui(click_clear=False)
    gui._imgui = imgui

    assert gui._render_parameter_table_toolbar() is False

    filter_event = imgui.layout_events.index("button:parameter_filter_menu")
    midi_event = imgui.layout_events.index("button:midi_menu")
    assert imgui.layout_events[filter_event : midi_event + 1] == [
        "button:parameter_filter_menu",
        "same_line:0.0:-1.0",
        "button:midi_menu",
    ]


def test_table_toolbar_updates_search_filter_and_displays_filtered_count(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, _store, _key = _setup_with_mapping(initialized_parameter_gui)
    imgui = _Imgui(
        click_clear=False,
        query="CIRCLE MIDI 12",
        click_filter=True,
        clicked_filter_ids={"filter_activity_active", "filter_midi_mapped"},
    )
    gui._imgui = imgui

    assert gui._render_parameter_table_toolbar() is False

    assert gui._session.filter_state.query == "CIRCLE MIDI 12"
    assert gui._session.filter_state.activity == "active"
    assert gui._session.filter_state.midi_mapped_only is True
    assert "1 / 1 parameters" in imgui.disabled_text


def test_clear_all_menu_item_is_disabled_without_mappings(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, store, key = _setup_with_mapping(initialized_parameter_gui)
    state = store.get_state(key)
    meta = store.get_meta(key)
    assert state is not None and meta is not None
    ok, error = update_state_from_ui(
        store,
        key,
        state.ui_value,
        meta=meta,
        override=state.override,
        cc_key=None,
    )
    assert ok and error is None
    imgui = _Imgui(click_clear=True)
    gui._imgui = imgui

    assert gui._render_parameter_table_toolbar() is False
    assert imgui.menu_enabled == [False]
    assert gui._session.midi_clear_notice is None


def test_midi_disabled_session_does_not_enable_or_run_reconnect(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, _store, _key = _setup_with_mapping(initialized_parameter_gui)
    center = DiagnosticCenter()
    session = MidiSession(
        controller=None,
        snapshot_load_result=None,
        diagnostics=center,
    )
    gui._midi_session = session
    imgui = _Imgui(
        click_clear=False,
        clicked_filter_ids={"midi_reconnect"},
    )
    gui._imgui = imgui

    assert gui._render_midi_mapping_menu() is False
    assert imgui.menu_enabled_by_id["midi_reconnect"] is False
    assert session.can_reconnect is False
    assert center.snapshot() == ()


def test_clear_notice_exposes_one_click_undo(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, store, key = _setup_with_mapping(initialized_parameter_gui)
    history = ParamStoreHistory(store)
    gui._history = history
    gui._imgui = _Imgui(click_clear=True)
    assert gui._render_parameter_table_toolbar() is True
    assert history.undo_depth == 1

    gui._imgui = _Imgui(click_clear=False, click_undo=True)
    assert gui._render_midi_clear_notice() is True

    state = store.get_state(key)
    assert state is not None and state.cc_key == 12
    assert gui._session.midi_clear_notice is None


def test_clear_notice_disappears_instead_of_undoing_a_later_edit(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, store, key = _setup_with_mapping(initialized_parameter_gui)
    history = ParamStoreHistory(store)
    gui._history = history
    gui._imgui = _Imgui(click_clear=True)
    assert gui._render_parameter_table_toolbar() is True

    meta = store.get_meta(key)
    assert meta is not None
    with history.transaction(source="later_parameter_edit"):
        ok, error = update_state_from_ui(store, key, 0.9, meta=meta, override=True)
        assert ok and error is None
    assert history.undo_depth == 2

    gui._imgui = _Imgui(click_clear=False, click_undo=True)
    assert gui._render_midi_clear_notice() is False
    state = store.get_state(key)
    assert state is not None and state.ui_value == 0.9
    assert gui._session.midi_clear_notice is None


def test_collapse_all_is_an_independent_undoable_operation(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    gui, store, _key = _setup_with_mapping(initialized_parameter_gui)
    history = ParamStoreHistory(store)
    gui._history = history
    gui._imgui = _Imgui(
        click_clear=False,
        clicked_button_ids={"parameter_groups_collapse_all"},
    )

    assert gui._render_parameter_table_toolbar() is True
    assert history.undo_depth == 1
    assert store.collapsed_headers()

    assert history.undo() is True
    assert store.collapsed_headers() == set()


def test_vec3_menu_counts_each_assigned_component(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="site", arg="xyz")
    meta = ParamMeta(kind="vec3", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=(0.0, 0.0, 0.0),
                meta=meta,
                effective=(0.0, 0.0, 0.0),
                source="code",
                explicit=False,
            )
        ],
    )
    ok, error = update_state_from_ui(
        store,
        key,
        (0.1, 0.2, 0.3),
        meta=meta,
        cc_key=(10, 11, 12),
    )
    assert ok and error is None
    gui = cast(Any, initialized_parameter_gui)
    gui._store = store
    gui._history = None
    gui._session.show_inactive_parameters = False
    gui._session.midi_clear_notice = None
    gui._session.midi_learn = SimpleNamespace(active_target=None, active_component=None)
    imgui = _Imgui(click_clear=False)
    gui._imgui = imgui

    assert gui._render_parameter_table_toolbar() is False
    assert any("3 mappings" in text for text in imgui.disabled_text)
