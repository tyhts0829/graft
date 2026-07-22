from __future__ import annotations

from dataclasses import replace

import pytest

from grafix.core.parameters.favorites import set_parameters_favorite
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.parameter_gui import store_bridge
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.table import TableEdits


def _layout_rows(group_layout, model_rows):
    return [
        model_rows[item.row_index]
        for block in group_layout
        for item in block.items
    ]


def _table_edits(render_input, rows) -> TableEdits:
    return TableEdits(
        rows=tuple(rows),
        collapsed_headers=render_input.collapsed_headers,
        midi_learn_state=render_input.midi_learn_state,
    )


def _store_with_rows(count: int) -> tuple[ParamStore, list[FrameParamRecord]]:
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=float(count))
    records = [
        FrameParamRecord(
            key=ParameterKey(
                op="model_bench",
                site_id="site",
                arg=f"value_{index:04d}",
            ),
            base=float(index),
            meta=meta,
            explicit=False,
            effective=float(index),
            source="code",
        )
        for index in range(count)
    ]
    store = ParamStore()
    merge_frame_params(store, records)
    return store, records


def test_1000_rows_reuse_one_table_model_for_60_frames(monkeypatch) -> None:
    store, records = _store_with_rows(1_000)
    store_bridge.clear_parameter_table_model_cache()
    render_calls = 0
    widget_state = WidgetSessionState()

    def fake_render(render_input, **_kwargs):
        nonlocal render_calls
        render_calls += 1
        rows = _layout_rows(render_input.group_layout, render_input.model_rows)
        assert len(rows) == 1_000
        assert render_input.group_layout is first.group_layout
        return _table_edits(render_input, rows)

    monkeypatch.setattr(store_bridge, "render_parameter_table", fake_render)

    first = store_bridge._parameter_table_model_for_store(store)
    for _ in range(60):
        view = store_bridge.parameter_table_view_for_store(
            store,
            show_inactive_params=True,
        )
        assert not store_bridge.render_store_parameter_table(
            store,
            table_view=view,
            widget_state=widget_state,
        ).changed

    assert render_calls == 60
    assert store_bridge.parameter_table_model_build_count() == 1
    assert store_bridge._parameter_table_model_for_store(store) is first

    # effective はフレーム動的値なので、更新しても静的モデルは作り直さない。
    store._runtime_ref().last_effective_by_key[records[0].key] = 999.0
    assert store_bridge._parameter_table_model_for_store(store) is first
    assert store_bridge.parameter_table_model_build_count() == 1


def test_table_model_patches_value_change_and_rebuilds_for_catalog_generation() -> None:
    store, records = _store_with_rows(2)
    store_bridge.clear_parameter_table_model_cache()

    first = store_bridge._parameter_table_model_for_store(store)
    ok, error = update_state_from_ui(
        store,
        records[0].key,
        1.5,
        meta=records[0].meta,
    )
    assert ok is True and error is None
    second = store_bridge._parameter_table_model_for_store(store)
    assert second is not first
    assert store_bridge.parameter_table_model_build_count() == 1
    assert second.group_layout is first.group_layout
    row_index = second.row_index_by_key[records[0].key]
    assert second.rows[row_index].ui_value == 1.5

    third = store_bridge._parameter_table_model_for_store(
        store,
        catalog=current_parameter_gui_catalog(),
    )
    assert third is not second
    assert third.group_layout is not second.group_layout
    assert store_bridge.parameter_table_model_build_count() == 2


