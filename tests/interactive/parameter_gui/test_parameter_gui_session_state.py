from grafix.core.parameters.store import ParamStore
from grafix.interactive.parameter_gui import table, widgets
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.session_state import ParameterGuiSessionState
from grafix.interactive.parameter_gui.table_view import parameter_table_view_for_store


def test_parameter_gui_session_state_owns_mutable_frame_state() -> None:
    session = ParameterGuiSessionState.for_store(
        ParamStore(),
        catalog=current_parameter_gui_catalog(),
    )

    assert session.filter_state == ParameterFilterState()
    assert session.table_view is None
    assert session.help_row is None
    assert session.midi_clear_notice is None
    assert session.reconcile_model is not None
    assert session.widgets.font_filter_by_key == {}
    assert session.widgets.font_choices is None
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
    catalog = current_parameter_gui_catalog()
    first = ParameterGuiSessionState.for_store(ParamStore(), catalog=catalog)
    second = ParameterGuiSessionState.for_store(ParamStore(), catalog=catalog)

    first.widgets.font_filter_by_key[key] = "noto sans"
    first.widgets.font_choices = (
        ("Noto Sans", "Nested/Noto Sans.ttf", False, "noto sans"),
    )
    first.widgets.choice_filter_by_key[key] = "serif"
    first.widgets.snippet_popup_text = "G.text(...)"
    first.widgets.snippet_popup_focus_next = True

    assert first.widgets is not second.widgets
    assert second.widgets.font_filter_by_key == {}
    assert second.widgets.font_choices is None
    assert second.widgets.choice_filter_by_key == {}
    assert second.widgets.snippet_popup_text == ""
    assert second.widgets.snippet_popup_focus_next is False


def test_reopened_gui_session_does_not_reuse_closed_session_widget_state() -> None:
    key = ("selector", "same-site", "target")
    catalog = current_parameter_gui_catalog()
    store = ParamStore()
    closed_session = ParameterGuiSessionState.for_store(
        store,
        catalog=catalog,
    )
    closed_session.table_view = parameter_table_view_for_store(
        store,
        cache=closed_session.table_cache,
        show_inactive_params=True,
    )
    closed_session.widgets.font_filter_by_key[key] = "old font"
    closed_session.widgets.font_choices = (
        ("Old", "Nested/Old.ttf", False, "old"),
    )
    closed_session.widgets.choice_filter_by_key[key] = "old choice"
    closed_session.widgets.snippet_popup_text = "old snippet"
    closed_session.widgets.snippet_popup_focus_next = True
    closed_cache = closed_session.table_cache

    closed_session.close()

    reopened_session = ParameterGuiSessionState.for_store(
        store,
        catalog=catalog,
    )

    assert closed_session.table_view is None
    assert closed_cache.model_build_count == 0
    assert closed_cache.view_build_count == 0
    assert reopened_session.table_cache is not closed_cache
    assert reopened_session.widgets is not closed_session.widgets
    assert reopened_session.widgets.font_filter_by_key == {}
    assert closed_session.widgets.font_choices is None
    assert reopened_session.widgets.font_choices is None
    assert reopened_session.widgets.choice_filter_by_key == {}
    assert reopened_session.widgets.snippet_popup_text == ""
    assert reopened_session.widgets.snippet_popup_focus_next is False


def test_catalog_exchange_only_invalidates_the_owning_session_cache() -> None:
    store = ParamStore()
    catalog = current_parameter_gui_catalog()
    first = ParameterGuiSessionState.for_store(store, catalog=catalog)
    second = ParameterGuiSessionState.for_store(store, catalog=catalog)
    first.table_view = parameter_table_view_for_store(
        store,
        cache=first.table_cache,
        show_inactive_params=True,
    )
    second.table_view = parameter_table_view_for_store(
        store,
        cache=second.table_cache,
        show_inactive_params=True,
    )
    first_cache = first.table_cache
    second_cache = second.table_cache
    second_view = second.table_view

    replacement = current_parameter_gui_catalog()
    first.replace_catalog(replacement)

    assert first.table_cache is not first_cache
    assert first.table_cache.catalog is replacement
    assert first.table_view is None
    assert first_cache.model_build_count == 0
    assert first_cache.view_build_count == 0
    assert second.table_cache is second_cache
    assert second.table_view is second_view
    assert second_cache.model_build_count == 1
    assert second_cache.view_build_count == 1
    assert (
        parameter_table_view_for_store(
            store,
            cache=second_cache,
            show_inactive_params=True,
        )
        is second_view
    )


def test_widget_and_snippet_state_have_no_module_global_fallback() -> None:
    assert not hasattr(widgets, "_FONT_FILTER_BY_KEY")
    assert not hasattr(widgets, "_CHOICE_FILTER_BY_KEY")
    assert not hasattr(table, "_SNIPPET_POPUP_TEXT")
    assert not hasattr(table, "_SNIPPET_POPUP_FOCUS_NEXT")
