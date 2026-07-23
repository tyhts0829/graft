from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from grafix.core.parameters.collapsed_header import (
    primitive_collapsed_header_key,
)
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore


_META = ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)


def _store_with_parameter() -> tuple[ParamStore, ParameterKey]:
    store = ParamStore()
    key = ParameterKey(op="circle", site_id="site", arg="radius")
    merge_frame_params(
        store,
        (
            FrameParamRecord(
                key=key,
                base=0.25,
                meta=_META,
                effective=0.25,
                source="code",
                explicit=False,
            ),
        ),
    )
    return store, key


def test_collapsed_header_commands_are_atomic_and_noop_stable() -> None:
    store, key = _store_with_parameter()
    first = primitive_collapsed_header_key((key.op, key.site_id))
    second = primitive_collapsed_header_key(("line", "other"))
    observed: list[frozenset[object] | None] = []
    store._begin_history_patch_capture(
        observe_key=lambda _key: None,
        observe_headers=lambda headers: observed.append(headers),
    )
    try:
        revision = store.revision
        assert store.set_collapsed(first, collapsed=False) is False
        assert store.set_all_collapsed((first, second), collapsed=False) == ()
        assert store.revision == revision
        assert observed == []

        assert store.set_all_collapsed((first, second), collapsed=True) == (
            first,
            second,
        )
        assert store.revision == revision + 1
        assert observed == [frozenset()]
        assert store.collapsed_headers() == frozenset({first, second})

        revision = store.revision
        assert store.set_all_collapsed((first, second), collapsed=True) == ()
        assert store.revision == revision
        assert len(observed) == 1
    finally:
        store._end_history_patch_capture()


def test_replace_collapsed_headers_observes_and_touches_once() -> None:
    store, key = _store_with_parameter()
    first = primitive_collapsed_header_key((key.op, key.site_id))
    second = primitive_collapsed_header_key(("line", "other"))
    assert store.set_collapsed(first, collapsed=True) is True

    observed: list[frozenset[object] | None] = []
    store._begin_history_patch_capture(
        observe_key=lambda _key: None,
        observe_headers=lambda headers: observed.append(headers),
    )
    try:
        revision = store.revision
        assert store.replace_collapsed_headers((second,)) is True
        assert store.revision == revision + 1
        assert observed == [frozenset({first})]
        assert store.collapsed_headers() == frozenset({second})

        revision = store.revision
        assert store.replace_collapsed_headers((second, second)) is False
        assert store.revision == revision
        assert len(observed) == 1
    finally:
        store._end_history_patch_capture()


def test_collapsed_command_is_one_history_operation() -> None:
    store, key = _store_with_parameter()
    header = primitive_collapsed_header_key((key.op, key.site_id))
    history = ParamStoreHistory(store)
    revision = store.revision

    with history.transaction(source="collapse", patch=True):
        assert store.set_collapsed(header, collapsed=True) is True

    assert store.revision == revision + 1
    assert history.undo_depth == 1

    with history.transaction(source="collapse-noop", patch=True):
        assert store.set_collapsed(header, collapsed=True) is False

    assert store.revision == revision + 1
    assert history.undo_depth == 1
    assert history.undo() is True
    assert store.collapsed_headers() == frozenset()


def test_runtime_view_is_frozen_and_does_not_expose_mutable_sets() -> None:
    store, key = _store_with_parameter()
    runtime = store._runtime_ref()
    runtime.loaded_groups.add((key.op, key.site_id))
    runtime.observed_groups.add((key.op, key.site_id))
    runtime.last_effective_by_key[key] = 0.75
    runtime.last_source_by_key[key] = "midi_live"
    runtime.record_effective_changes((key,))

    view = store.runtime_view()

    assert view.loaded_groups == frozenset({(key.op, key.site_id)})
    assert view.observed_groups == frozenset({(key.op, key.site_id)})
    assert view.last_effective_by_key[key] == 0.75
    assert view.last_source_by_key[key] == "midi_live"
    assert view.visibility_cache_token() == (runtime.visibility_revision,)
    assert store.effective_changes_since(view.effective_revision) == frozenset()
    with pytest.raises(FrozenInstanceError):
        view.effective_revision = 99  # type: ignore[misc]
    with pytest.raises(TypeError):
        view.last_effective_by_key[key] = 1.0  # type: ignore[index]


def test_runtime_view_keeps_a_point_in_time_mapping_snapshot() -> None:
    store, key = _store_with_parameter()
    view = store.runtime_view()
    display_order = dict(view.display_order_by_group)

    merge_frame_params(
        store,
        (
            FrameParamRecord(
                key=key,
                base=0.25,
                meta=_META,
                effective=0.75,
                source="midi_live",
                explicit=False,
            ),
        ),
    )
    runtime = store._runtime_ref()
    runtime.display_order_by_group[("line", "later")] = 999

    assert dict(view.display_order_by_group) == display_order
    assert view.last_effective_by_key[key] == 0.25
    assert view.last_source_by_key[key] == "code"
    assert store.runtime_view().last_effective_by_key[key] == 0.75
    assert store.runtime_view().last_source_by_key[key] == "midi_live"


def test_narrow_runtime_queries_and_commands_do_not_touch_store_revision() -> None:
    store, key = _store_with_parameter()
    runtime = store._runtime_ref()
    runtime.last_effective_by_key[key] = 0.5
    revision = store.revision

    assert store.last_effective_value(key) == 0.5
    assert store.last_effective_value(
        ParameterKey("circle", "missing", "radius")
    ) is None
    assert store.record_unknown_argument_warnings(
        (("circle", "legacy"), ("circle", "legacy"))
    ) == frozenset({("circle", "legacy")})
    assert store.record_unknown_argument_warnings(
        (("circle", "legacy"),)
    ) == frozenset()
    assert store.variation_count() == 0
    assert store.revision == revision