def test_show_inactive_without_activity_filter_skips_activity_mask(
    monkeypatch,
) -> None:
    store, records = _store_with_rows(3)

    def fail_activity_mask(*_args, **_kwargs):
        raise AssertionError("activity mask should not be evaluated")

    monkeypatch.setattr(store_bridge, "active_mask_for_rows", fail_activity_mask)

    cases = (
        (None, frozenset(), frozenset()),
        (ParameterFilterState(query="model_bench"), frozenset(), frozenset()),
        (
            ParameterFilterState(favorite_only=True),
            frozenset(),
            frozenset({records[0].key}),
        ),
        (
            ParameterFilterState(error_only=True),
            frozenset({records[1].key}),
            frozenset(),
        ),
    )
    for state, error_keys, favorite_keys in cases:
        view = store_bridge.parameter_table_view_for_store(
            store,
            show_inactive_params=True,
            filter_state=state,
            error_keys=error_keys,
            favorite_keys=favorite_keys,
        )
        assert view.total_count == 3


def test_activity_mask_is_kept_when_visibility_or_activity_filter_needs_it(
    monkeypatch,
) -> None:
    store, _records = _store_with_rows(3)
    calls = 0

    def count_activity_mask(rows, **_kwargs):
        nonlocal calls
        calls += 1
        return [True] * len(rows)

    monkeypatch.setattr(store_bridge, "active_mask_for_rows", count_activity_mask)

    store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=False,
    )
    store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=ParameterFilterState(activity="active"),
    )
    assert calls == 2


def test_query_changes_reuse_filter_independent_base_visibility(
    monkeypatch,
) -> None:
    store, _records = _store_with_rows(100)
    store_bridge.clear_parameter_table_model_cache()
    original = store_bridge.active_mask_for_rows
    calls = 0

    def counted(rows, **kwargs):
        nonlocal calls
        calls += 1
        return original(rows, **kwargs)

    monkeypatch.setattr(store_bridge, "active_mask_for_rows", counted)
    first = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=False,
        filter_state=ParameterFilterState(query="value_0001"),
    )
    second = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=False,
        filter_state=ParameterFilterState(query="value_0099"),
    )

    assert first.filtered_count == second.filtered_count == 1
    assert calls == 1


def test_search_trigram_index_preserves_partial_and_dynamic_matches() -> None:
    store, records = _store_with_rows(100)
    store_bridge.clear_parameter_table_model_cache()

    partial = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=ParameterFilterState(query="alue_009"),
    )
    assert partial.filtered_count == 10

    assert update_state_from_ui(
        store,
        records[0].key,
        records[0].base,
        meta=records[0].meta,
        override=False,
    )[0]
    dynamic = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=ParameterFilterState(query="cod"),
    )
    assert dynamic.filtered_count == 1


@pytest.mark.parametrize("query", ("127", "cc 127", "cc", "midi"))
def test_search_index_matches_valid_midi_cc(query: str) -> None:
    store, records = _store_with_rows(3)
    store_bridge.clear_parameter_table_model_cache()
    assert update_state_from_ui(
        store,
        records[0].key,
        records[0].base,
        meta=records[0].meta,
        cc_key=127,
    )[0]

    view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=ParameterFilterState(query=query),
    )

    assert view.filtered_count == 1


def test_unchanged_render_returns_immutable_rows_without_store_change(monkeypatch) -> None:
    store, _records = _store_with_rows(3)

    def fake_render(render_input, **_kwargs):
        rows = _layout_rows(render_input.group_layout, render_input.model_rows)
        return _table_edits(render_input, rows)

    monkeypatch.setattr(store_bridge, "render_parameter_table", fake_render)

    view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    assert not store_bridge.render_store_parameter_table(
        store,
        table_view=view,
        widget_state=WidgetSessionState(),
    ).changed


def test_changed_render_refreshes_only_value_without_model_rebuild(monkeypatch) -> None:
    store, records = _store_with_rows(1_000)
    store_bridge.clear_parameter_table_model_cache()

    def fake_render(render_input, **_kwargs):
        rows = _layout_rows(render_input.group_layout, render_input.model_rows)
        updated = list(rows)
        updated[0] = replace(updated[0], ui_value=123.5)
        return _table_edits(render_input, updated)

    monkeypatch.setattr(store_bridge, "render_parameter_table", fake_render)
    view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    assert store_bridge.render_store_parameter_table(
        store,
        table_view=view,
        widget_state=WidgetSessionState(),
    ).changed

    model = store_bridge._parameter_table_model_for_store(store)
    index = model.row_index_by_key[records[0].key]
    assert model.rows[index].ui_value == 123.5
    assert store_bridge.parameter_table_model_build_count() == 1


