"""operation selector と Parameter GUI/block/snippet の統合契約を検証する。"""

from __future__ import annotations

import sys
from dataclasses import replace
from typing import Literal

import pytest

from grafix import E, G
from grafix.api._operation_selector import (
    PRIMITIVE_SELECTOR_OP,
    effect_selector_op,
)
from grafix.core.geometry import Geometry
from grafix.core.operation_authoring import effect, primitive
from grafix.core.operation_catalog import OperationCatalogBuilder, current_operation_catalog
from grafix.core.operation_selector import (
    decode_selector_param_key,
    selector_kind,
)
from grafix.core.parameters import ParamStore, ParameterKey, parameter_context
from grafix.core.parameters.codec import (
    dumps_param_store,
    loads_param_store_result,
)
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.preset_catalog import current_preset_catalog
from grafix.core.realized_geometry import GeomTuple
from grafix.interactive.parameter_gui.group_blocks import (
    GroupBlockLayout,
)
from grafix.interactive.parameter_gui.catalog import (
    ParameterGuiCatalog,
    current_parameter_gui_catalog,
)
from grafix.interactive.parameter_gui.grouping import GroupType
from grafix.interactive.parameter_gui.help_pane import parameter_help_content
from grafix.interactive.parameter_gui.snippet import snippet_for_block
from grafix.interactive.parameter_gui.store_bridge import (
    ParameterTableView,
    parameter_table_view_for_store,
)
from grafix.interactive.parameter_gui.table import _effect_step_heading_by_rows
from grafix.interactive.parameter_gui.widgets import widget_choice_radio
from grafix.interactive.parameter_gui.session_state import WidgetSessionState

SelectorKind = Literal["primitive", "effect"]
_SOURCE = Geometry.create(op="selector_test_source")


@effect(n_inputs=3, meta={"amount": ParamMeta(kind="float")})
def selector_test_ternary_effect(
    first: GeomTuple,
    _second: GeomTuple,
    _third: GeomTuple,
    *,
    amount: float = 1.0,
) -> GeomTuple:
    """候補消失後の stale selector GUI を検証する 3 入力 effect。"""

    _ = amount
    return first


def _draw_primitive_selector(store: ParamStore) -> Geometry:
    with parameter_context(store):
        return G.select(
            target="circle",
            params_by_target={
                "circle": {"radius": 2.0},
                "rect": {"width": 3.0},
            },
            key="selector-gui-shape",
        )


def _draw_effect_selector(store: ParamStore) -> Geometry:
    with parameter_context(store):
        return E.select(
            target="rotate",
            params_by_target={
                "rotate": {
                    "rotation": (0.0, 0.0, 20.0),
                }
            },
            key="selector-gui-effect",
        )(_SOURCE)


def _selector_parameter_key(
    store: ParamStore,
    *,
    kind: SelectorKind,
    target: str | None,
    arg: str,
) -> ParameterKey:
    matches: list[ParameterKey] = []
    for key in store_snapshot(store):
        if selector_kind(key.op) != kind:
            continue
        if target is None:
            if key.arg == arg:
                matches.append(key)
            continue
        if decode_selector_param_key(key.arg) == (target, arg):
            matches.append(key)
    assert len(matches) == 1
    return matches[0]


def _set_ui_value(store: ParamStore, key: ParameterKey, value: object) -> None:
    meta = store.get_meta(key)
    assert meta is not None
    ok, error = update_state_from_ui(
        store,
        key,
        value,
        meta=meta,
        override=True,
    )
    assert ok is True
    assert error is None


def _selector_view_and_block(
    store: ParamStore,
    *,
    kind: SelectorKind,
    catalog: ParameterGuiCatalog | None = None,
) -> tuple[ParameterTableView, GroupBlockLayout]:
    view = parameter_table_view_for_store(
        store,
        catalog=catalog,
        show_inactive_params=False,
    )
    selector_blocks = [
        block
        for block in view.group_layout
        if block.items
        and selector_kind(
            view.model.rows[block.items[0].row_index].op
        )
        == kind
    ]
    assert len(selector_blocks) == 1
    return view, selector_blocks[0]


