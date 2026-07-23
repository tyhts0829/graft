from __future__ import annotations

import pytest

from grafix import E, G
from grafix.core.parameters.codec import (
    decode_param_store_result,
    encode_param_store,
)
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.view import ParameterRow
from grafix.interactive.parameter_gui.help_pane import (
    NO_DESCRIPTION,
    NOT_SPECIFIED,
    parameter_help_content,
)
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)
from grafix.interactive.parameter_gui.table import _notify_parameter_help


def _row(**metadata: object) -> ParameterRow:
    return ParameterRow(
        label="1:radius",
        op="circle",
        site_id="site",
        arg="radius",
        kind="float",
        ui_value=1.0,
        ui_min=0.0,
        ui_max=10.0,
        choices=None,
        cc_key=None,
        override=False,
        ordinal=1,
        **metadata,
    )


def test_help_content_shows_description_unit_and_recommended_range() -> None:
    content = parameter_help_content(
        _row(
            display_name="Radius",
            description="Controls the circle size.",
            unit="mm",
            recommended_range=(0.5, 8.0),
        )
    )

    assert content.title == "Radius"
    assert content.identity == "circle.radius"
    assert content.description == "Controls the circle size."
    assert content.unit == "mm"
    assert content.recommended_range == "0.5 – 8"


def test_help_content_has_clear_fallback_when_metadata_is_absent() -> None:
    content = parameter_help_content(_row())

    assert content.title == "Radius"
    assert content.description == NO_DESCRIPTION
    assert content.unit == NOT_SPECIFIED
    assert content.recommended_range == NOT_SPECIFIED


class _InteractionImgui:
    def __init__(self, active_state: str) -> None:
        self.active_state = str(active_state)

    def is_item_clicked(self) -> bool:
        return self.active_state == "selected"

    def is_item_hovered(self) -> bool:
        return self.active_state == "hovered"

    def is_item_focused(self) -> bool:
        return self.active_state == "focused"

    def is_item_active(self) -> bool:
        return False


@pytest.mark.parametrize("state", ["selected", "hovered", "focused"])
def test_selected_hovered_and_focused_rows_feed_help_pane(state: str) -> None:
    row = _row()
    seen: list[tuple[ParameterRow, bool]] = []

    _notify_parameter_help(
        _InteractionImgui(state),
        row,
        lambda item, selected: seen.append((item, selected)),
    )

    assert seen == [(row, state == "selected")]


def test_loaded_builtin_meta_is_upgraded_before_gui_help(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store = ParamStore()
    with parameter_context(store):
        line = G.line(key="description-upgrade-line")
        E.scale(key="description-upgrade-scale")(line)

    payload = encode_param_store(store)
    for item in payload["meta"]:
        item.pop("description", None)
        if item["op"] == "line" and item["arg"] == "length":
            item["ui_min"] = -10.0
            item["ui_max"] = 10.0
    loaded = decode_param_store_result(payload).store
    pre_draw_view = parameter_table_view_for_store(
        loaded,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    pre_draw_rows = [
        row for row in pre_draw_view.model.rows if row.op in {"line", "scale"}
    ]
    assert pre_draw_rows
    assert all(
        row.description and row.description.strip() for row in pre_draw_rows
    )
    assert all(
        parameter_help_content(row).description != NO_DESCRIPTION
        for row in pre_draw_rows
    )
    pre_draw_length = next(
        row
        for row in pre_draw_rows
        if row.op == "line" and row.arg == "length"
    )
    assert (pre_draw_length.ui_min, pre_draw_length.ui_max) == (-10.0, 10.0)

    with parameter_context(loaded):
        line = G.line(key="description-upgrade-line")
        E.scale(key="description-upgrade-scale")(line)

    current_view = parameter_table_view_for_store(
        loaded,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    rows = [
        row
        for row in current_view.model.rows
        if row.op in {"line", "scale"}
    ]
    assert current_view.model is not pre_draw_view.model
    assert rows
    assert all(row.description and row.description.strip() for row in rows)
    assert all(parameter_help_content(row).description != NO_DESCRIPTION for row in rows)

    length_row = next(row for row in rows if row.op == "line" and row.arg == "length")
    assert (length_row.ui_min, length_row.ui_max) == (-10.0, 10.0)
