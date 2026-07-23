from __future__ import annotations

from dataclasses import replace

from grafix.core.parameters.collapsed_header import primitive_collapsed_header_key
from grafix.core.parameters.favorites import favorite_parameter_keys
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.table_commit import (
    _apply_updated_rows_to_store,
    set_all_parameter_groups_collapsed,
)
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)


def _store_with_two_groups() -> ParamStore:
    store = ParamStore()
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=10.0)
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=ParameterKey("circle", "site-a", "radius"),
                base=1.0,
                meta=meta,
                effective=1.0,
                source="code",
                explicit=False,
            ),
            FrameParamRecord(
                key=ParameterKey("circle", "site-b", "radius"),
                base=2.0,
                meta=meta,
                effective=2.0,
                source="code",
                explicit=False,
            ),
        ],
    )
    return store


def test_collapse_all_and_expand_all_update_all_current_groups(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store = _store_with_two_groups()
    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )

    assert set_all_parameter_groups_collapsed(store, view, collapsed=True) is True
    assert store._collapsed_headers_ref() == {
        primitive_collapsed_header_key(("circle", "site-a")),
        primitive_collapsed_header_key(("circle", "site-b")),
    }
    assert set_all_parameter_groups_collapsed(store, view, collapsed=True) is False

    assert set_all_parameter_groups_collapsed(store, view, collapsed=False) is True
    assert store._collapsed_headers_ref() == set()
    assert set_all_parameter_groups_collapsed(store, view, collapsed=False) is False


def test_hidden_count_reports_rows_removed_by_search(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store = _store_with_two_groups()

    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
        filter_state=ParameterFilterState(query="not-present"),
    )

    assert view.filtered_count == 0
    assert view.total_count == 2
    assert view.hidden_count == 2


def test_row_pin_updates_store_and_default_favorite_filter(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store = _store_with_two_groups()
    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    rows_before = list(view.model.rows)
    target = rows_before[0]
    rows_after = [replace(target, favorite=True), *rows_before[1:]]

    _apply_updated_rows_to_store(
        store,
        view.model.snapshot,
        rows_before,
        rows_after,
    )

    target_key = ParameterKey(target.op, target.site_id, target.arg)
    assert favorite_parameter_keys(store) == (target_key,)
    favorite_view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
        filter_state=ParameterFilterState(favorite_only=True),
    )
    visible_rows = [
        (row, key in favorite_view.favorite_keys)
        for row, key, visible in zip(
            favorite_view.model.rows,
            favorite_view.model.keys,
            favorite_view.visible_mask,
            strict=True,
        )
        if visible
    ]
    assert [(row.site_id, favorite) for row, favorite in visible_rows] == [
        (target.site_id, True)
    ]