def test_g_select_switches_target_and_preserves_each_target_state() -> None:
    store = ParamStore()

    circle = _draw_primitive_selector(store)
    _view, circle_block = _selector_view_and_block(store, kind="primitive")
    assert circle.op == "circle"
    assert dict(circle.args)["radius"] == 2.0
    assert [item.visible_label for item in circle_block.items] == [
        "Operation",
        "Activate",
        "Radius",
        "Segments",
        "Center",
    ]

    target_key = _selector_parameter_key(
        store,
        kind="primitive",
        target=None,
        arg="target",
    )
    circle_radius_key = _selector_parameter_key(
        store,
        kind="primitive",
        target="circle",
        arg="radius",
    )
    _set_ui_value(store, circle_radius_key, 4.25)
    _set_ui_value(store, target_key, "rect")

    rect = _draw_primitive_selector(store)
    assert rect.op == "rect"
    assert dict(rect.args)["width"] == 3.0
    rect_width_key = _selector_parameter_key(
        store,
        kind="primitive",
        target="rect",
        arg="width",
    )
    _set_ui_value(store, rect_width_key, 8.5)
    _set_ui_value(store, target_key, "circle")

    restored_circle = _draw_primitive_selector(store)
    assert restored_circle.op == "circle"
    assert dict(restored_circle.args)["radius"] == 4.25
    _set_ui_value(store, target_key, "rect")

    restored_rect = _draw_primitive_selector(store)
    assert restored_rect.op == "rect"
    assert dict(restored_rect.args)["width"] == 8.5
    assert store.get_state(circle_radius_key).ui_value == 4.25  # type: ignore[union-attr]
    assert store.get_state(rect_width_key).ui_value == 8.5  # type: ignore[union-attr]

    _view, rect_block = _selector_view_and_block(store, kind="primitive")
    visible_labels = [item.visible_label for item in rect_block.items]
    assert visible_labels == [
        "Operation",
        "Activate",
        "Width",
        "Height",
        "Angle",
        "Center",
    ]
    visible_text = " ".join((rect_block.header or "", *visible_labels))
    assert rect_block.header == "select"
    assert "_grafix" not in visible_text
    assert "@" not in visible_text
    assert "Radius" not in visible_labels
    assert all("_grafix" not in corpus and "@" not in corpus for corpus in _view.model.search_corpus_by_row)
    help_identities = {
        parameter_help_content(_view.model.rows[item.row_index]).identity
        for item in rect_block.items
    }
    assert "G.select.rect.width" in help_identities
    assert all("_grafix" not in identity and "@" not in identity for identity in help_identities)


def test_e_select_composes_target_specific_ui_visible_with_selector_target() -> None:
    store = ParamStore()

    rotate = _draw_effect_selector(store)
    _view, block = _selector_view_and_block(store, kind="effect")
    assert rotate.op == "rotate"
    assert dict(rotate.args)["auto_center"] is True
    assert block.group_id[0] is GroupType.EFFECT_CHAIN
    assert [item.visible_label for item in block.items] == [
        "Operation",
        "Activate",
        "Auto center",
        "Rotation",
    ]
    block_rows = [_view.model.rows[item.row_index] for item in block.items]
    assert set(_effect_step_heading_by_rows(block_rows).values()) == {
        "Select"
    }
    assert all(
        decoded is None or decoded[0] == "rotate"
        for decoded in (
            decode_selector_param_key(row.arg) for row in block_rows
        )
    )

    auto_center_key = _selector_parameter_key(
        store,
        kind="effect",
        target="rotate",
        arg="auto_center",
    )
    _set_ui_value(store, auto_center_key, False)

    rotate_with_pivot = _draw_effect_selector(store)
    _view, block_with_pivot = _selector_view_and_block(store, kind="effect")
    assert dict(rotate_with_pivot.args)["auto_center"] is False
    assert [item.visible_label for item in block_with_pivot.items] == [
        "Operation",
        "Activate",
        "Auto center",
        "Pivot",
        "Rotation",
    ]


def test_effect_step_headings_number_selectors_with_different_arities() -> None:
    store = ParamStore()
    first = G.line(key="selector-heading-first")
    second = G.line(key="selector-heading-second")
    with parameter_context(store):
        E.select(
            target="boolean",
            n_inputs=2,
            key="selector-heading-binary",
        ).select(
            target="rotate",
            key="selector-heading-unary",
        )(first, second)

    view = parameter_table_view_for_store(
        store,
        show_inactive_params=False,
    )
    selector_rows = [
        row for row in view.model.rows if selector_kind(row.op) == "effect"
    ]
    headings = _effect_step_heading_by_rows(selector_rows)
    assert set(headings.values()) == {"Select 1", "Select 2"}


