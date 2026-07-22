from grafix.core.parameters.store import ParamStore
from grafix.interactive.parameter_gui import table, widgets
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.session_state import ParameterGuiSessionState


def test_parameter_gui_session_state_owns_mutable_frame_state() -> None:
    session = ParameterGuiSessionState.for_store(ParamStore())

    assert session.filter_state == ParameterFilterState()
    assert session.table_view is None
    assert session.help_row is None
    assert session.midi_clear_notice is None
    assert session.reconcile_model is not None
    assert session.widgets.font_filter_by_key == {}
    assert session.widgets.choice_filter_by_key == {}
    assert session.widgets.snippet_popup_text == ""
    assert session.widgets.snippet_popup_focus_next is False

    session.show_inactive_parameters = True
    session.filter_state = ParameterFilterState(query="radius")
    session.parameter_edit_active = True
    session.invalidate_table()

    assert session.show_inactive_parameters is True
    assert session.filter_state.query == "radius"
    assert session.parameter_edit_active is True
    assert session.table_view is None


def test_widget_state_is_isolated_between_simultaneous_gui_sessions() -> None:
    key = ("text", "shared-site", "font")
    first = ParameterGuiSessionState.for_store(ParamStore())
    second = ParameterGuiSessionState.for_store(ParamStore())

    first.widgets.font_filter_by_key[key] = "noto sans"
    first.widgets.choice_filter_by_key[key] = "serif"
    first.widgets.snippet_popup_text = "G.text(...)"
    first.widgets.snippet_popup_focus_next = True

    assert first.widgets is not second.widgets
    assert second.widgets.font_filter_by_key == {}
    assert second.widgets.choice_filter_by_key == {}
    assert second.widgets.snippet_popup_text == ""
    assert second.widgets.snippet_popup_focus_next is False


def test_reopened_gui_session_does_not_reuse_closed_session_widget_state() -> None:
    key = ("selector", "same-site", "target")
    closed_session = ParameterGuiSessionState.for_store(ParamStore())
    closed_session.widgets.font_filter_by_key[key] = "old font"
    closed_session.widgets.choice_filter_by_key[key] = "old choice"
    closed_session.widgets.snippet_popup_text = "old snippet"
    closed_session.widgets.snippet_popup_focus_next = True

    reopened_session = ParameterGuiSessionState.for_store(ParamStore())

    assert reopened_session.widgets is not closed_session.widgets
    assert reopened_session.widgets.font_filter_by_key == {}
    assert reopened_session.widgets.choice_filter_by_key == {}
    assert reopened_session.widgets.snippet_popup_text == ""
    assert reopened_session.widgets.snippet_popup_focus_next is False


def test_widget_and_snippet_state_have_no_module_global_fallback() -> None:
    assert not hasattr(widgets, "_FONT_FILTER_BY_KEY")
    assert not hasattr(widgets, "_CHOICE_FILTER_BY_KEY")
    assert not hasattr(table, "_SNIPPET_POPUP_TEXT")
    assert not hasattr(table, "_SNIPPET_POPUP_FOCUS_NEXT")
