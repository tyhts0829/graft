from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.collapsed_header import primitive_collapsed_header_key
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.interactive.parameter_gui.midi_learn import MidiLearnState
from grafix.interactive.parameter_gui.table_commit import commit_table_edits
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)
from grafix.interactive.parameter_gui.table import TableEdits, TableRenderInput


def _store_with_rows(count: int = 2) -> tuple[ParamStore, tuple[ParameterKey, ...]]:
    store = ParamStore()
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=10.0)
    keys = tuple(
        ParameterKey(op="circle", site_id=f"site-{index}", arg="radius")
        for index in range(count)
    )
    merge_frame_params(
        store,
        tuple(
            FrameParamRecord(
                key=key,
                base=float(index),
                meta=meta,
                effective=float(index),
                source="code",
                explicit=True,
            )
            for index, key in enumerate(keys)
        ),
    )
    return store, keys


def _visible_rows(view) -> tuple:
    return tuple(
        view.model.rows[item.row_index]
        for block in view.group_layout
        for item in block.items
    )


def test_table_boundary_values_are_frozen_and_container_free(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store, _keys = _store_with_rows(1)
    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    rows = _visible_rows(view)
    render_input = TableRenderInput(
        group_layout=view.group_layout,
        model_rows=view.model.rows,
        catalog=view.model.catalog,
        collapsed_headers=store.collapsed_headers(),
        midi_learn_state=MidiLearnState(),
    )
    edits = TableEdits(
        rows=rows,
        collapsed_headers=frozenset(),
        midi_learn_state=MidiLearnState(),
    )

    assert isinstance(render_input.model_rows, tuple)
    assert isinstance(render_input.collapsed_headers, frozenset)
    assert isinstance(edits.rows, tuple)
    assert isinstance(edits.collapsed_headers, frozenset)
    assert not any(
        isinstance(getattr(render_input, field.name), ParamStore)
        for field in fields(render_input)
    )
    with pytest.raises(FrozenInstanceError):
        edits.rows = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        render_input.midi_learn_state.active_target = None  # type: ignore[misc,union-attr]


def test_multiple_row_edits_commit_as_one_revision_and_one_history_unit(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store, keys = _store_with_rows(2)
    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    rows = _visible_rows(view)
    history = ParamStoreHistory(store)
    revision = store.revision

    edits = TableEdits(
        rows=tuple(
            replace(row, ui_value=float(index + 4), override=True)
            for index, row in enumerate(rows)
        ),
        collapsed_headers=store.collapsed_headers(),
        midi_learn_state=MidiLearnState(),
    )

    assert commit_table_edits(store, table_view=view, edits=edits, history=history)
    assert store.revision == revision + 1
    assert history.undo_depth == 1
    assert [store.get_state(key).ui_value for key in keys] == [4.0, 5.0]  # type: ignore[union-attr]
    assert history.undo()
    assert [store.get_state(key).ui_value for key in keys] == [0.0, 1.0]  # type: ignore[union-attr]


def test_noop_table_result_does_not_change_revision_or_history(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store, _keys = _store_with_rows(1)
    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    history = ParamStoreHistory(store)
    revision = store.revision
    edits = TableEdits(
        rows=_visible_rows(view),
        collapsed_headers=store.collapsed_headers(),
        midi_learn_state=MidiLearnState(),
    )

    assert not commit_table_edits(store, table_view=view, edits=edits, history=history)
    assert store.revision == revision
    assert history.undo_depth == 0


def test_midi_assignment_is_one_discrete_history_unit(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store, keys = _store_with_rows(1)
    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    history = ParamStoreHistory(store)
    row = _visible_rows(view)[0]
    edits = TableEdits(
        rows=(replace(row, cc_key=74),),
        collapsed_headers=store.collapsed_headers(),
        midi_learn_state=MidiLearnState(),
    )

    assert commit_table_edits(store, table_view=view, edits=edits, history=history)
    assert history.undo_depth == 1
    assert store.get_state(keys[0]).cc_key == 74  # type: ignore[union-attr]

    next_view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    next_row = _visible_rows(next_view)[0]
    assert commit_table_edits(
        store,
        table_view=next_view,
        edits=TableEdits(
            rows=(replace(next_row, cc_key=75),),
            collapsed_headers=store.collapsed_headers(),
            midi_learn_state=MidiLearnState(),
        ),
        history=history,
    )
    assert history.undo_depth == 2
    assert history.undo()
    assert store.get_state(keys[0]).cc_key == 74  # type: ignore[union-attr]
    assert history.undo()
    assert store.get_state(keys[0]).cc_key is None  # type: ignore[union-attr]


def test_collapse_edit_is_one_history_unit_and_noop_is_stable(
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store, _keys = _store_with_rows(1)
    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    history = ParamStoreHistory(store)
    header = primitive_collapsed_header_key(("circle", "site-0"))
    edits = TableEdits(
        rows=_visible_rows(view),
        collapsed_headers=frozenset({header}),
        midi_learn_state=MidiLearnState(),
    )

    assert commit_table_edits(store, table_view=view, edits=edits, history=history)
    assert history.undo_depth == 1
    revision = store.revision
    assert not commit_table_edits(store, table_view=view, edits=edits, history=history)
    assert store.revision == revision
    assert history.undo_depth == 1
    assert history.undo()
    assert store.collapsed_headers() == frozenset()