def test_g_select_snippet_uses_public_api_and_hides_internal_names() -> None:
    store = ParamStore()
    _draw_primitive_selector(store)
    view, block = _selector_view_and_block(store, kind="primitive")

    snippet = snippet_for_block(
        block,
        view.model.rows,
        step_info_by_site=view.model.step_info_by_site,
        raw_label_by_site=view.model.raw_label_by_site,
    )

    assert "G.select(" in snippet
    assert "target='circle'" in snippet
    assert "params_by_target={'circle':" in snippet
    assert "G.circle(" not in snippet
    assert PRIMITIVE_SELECTOR_OP not in snippet
    assert "_grafix" not in snippet
    assert "@" not in snippet


def test_e_select_snippet_uses_public_api_and_hides_internal_names() -> None:
    store = ParamStore()
    _draw_effect_selector(store)
    view, block = _selector_view_and_block(store, kind="effect")

    snippet = snippet_for_block(
        block,
        view.model.rows,
        step_info_by_site=view.model.step_info_by_site,
        raw_label_by_site=view.model.raw_label_by_site,
    )

    assert "E.select(" in snippet
    assert "target='rotate'" in snippet
    assert "n_inputs=1" in snippet
    assert "params_by_target={'rotate':" in snippet
    assert "'pivot': (0.0, 0.0, 0.0)" in snippet
    assert "E.rotate(" not in snippet
    assert effect_selector_op(1) not in snippet
    assert "_grafix" not in snippet
    assert "@" not in snippet


def test_selector_snippet_returns_note_for_required_non_gui_argument() -> None:
    @primitive
    def selector_test_snippet_required_hidden(
        *,
        extent: float,
    ) -> GeomTuple:
        _ = extent
        raise AssertionError("Copy Code 検証では evaluator を実行してはならない")

    store = ParamStore()
    with parameter_context(store):
        G.select(
            target="selector_test_snippet_required_hidden",
            params_by_target={
                "selector_test_snippet_required_hidden": {"extent": 7.0}
            },
            key="selector-snippet-required-hidden",
        )
    view, block = _selector_view_and_block(store, kind="primitive")

    snippet = snippet_for_block(
        block,
        view.model.rows,
        step_info_by_site=view.model.step_info_by_site,
        raw_label_by_site=view.model.raw_label_by_site,
    )

    assert "NOTE:" in snippet
    assert "selector_test_snippet_required_hidden" in snippet
    assert "GUI 非公開引数 'extent'" in snippet
    assert "params_by_target['selector_test_snippet_required_hidden']" in snippet
    assert "G.select(" not in snippet


def test_selector_target_states_survive_recovery_roundtrip() -> None:
    store = ParamStore()
    _draw_primitive_selector(store)
    target_key = _selector_parameter_key(
        store,
        kind="primitive",
        target=None,
        arg="target",
    )
    radius_key = _selector_parameter_key(
        store,
        kind="primitive",
        target="circle",
        arg="radius",
    )
    radius_meta = store.get_meta(radius_key)
    assert radius_meta is not None
    ok, error = update_state_from_ui(
        store,
        radius_key,
        6.75,
        meta=radius_meta,
        override=True,
        cc_key=14,
    )
    assert ok is True
    assert error is None
    _set_ui_value(store, target_key, "rect")
    _draw_primitive_selector(store)
    width_key = _selector_parameter_key(
        store,
        kind="primitive",
        target="rect",
        arg="width",
    )
    _set_ui_value(store, width_key, 9.5)

    loaded = loads_param_store_result(
        dumps_param_store(store, preserve_explicit_overrides=True),
        preserve_explicit_overrides=True,
    ).store
    restored_rect = _draw_primitive_selector(loaded)
    assert restored_rect.op == "rect"
    assert dict(restored_rect.args)["width"] == 9.5

    loaded_target_key = _selector_parameter_key(
        loaded,
        kind="primitive",
        target=None,
        arg="target",
    )
    _set_ui_value(loaded, loaded_target_key, "circle")
    restored_circle = _draw_primitive_selector(loaded)
    assert restored_circle.op == "circle"
    assert dict(restored_circle.args)["radius"] == 6.75
    restored_radius_key = _selector_parameter_key(
        loaded,
        kind="primitive",
        target="circle",
        arg="radius",
    )
    restored_radius_state = loaded.get_state(restored_radius_key)
    assert restored_radius_state is not None
    assert restored_radius_state.cc_key == 14


