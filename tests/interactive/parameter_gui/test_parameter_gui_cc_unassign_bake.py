from __future__ import annotations

import sys
from dataclasses import replace

from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey, rows_from_snapshot
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.snapshot_ops import store_snapshot_for_gui
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.parameter_gui.store_bridge import _apply_updated_rows_to_store
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.table import render_parameter_row_4cols


class _ClosedPopup:
    opened = False

    def __enter__(self) -> _ClosedPopup:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _SourceSwitchImGui:
    """render_parameter_row_4cols を通す最小 ImGui double。"""

    COLOR_BUTTON = 0
    COLOR_BUTTON_HOVERED = 1
    COLOR_BUTTON_ACTIVE = 2
    COLOR_TEXT = 3
    STYLE_FRAME_PADDING = 4

    def push_id(self, _value: str) -> None:
        return None

    def pop_id(self) -> None:
        return None

    def table_next_row(self) -> None:
        return None

    def table_set_column_index(self, _index: int) -> None:
        return None

    def get_content_region_available_width(self) -> float:
        return 200.0

    def calc_text_size(self, text: str) -> tuple[float, float]:
        return float(len(text) * 7), 14.0

    def button(self, label: str, _width: float = 0.0) -> bool:
        return str(label).endswith("##source_ui")

    def small_button(self, _label: str) -> bool:
        return False

    def push_style_color(self, *_args: object) -> None:
        return None

    def pop_style_color(self, _count: int = 1) -> None:
        return None

    def get_style(self) -> object:
        return type("_Style", (), {"frame_padding": (4.0, 3.0)})()

    def push_style_var(self, *_args: object) -> None:
        return None

    def pop_style_var(self, _count: int = 1) -> None:
        return None

    def same_line(self, *_args: float) -> None:
        return None

    def text(self, _value: str) -> None:
        return None

    def open_popup(self, _label: str) -> None:
        return None

    def begin_popup(self, _label: str) -> _ClosedPopup:
        return _ClosedPopup()

    def menu_item(self, *_args: object) -> tuple[bool, bool]:
        return False, False

    def is_item_hovered(self) -> bool:
        return False

    def is_item_focused(self) -> bool:
        return False

    def set_tooltip(self, _text: str) -> None:
        return None

    def set_next_item_width(self, _width: float) -> None:
        return None

    def slider_float(
        self,
        _label: str,
        value: float,
        _minimum: float,
        _maximum: float,
    ) -> tuple[bool, float]:
        return False, float(value)

    def drag_float_range2(
        self,
        _label: str,
        current_min: float,
        current_max: float,
        *_args: object,
    ) -> tuple[bool, float, float]:
        return False, float(current_min), float(current_max)


def test_source_switch_changes_only_override_and_is_one_undo_unit() -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="source-switch", arg="r")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.2,
                meta=meta,
                effective=0.2,
                source="code",
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(
        store,
        key,
        0.7,
        meta=stored_meta,
        override=True,
        cc_key=12,
    )
    history = ParamStoreHistory(store)

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [replace(rows_before[0], override=False)]
    with history.transaction(source="parameter_gui"):
        _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.override is False
    assert state.cc_key == 12
    assert state.ui_value == 0.7
    assert history.undo_depth == 1

    assert history.undo() is True
    restored = store.get_state(key)
    assert restored is not None
    assert restored.override is True
    assert restored.cc_key == 12


def test_code_to_ui_render_store_undo_redo_keeps_midi(monkeypatch) -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="source-render", arg="r")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.2,
                meta=meta,
                effective=0.2,
                source="code",
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(
        store,
        key,
        0.7,
        meta=stored_meta,
        override=False,
        cc_key=12,
    )
    history = ParamStoreHistory(store)
    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)

    monkeypatch.setitem(sys.modules, "imgui", _SourceSwitchImGui())
    changed, rendered, _learn_state = render_parameter_row_4cols(
        rows_before[0],
        widget_state=WidgetSessionState(),
    )

    assert changed is True
    assert rendered.override is True
    assert rendered.cc_key == 12
    with history.transaction(source="parameter_gui"):
        _apply_updated_rows_to_store(store, snapshot, rows_before, [rendered])

    state = store.get_state(key)
    assert state is not None
    assert state.override is True
    assert state.cc_key == 12
    assert history.undo_depth == 1

    assert history.undo() is True
    undone = store.get_state(key)
    assert undone is not None
    assert undone.override is False
    assert undone.cc_key == 12

    assert history.redo() is True
    redone = store.get_state(key)
    assert redone is not None
    assert redone.override is True
    assert redone.cc_key == 12


def test_cc_unassign_bakes_scalar_effective_and_enables_override() -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s1", arg="r")
    meta_r = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.0,
                meta=meta_r,
                effective=0.0,
                source="code",
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(store, key, 0.1, meta=stored_meta, override=False, cc_key=12)
    store._runtime_ref().last_effective_by_key[key] = 0.75

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [replace(rows_before[0], cc_key=None)]
    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key is None
    assert state.override is True
    assert state.ui_value == 0.75


def test_cc_component_unassign_bakes_vec3_effective_and_keeps_other_cc() -> None:
    store = ParamStore()
    key = ParameterKey(op="scale", site_id="sv1", arg="p")
    meta_p = ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=(0.0, 0.0, 0.0),
                meta=meta_p,
                effective=(0.0, 0.0, 0.0),
                source="code",
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(
        store,
        key,
        (0.0, 0.0, 0.0),
        meta=stored_meta,
        override=False,
        cc_key=(10, 11, 12),
    )
    store._runtime_ref().last_effective_by_key[key] = (-1.0, 0.25, 1.0)

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [replace(rows_before[0], cc_key=(10, None, 12))]
    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key == (10, None, 12)
    assert state.override is True
    assert state.ui_value == (-1.0, 0.25, 1.0)


def test_cc_reassign_does_not_bake_effective() -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s2", arg="r")
    meta_r = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.0,
                meta=meta_r,
                effective=0.0,
                source="code",
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(store, key, 0.1, meta=stored_meta, override=False, cc_key=12)
    store._runtime_ref().last_effective_by_key[key] = 0.75

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [replace(rows_before[0], cc_key=64)]
    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key == 64
    assert state.override is False
    assert state.ui_value == 0.1


def test_explicit_reset_to_code_clears_midi_without_baking_effective() -> None:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="s3", arg="r")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=key,
                base=0.2,
                meta=meta,
                effective=0.2,
                source="code",
                explicit=True,
            )
        ],
    )
    stored_meta = store.get_meta(key)
    assert stored_meta is not None
    update_state_from_ui(
        store,
        key,
        0.1,
        meta=stored_meta,
        override=False,
        cc_key=12,
    )
    store._runtime_ref().last_effective_by_key[key] = 0.75

    snapshot = store_snapshot_for_gui(store)
    rows_before = rows_from_snapshot(snapshot)
    rows_after = [
        replace(
            rows_before[0],
            cc_key=None,
            override=False,
            reset_to_code=True,
        )
    ]
    _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)

    state = store.get_state(key)
    assert state is not None
    assert state.cc_key is None
    assert state.override is False
    assert state.ui_value == 0.1
