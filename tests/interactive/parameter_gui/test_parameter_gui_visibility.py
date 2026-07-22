from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from grafix.api import preset
from grafix.core.geometry import Geometry
from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui import store_bridge
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.table import TableEdits
from grafix.interactive.parameter_gui.visibility import active_mask_for_rows


def _base_is(name: str):
    def _pred(v: Mapping[str, Any]) -> bool:
        return str(v.get("base", "")) == str(name)

    return _pred


UI_VISIBLE = {
    "cell_size": _base_is("square"),
    "ratio": _base_is("ratio_lines"),
    "boom": lambda _v: 1 / 0,
}


@preset(
    meta={
        "base": ParamMeta(kind="choice", choices=("square", "ratio_lines")),
        "cell_size": ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
        "ratio": ParamMeta(kind="float", ui_min=1.01, ui_max=10.0),
        "boom": ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
    },
    ui_visible=UI_VISIBLE,
)
def _vis_preset(
    *,
    base: str = "square",
    cell_size: float = 10.0,
    ratio: float = 1.618,
    boom: float = 0.0,
) -> Geometry:
    return Geometry.create(op="concat")


def _row(*, arg: str, value: object) -> ParameterRow:
    return ParameterRow(
        label=f"1:{arg}",
        op="preset._vis_preset",
        site_id="s:1",
        arg=str(arg),
        kind="float",
        ui_value=value,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=1,
    )


def test_active_mask_for_rows_hides_inactive_params() -> None:
    rows = [
        _row(arg="base", value="square"),
        _row(arg="cell_size", value=10.0),
        _row(arg="ratio", value=1.618),
    ]
    mask = active_mask_for_rows(rows, show_inactive=False, last_effective_by_key=None)
    assert mask == [True, True, False]

    rows2 = [
        _row(arg="base", value="ratio_lines"),
        _row(arg="cell_size", value=10.0),
        _row(arg="ratio", value=1.618),
    ]
    mask2 = active_mask_for_rows(rows2, show_inactive=False, last_effective_by_key=None)
    assert mask2 == [True, False, True]


def test_active_mask_for_rows_show_inactive_returns_all_true() -> None:
    rows = [
        _row(arg="base", value="square"),
        _row(arg="cell_size", value=10.0),
        _row(arg="ratio", value=1.618),
    ]
    mask = active_mask_for_rows(rows, show_inactive=True, last_effective_by_key=None)
    assert mask == [True, True, True]


def test_active_mask_uses_last_effective_by_key() -> None:
    rows = [
        _row(arg="base", value="square"),
        _row(arg="cell_size", value=10.0),
        _row(arg="ratio", value=1.618),
    ]
    eff = {ParameterKey(op="preset._vis_preset", site_id="s:1", arg="base"): "ratio_lines"}
    mask = active_mask_for_rows(rows, show_inactive=False, last_effective_by_key=eff)
    assert mask == [True, False, True]


def test_active_mask_predicate_error_does_not_hide() -> None:
    rows = [
        _row(arg="base", value="square"),
        _row(arg="boom", value=0.0),
    ]
    mask = active_mask_for_rows(rows, show_inactive=False, last_effective_by_key=None)
    assert mask == [True, True]


def _row2(*, site_id: str, arg: str, value: object) -> ParameterRow:
    kind = "bool" if arg == "activate" else "float"
    return ParameterRow(
        label=f"{site_id}:{arg}",
        op="preset._vis_preset",
        site_id=str(site_id),
        arg=str(arg),
        kind=kind,
        ui_value=value,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=1,
    )


def test_active_mask_activate_off_hides_other_params() -> None:
    rows = [
        _row2(site_id="s:1", arg="activate", value=False),
        _row2(site_id="s:1", arg="base", value="square"),
        _row2(site_id="s:1", arg="cell_size", value=10.0),
    ]
    mask = active_mask_for_rows(rows, show_inactive=False, last_effective_by_key=None)
    assert mask == [True, False, False]


def test_active_mask_activate_off_show_inactive_returns_all_true() -> None:
    rows = [
        _row2(site_id="s:1", arg="activate", value=False),
        _row2(site_id="s:1", arg="base", value="square"),
        _row2(site_id="s:1", arg="cell_size", value=10.0),
    ]
    mask = active_mask_for_rows(rows, show_inactive=True, last_effective_by_key=None)
    assert mask == [True, True, True]


def test_active_mask_activate_off_uses_last_effective_by_key() -> None:
    rows = [
        _row2(site_id="s:1", arg="activate", value=True),
        _row2(site_id="s:1", arg="base", value="square"),
    ]
    eff = {ParameterKey(op="preset._vis_preset", site_id="s:1", arg="activate"): False}
    mask = active_mask_for_rows(rows, show_inactive=False, last_effective_by_key=eff)
    assert mask == [True, False]


