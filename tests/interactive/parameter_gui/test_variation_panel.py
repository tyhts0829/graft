from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from grafix.core.parameters.favorites import set_parameters_favorite
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.parameters.variations import (
    create_variation,
    list_variations,
    set_parameters_locked,
)
from grafix.interactive.parameter_gui.gui import ParameterGUI
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)
from grafix.interactive.parameter_gui.variation_controller import VariationController
from grafix.interactive.parameter_gui.variation_panel import (
    normalize_variation_selection,
    variation_panel_model,
    variation_scope_summary,
)


META = ParamMeta(
    kind="float",
    ui_min=0.0,
    ui_max=10.0,
    recommended_range=(1.0, 9.0),
)


def _store() -> tuple[ParamStore, ParameterKey, ParameterKey]:
    store = ParamStore()
    key_a = ParameterKey("circle", "site-a", "radius")
    key_b = ParameterKey("circle", "site-b", "radius")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key_a,
                base=2.0,
                meta=META,
                effective=2.0,
                source="code",
                explicit=False,
            ),
            FrameParamRecord(
                key=key_b,
                base=4.0,
                meta=META,
                effective=4.0,
                source="code",
                explicit=False,
            ),
        ],
    )
    return store, key_a, key_b


def _set(store: ParamStore, key: ParameterKey, value: float) -> None:
    ok, error = update_state_from_ui(store, key, value, meta=META, override=True)
    assert ok and error is None


def _gui(
    gui: ParameterGUI,
    store: ParamStore,
) -> Any:
    gui_state = cast(Any, gui)
    gui_state._store = store
    gui_state._history = ParamStoreHistory(store)
    gui_state._transport = None
    gui_state._session.show_inactive_parameters = True
    gui_state._session.filter_state = ParameterFilterState()
    gui_state._session.error_keys = frozenset()
    gui_state._session.favorite_keys = frozenset()
    gui_state._session.table_view = None
    gui_state._variation_controller = VariationController(
        store,
        history=gui_state._history,
    )
    return gui_state


def test_panel_model_displays_metadata_diff_count_and_empty_state() -> None:
    store, key_a, _key_b = _store()
    empty = variation_panel_model(store)
    assert empty.items == ()
    assert empty.empty_message == "No saved variations yet."

    create_variation(
        store,
        "calm",
        note="soft motion",
        seed=7,
        thumbnail_path="calm.png",
        created_at=0.0,
    )
    _set(store, key_a, 8.0)

    model = variation_panel_model(store)

    assert model.count == 1
    assert model.items[0].name == "calm"
    assert model.items[0].note == "soft motion"
    assert model.items[0].timestamp == "1970-01-01 00:00:00 UTC"
    assert model.items[0].seed == 7
    assert model.items[0].diff_count == 1
    assert model.items[0].thumbnail_path == Path("calm.png")


def test_scope_model_uses_current_filter_or_all_favorites_and_counts_locks(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store, key_a, key_b = _store()
    set_parameters_favorite(store, (key_a,), favorite=True)
    set_parameters_locked(store, (key_a,), locked=True)
    filtered_view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
        filter_state=ParameterFilterState(query="site-b"),
    )

    filtered = variation_scope_summary(store, filtered_view, "filtered")
    favorites = variation_scope_summary(store, filtered_view, "favorites")

    assert filtered.keys == (key_b,)
    assert filtered.locked_count == 0
    assert favorites.keys == (key_a,)
    assert favorites.locked_count == 1


def test_selection_falls_back_after_rename_or_delete() -> None:
    assert normalize_variation_selection(("a", "b"), "b") == "b"
    assert normalize_variation_selection(("a", "b"), "missing") == "a"
    assert normalize_variation_selection((), "missing") is None


class _OpenedModal:
    opened = True

    def __enter__(self) -> _OpenedModal:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _DeleteConfirmationImgui:
    def __init__(self, *, confirm: bool) -> None:
        self.confirm = bool(confirm)
        self.labels: list[str] = []
        self.messages: list[str] = []
        self.closed = False

    def begin_popup_modal(self, label: str) -> _OpenedModal:
        self.labels.append(label)
        return _OpenedModal()

    def text_wrapped(self, message: str) -> None:
        self.messages.append(str(message))

    def text_disabled(self, message: str) -> None:
        self.messages.append(str(message))

    def button(self, label: str) -> bool:
        return self.confirm and str(label).startswith("Delete permanently")

    def same_line(self) -> None:
        return None

    def close_current_popup(self) -> None:
        self.closed = True


def test_delete_confirmation_modal_names_target_before_permanent_delete(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    store, _key_a, _key_b = _store()
    create_variation(store, "precious")
    gui = _gui(initialized_parameter_gui, store)
    state = gui._variation_controller.state
    state.selected_name = "precious"
    assert gui._variation_controller.request_delete_selected() is True
    assert list_variations(store)[0].name == "precious"

    imgui = _DeleteConfirmationImgui(confirm=True)
    gui._imgui = imgui

    assert gui._render_variation_delete_confirmation() is True
    assert imgui.closed is True
    assert any("precious" in message for message in imgui.messages)
    assert list_variations(store) == ()


def test_real_pyimgui_renders_empty_variation_popup(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    imgui = pytest.importorskip("imgui")
    store, _key_a, _key_b = _store()
    gui = _gui(initialized_parameter_gui, store)
    context = gui._backend._context
    imgui.set_current_context(context)
    try:
        io = imgui.get_io()
        io.display_size = (900.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.fonts.get_tex_data_as_rgba32()
        imgui.new_frame()
        imgui.begin("variation popup smoke")
        gui._imgui = imgui

        assert gui._render_variation_popup() is False

        imgui.end()
        imgui.render()
        assert imgui.get_draw_data() is not None
    finally:
        imgui.set_current_context(context)