def test_filtered_render_keeps_model_indices_for_layout_and_applies_visible_edit(
    monkeypatch,
) -> None:
    store, records = _store_with_rows(3)
    store_bridge.clear_parameter_table_model_cache()

    def fake_render(render_input, **_kwargs):
        rows = _layout_rows(render_input.group_layout, render_input.model_rows)
        assert [row.arg for row in rows] == ["value_0001"]
        assert (
            render_input.model_rows[
                render_input.group_layout[0].items[0].row_index
            ]
            is rows[0]
        )
        updated = [replace(rows[0], ui_value=99.0)]
        return _table_edits(render_input, updated)

    monkeypatch.setattr(store_bridge, "render_parameter_table", fake_render)
    view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=ParameterFilterState(query="value_0001"),
    )
    assert store_bridge.render_store_parameter_table(
        store,
        table_view=view,
        widget_state=WidgetSessionState(),
    ).changed

    assert store.get_state(records[0].key).ui_value == 0.0
    assert store.get_state(records[1].key).ui_value == 99.0
    assert store.get_state(records[2].key).ui_value == 2.0


def test_value_change_log_overflow_falls_back_to_safe_model_rebuild() -> None:
    store, records = _store_with_rows(1)
    store_bridge.clear_parameter_table_model_cache()
    store_bridge._parameter_table_model_for_store(store)

    for value in range(4_100):
        ok, error = update_state_from_ui(
            store,
            records[0].key,
            float(value),
            meta=records[0].meta,
        )
        assert ok is True and error is None

    model = store_bridge._parameter_table_model_for_store(store)
    assert model.rows[0].ui_value == 4_099.0
    assert store_bridge.parameter_table_model_build_count() == 2


def test_stable_default_and_search_views_are_reused_without_rebuild() -> None:
    store, _records = _store_with_rows(1_000)
    store_bridge.clear_parameter_table_model_cache()

    default = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    for _ in range(60):
        assert (
            store_bridge.parameter_table_view_for_store(
                store,
                show_inactive_params=True,
            )
            is default
        )
    assert store_bridge.parameter_table_view_build_count() == 1

    state = ParameterFilterState(query="value_0999")
    search = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=state,
    )
    for _ in range(60):
        assert (
            store_bridge.parameter_table_view_for_store(
                store,
                show_inactive_params=True,
                filter_state=state,
            )
            is search
        )
    assert search.filtered_count == 1
    assert store_bridge.parameter_table_view_build_count() == 2


def test_view_cache_invalidates_value_effective_visibility_and_external_flags() -> None:
    store, records = _store_with_rows(2)
    store_bridge.clear_parameter_table_model_cache()

    first = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    assert update_state_from_ui(
        store,
        records[0].key,
        1.25,
        meta=records[0].meta,
        override=False,
        cc_key=7,
    )[0]
    value_changed = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    assert value_changed is not first
    assert (
        store_bridge.parameter_table_view_for_store(
            store,
            show_inactive_params=True,
            filter_state=ParameterFilterState(ui_override_only=True),
        ).filtered_count
        == 1
    )
    assert (
        store_bridge.parameter_table_view_for_store(
            store,
            show_inactive_params=True,
            filter_state=ParameterFilterState(midi_mapped_only=True),
        ).filtered_count
        == 1
    )

    runtime = store._runtime_ref()
    runtime.last_source_by_key[records[0].key] = "midi_live"
    runtime.effective_revision += 1
    effective_changed = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    assert effective_changed is not value_changed
    assert (
        store_bridge.parameter_table_view_for_store(
            store,
            show_inactive_params=True,
            filter_state=ParameterFilterState(query="MIDI LIVE"),
        ).filtered_count
        == 1
    )

    runtime.loaded_groups.add(("model_bench", "loaded-only"))
    visibility_changed = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )
    assert visibility_changed is not effective_changed

    error_state = ParameterFilterState(error_only=True)
    no_error = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=error_state,
    )
    with_error = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=error_state,
        error_keys=frozenset({records[1].key}),
    )
    assert with_error is not no_error
    assert with_error.filtered_count == 1