def test_render_store_parameter_table_filters_rows_passed_to_renderer(monkeypatch) -> None:
    store = ParamStore()
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=ParameterKey("preset._vis_preset", "s:1", "base"),
                base="square",
                effective="square",
                source="code",
                meta=ParamMeta(kind="choice", choices=("square", "ratio_lines")),
                explicit=False,
            ),
            FrameParamRecord(
                key=ParameterKey("preset._vis_preset", "s:1", "cell_size"),
                base=10.0,
                effective=10.0,
                source="code",
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
                explicit=False,
            ),
            FrameParamRecord(
                key=ParameterKey("preset._vis_preset", "s:1", "ratio"),
                base=1.618,
                effective=1.618,
                source="code",
                meta=ParamMeta(kind="float", ui_min=1.01, ui_max=10.0),
                explicit=False,
            ),
        ],
    )

    captured_args: list[str] = []

    def _fake_render_parameter_table(render_input, **_kwargs):
        rows = [
            render_input.model_rows[item.row_index]
            for block in render_input.group_layout
            for item in block.items
        ]
        captured_args[:] = [str(r.arg) for r in rows]
        return TableEdits(
            rows=tuple(rows),
            collapsed_headers=render_input.collapsed_headers,
            midi_learn_state=render_input.midi_learn_state,
        )

    monkeypatch.setattr(store_bridge, "render_parameter_table", _fake_render_parameter_table)

    active_view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=False,
    )
    widget_state = WidgetSessionState()
    store_bridge.render_store_parameter_table(
        store,
        table_view=active_view,
        widget_state=widget_state,
    )
    assert captured_args == ["base", "cell_size"]

    all_view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    store_bridge.render_store_parameter_table(
        store,
        table_view=all_view,
        widget_state=widget_state,
    )
    assert captured_args == ["base", "cell_size", "ratio"]


def test_search_filter_composes_with_existing_show_inactive_visibility(
    monkeypatch,
) -> None:
    store = ParamStore()
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=ParameterKey("preset._vis_preset", "s:filter", "base"),
                base="square",
                effective="square",
                source="code",
                meta=ParamMeta(kind="choice", choices=("square", "ratio_lines")),
                explicit=False,
            ),
            FrameParamRecord(
                key=ParameterKey("preset._vis_preset", "s:filter", "cell_size"),
                base=10.0,
                effective=10.0,
                source="code",
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=100.0),
                explicit=False,
            ),
            FrameParamRecord(
                key=ParameterKey("preset._vis_preset", "s:filter", "ratio"),
                base=1.618,
                effective=1.618,
                source="code",
                meta=ParamMeta(kind="float", ui_min=1.01, ui_max=10.0),
                explicit=False,
            ),
        ],
    )
    state = ParameterFilterState(query="RATIO", activity="inactive")
    captured_args: list[str] = []

    def fake_render(render_input, **_kwargs):
        rows = [
            render_input.model_rows[item.row_index]
            for block in render_input.group_layout
            for item in block.items
        ]
        captured_args[:] = [str(row.arg) for row in rows]
        return TableEdits(
            rows=tuple(rows),
            collapsed_headers=render_input.collapsed_headers,
            midi_learn_state=render_input.midi_learn_state,
        )

    monkeypatch.setattr(store_bridge, "render_parameter_table", fake_render)

    hidden_view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=False,
        filter_state=state,
    )
    assert (hidden_view.filtered_count, hidden_view.total_count) == (0, 3)
    store_bridge.render_store_parameter_table(
        store,
        table_view=hidden_view,
        widget_state=WidgetSessionState(),
    )
    assert captured_args == []

    shown_view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=state,
    )
    assert (shown_view.filtered_count, shown_view.total_count) == (1, 3)
    store_bridge.render_store_parameter_table(
        store,
        table_view=shown_view,
        widget_state=WidgetSessionState(),
    )
    assert captured_args == ["ratio"]


def _text_row(*, arg: str, value: object) -> ParameterRow:
    return ParameterRow(
        label=f"1:{arg}",
        op="text",
        site_id="s:1",
        arg=str(arg),
        kind="float",
        ui_value=value,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=1,
    )


def test_ui_visible_for_text_bounding_box_toggle() -> None:
    rows_off = [
        _text_row(arg="use_bounding_box", value=False),
        _text_row(arg="box_width", value=100.0),
        _text_row(arg="box_height", value=20.0),
        _text_row(arg="show_bounding_box", value=True),
    ]
    mask_off = active_mask_for_rows(rows_off, show_inactive=False, last_effective_by_key=None)
    assert mask_off == [True, False, False, False]

    rows_on = [
        _text_row(arg="use_bounding_box", value=True),
        _text_row(arg="box_width", value=100.0),
        _text_row(arg="box_height", value=20.0),
        _text_row(arg="show_bounding_box", value=True),
    ]
    mask_on = active_mask_for_rows(rows_on, show_inactive=False, last_effective_by_key=None)
    assert mask_on == [True, True, True, True]