def test_omitted_target_is_implicit_and_survives_normal_roundtrip() -> None:
    implicit_store = ParamStore()
    with parameter_context(implicit_store):
        G.select(key="implicit-selector-target")
    implicit_target_key = _selector_parameter_key(
        implicit_store,
        kind="primitive",
        target=None,
        arg="target",
    )
    implicit_state = implicit_store.get_state(implicit_target_key)
    assert implicit_state is not None
    assert implicit_state.override is True
    _set_ui_value(implicit_store, implicit_target_key, "rect")

    loaded = loads_param_store_result(dumps_param_store(implicit_store)).store
    with parameter_context(loaded):
        restored = G.select(key="implicit-selector-target")
    assert restored.op == "rect"

    explicit_store = ParamStore()
    with parameter_context(explicit_store):
        G.select(
            target="circle",
            key="explicit-selector-target",
        )
        E.select(
            target="rotate",
            key="explicit-effect-selector-target",
        )(_SOURCE)
    explicit_states = [
        state
        for key in store_snapshot(explicit_store)
        if key.arg == "target"
        for state in [explicit_store.get_state(key)]
    ]
    assert len(explicit_states) == 2
    assert all(state is not None and state.override is False for state in explicit_states)


def test_table_projects_selector_schema_without_mutating_catalog() -> None:
    primitive_store = ParamStore()
    effect_store = ParamStore()
    _draw_primitive_selector(primitive_store)
    _draw_effect_selector(effect_store)
    catalog = current_parameter_gui_catalog()
    before = catalog.entries()
    effect_op = effect_selector_op(1)

    _view, primitive_block = _selector_view_and_block(
        primitive_store,
        kind="primitive",
        catalog=catalog,
    )
    _view, effect_block = _selector_view_and_block(
        effect_store,
        kind="effect",
        catalog=catalog,
    )

    assert catalog.entries() == before
    primitive_selector = catalog.resolve(PRIMITIVE_SELECTOR_OP)
    effect_selector = catalog.resolve(effect_op)
    assert primitive_selector is not None and primitive_selector.kind == "selector"
    assert effect_selector is not None and effect_selector.kind == "selector"
    assert primitive_block.header == "select"
    assert "Radius" in {item.visible_label for item in primitive_block.items}
    assert "Width" not in {item.visible_label for item in primitive_block.items}
    assert effect_block.group_id[0] is GroupType.EFFECT_CHAIN
    assert "Pivot" not in {item.visible_label for item in effect_block.items}


def test_table_keeps_stale_selector_group_when_arity_catalog_disappears() -> None:
    store = ParamStore()
    first = G.line(key="stale-selector-first")
    second = G.line(key="stale-selector-second")
    third = G.line(key="stale-selector-third")
    with parameter_context(store):
        selected = E.select(
            target="selector_test_ternary_effect",
            n_inputs=3,
            params_by_target={
                "selector_test_ternary_effect": {"amount": 2.0}
            },
            key="stale-ternary-selector",
        )(first, second, third)
    assert selected.op == "selector_test_ternary_effect"

    selector_op = effect_selector_op(3)
    reduced_builder = OperationCatalogBuilder()
    for entry in current_operation_catalog().entries():
        if not (
            entry.kind == "effect"
            and entry.name == "selector_test_ternary_effect"
        ):
            reduced_builder.register(entry.declaration)
    reduced_catalog = ParameterGuiCatalog.capture(
        reduced_builder.freeze(),
        current_preset_catalog(),
    )
    view = parameter_table_view_for_store(
        store,
        catalog=reduced_catalog,
        show_inactive_params=False,
    )
    target_row = next(
        row
        for row in view.model.rows
        if row.op == selector_op and row.arg == "target"
    )
    assert target_row.ui_value == "selector_test_ternary_effect"


def test_gui_store_keeps_catalog_snapshot_after_operation_overwrite() -> None:
    def selector_test_choice_reload_v1(
        *,
        mode: str = "a",
    ) -> GeomTuple:
        _ = mode
        raise AssertionError("Geometry recipe の構築時に評価してはならない")

    selector_test_choice_reload_v1.__name__ = "selector_test_choice_reload"
    primitive(
        meta={"mode": ParamMeta(kind="choice", choices=("a", "b"))}
    )(selector_test_choice_reload_v1)

    store = ParamStore()
    with parameter_context(store):
        G.select(
            target="selector_test_choice_reload",
            params_by_target={"selector_test_choice_reload": {"mode": "a"}},
            key="selector-choice-reload",
        )
    mode_key = _selector_parameter_key(
        store,
        kind="primitive",
        target="selector_test_choice_reload",
        arg="mode",
    )
    _set_ui_value(store, mode_key, "b")
    parameter_table_view_for_store(store, show_inactive_params=False)

    def selector_test_choice_reload_v2(
        *,
        mode: str = "c",
    ) -> GeomTuple:
        _ = mode
        raise AssertionError("Geometry recipe の構築時に評価してはならない")

    selector_test_choice_reload_v2.__name__ = "selector_test_choice_reload"
    primitive(
        overwrite=True,
        meta={"mode": ParamMeta(kind="choice", choices=("c", "d"))},
    )(selector_test_choice_reload_v2)

    view = parameter_table_view_for_store(
        store,
        show_inactive_params=False,
    )
    mode_row = next(row for row in view.model.rows if row.arg == mode_key.arg)
    assert tuple(mode_row.choices or ()) == ("a", "b")

    new_store = ParamStore()
    with parameter_context(new_store):
        G.select(
            target="selector_test_choice_reload",
            params_by_target={"selector_test_choice_reload": {"mode": "c"}},
            key="selector-choice-reload-new-session",
        )
    new_view = parameter_table_view_for_store(
        new_store,
        show_inactive_params=False,
    )
    new_mode_row = next(
        row
        for row in new_view.model.rows
        if decode_selector_param_key(row.arg)
        == ("selector_test_choice_reload", "mode")
    )
    assert tuple(new_mode_row.choices or ()) == ("c", "d")