def test_default_show_inactive_view_rebinds_sparse_value_model_without_mask_build() -> None:
    store, records = _store_with_rows(1_000)
    store_bridge.clear_parameter_table_model_cache()
    first = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )

    assert update_state_from_ui(
        store,
        records[0].key,
        12.5,
        meta=records[0].meta,
    )[0]
    second = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
    )

    assert second is not first
    assert second.visible_mask is first.visible_mask
    assert second.group_layout is first.group_layout
    assert second.model.rows[0].ui_value == 12.5
    assert store_bridge.parameter_table_view_build_count() == 1


def test_default_active_view_reevaluates_only_changed_parameter_group(
    monkeypatch,
) -> None:
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=100.0)
    records = [
        FrameParamRecord(
            key=ParameterKey(
                op="model_bench",
                site_id=f"site-{index:03d}",
                arg="value",
            ),
            base=float(index),
            meta=meta,
            explicit=False,
            effective=float(index),
            source="code",
        )
        for index in range(100)
    ]
    store = ParamStore()
    merge_frame_params(store, records)
    store_bridge.clear_parameter_table_model_cache()
    original = store_bridge.active_mask_for_rows
    evaluated_row_counts: list[int] = []

    def counted(rows, **kwargs):
        evaluated_row_counts.append(len(rows))
        return original(rows, **kwargs)

    monkeypatch.setattr(store_bridge, "active_mask_for_rows", counted)
    first = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=False,
    )
    merge_frame_params(
        store,
        [replace(records[50], effective=999.0)],
    )
    second = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=False,
    )

    assert second is not first
    assert second.visible_mask is first.visible_mask
    assert evaluated_row_counts == [100, 1]
    assert store_bridge.parameter_table_view_build_count() == 1


def test_favorite_is_view_overlay_and_does_not_rebuild_static_model(
    monkeypatch,
) -> None:
    store, records = _store_with_rows(2)
    store_bridge.clear_parameter_table_model_cache()
    first = store_bridge._parameter_table_model_for_store(store)
    assert all(not row.favorite for row in first.rows)

    set_parameters_favorite(store, (records[0].key,), favorite=True)
    second = store_bridge._parameter_table_model_for_store(store)
    assert second is first

    favorite_view = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=ParameterFilterState(favorite_only=True),
    )
    assert favorite_view.filtered_count == 1
    assert favorite_view.favorite_keys == frozenset({records[0].key})
    assert (
        store_bridge.parameter_table_view_for_store(
            store,
            show_inactive_params=True,
            filter_state=ParameterFilterState(favorite_only=True),
        )
        is favorite_view
    )

    captured_favorite: list[bool] = []

    def fake_render(render_input, **_kwargs):
        rows = _layout_rows(render_input.group_layout, render_input.model_rows)
        captured_favorite[:] = [bool(row.favorite) for row in rows]
        return _table_edits(render_input, rows)

    monkeypatch.setattr(store_bridge, "render_parameter_table", fake_render)
    store_bridge.render_store_parameter_table(
        store,
        table_view=favorite_view,
        widget_state=WidgetSessionState(),
    )
    assert captured_favorite == [True]
    assert store_bridge.parameter_table_model_build_count() == 1

    set_parameters_favorite(store, (records[0].key,), favorite=False)
    without_favorite = store_bridge.parameter_table_view_for_store(
        store,
        show_inactive_params=True,
        filter_state=ParameterFilterState(favorite_only=True),
    )
    assert without_favorite is not favorite_view
    assert without_favorite.filtered_count == 0
    assert store_bridge._parameter_table_model_for_store(store) is first