def test_gui_uses_current_default_for_incompatible_target_kind_change() -> None:
    def selector_test_kind_reload_v1(
        *,
        value: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> GeomTuple:
        _ = value
        raise AssertionError("Geometry recipe の構築時に評価してはならない")

    selector_test_kind_reload_v1.__name__ = "selector_test_kind_reload"
    primitive(meta={"value": ParamMeta(kind="vec3")})(
        selector_test_kind_reload_v1
    )

    store = ParamStore()
    with parameter_context(store):
        G.select(
            target="selector_test_kind_reload",
            key="selector-kind-reload",
        )
    value_key = _selector_parameter_key(
        store,
        kind="primitive",
        target="selector_test_kind_reload",
        arg="value",
    )

    def selector_test_kind_reload_v2(
        *,
        value: bool = False,
    ) -> GeomTuple:
        _ = value
        raise AssertionError("Geometry recipe の構築時に評価してはならない")

    selector_test_kind_reload_v2.__name__ = "selector_test_kind_reload"
    primitive(
        overwrite=True,
        meta={"value": ParamMeta(kind="bool")},
    )(selector_test_kind_reload_v2)

    view = parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    value_row = next(row for row in view.model.rows if row.arg == value_key.arg)
    assert value_row.kind == "bool"
    assert value_row.ui_value is False


class _SelectorComboImgui:
    COMBO_HEIGHT_LARGE = 8

    def __init__(self, click: str | None = None) -> None:
        self.click = click
        self.selected: list[tuple[str, bool]] = []
        self.preview: str | None = None
        self.end_combo_calls = 0
        self.opened = True

    def begin_combo(
        self,
        _label: str,
        preview: str,
        flags: int = 0,
    ) -> _SelectorComboImgui:
        assert flags == self.COMBO_HEIGHT_LARGE
        self.preview = str(preview)
        return self

    def __enter__(self) -> _SelectorComboImgui:
        return self

    def __exit__(self, *_args: object) -> None:
        self.end_combo_calls += 1

    def selectable(self, label: str, selected: bool) -> tuple[bool, bool]:
        choice = label.split("##", 1)[0]
        self.selected.append((choice, bool(selected)))
        clicked = choice == self.click
        return clicked, clicked

    def set_item_default_focus(self) -> None:
        return

def test_removed_selector_target_is_not_silently_coerced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParamStore()
    _draw_primitive_selector(store)
    view, _block = _selector_view_and_block(store, kind="primitive")
    target_row = next(row for row in view.model.rows if row.arg == "target")
    stale_target_row = replace(
        target_row,
        ui_value="removed_target",
        choices=("circle", "rect"),
    )
    idle_imgui = _SelectorComboImgui()
    monkeypatch.setitem(sys.modules, "imgui", idle_imgui)
    widget_state = WidgetSessionState()

    changed, value = widget_choice_radio(stale_target_row, state=widget_state)

    assert changed is False
    assert value == "removed_target"
    assert idle_imgui.preview == "removed_target (unavailable)"
    assert idle_imgui.selected == [("circle", False), ("rect", False)]
    assert idle_imgui.end_combo_calls == 1

    selecting_imgui = _SelectorComboImgui(click="circle")
    monkeypatch.setitem(sys.modules, "imgui", selecting_imgui)
    changed, value = widget_choice_radio(stale_target_row, state=widget_state)
    assert changed is True
    assert value == "circle"
    assert selecting_imgui.end_combo_calls == 1
