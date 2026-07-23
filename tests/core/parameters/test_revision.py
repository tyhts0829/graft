from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import Any, cast

import pytest

from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.favorites import set_parameters_favorite
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.labels_ops import set_label
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters import merge_ops
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.snapshot_ops import materialize_snapshot, store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.style import STYLE_GLOBAL_THICKNESS, style_key
from grafix.core.parameters.style_ops import ensure_style_entries
from grafix.core.parameters.ui_ops import update_state_from_ui


def _record(index: int, *, value: float | None = None) -> FrameParamRecord:
    base = float(index if value is None else value)
    return FrameParamRecord(
        key=ParameterKey(op="line", site_id=f"site-{index}", arg="length"),
        base=base,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=10_000.0),
        explicit=False,
        effective=base,
        source="code",
    )


def test_snapshot_is_cached_until_store_structure_changes() -> None:
    store = ParamStore()
    record = _record(1)
    merge_frame_params(store, [record])
    revision = store.revision

    first = store_snapshot(store)
    second = store_snapshot(store)
    merge_frame_params(store, [record])
    third = store_snapshot(store)

    assert first is second is third
    assert store.revision == revision


def test_ui_and_label_updates_advance_revision_only_on_real_change() -> None:
    store = ParamStore()
    record = _record(1)
    merge_frame_params(store, [record])
    key = record.key
    revision = store.revision

    ok, error = update_state_from_ui(store, key, 1.0, meta=record.meta)
    assert ok is True and error is None
    assert store.revision == revision

    update_state_from_ui(store, key, 2.0, meta=record.meta)
    assert store.revision > revision
    revision = store.revision

    set_label(store, op=key.op, site_id=key.site_id, label="line")
    assert store.revision > revision
    revision = store.revision
    set_label(store, op=key.op, site_id=key.site_id, label="line")
    assert store.revision == revision


def test_style_revision_only_tracks_style_value_or_conservative_structure_changes() -> None:
    store = ParamStore()
    merge_frame_params(store, [_record(1)])
    style_before_geometry = store.style_revision
    table_before_geometry = store.table_revision

    geometry_key = _record(1).key
    assert update_state_from_ui(
        store,
        geometry_key,
        2.0,
        meta=_record(1).meta,
    )[0]
    assert store.style_revision == style_before_geometry
    assert store.table_revision == table_before_geometry

    ensure_style_entries(
        store,
        background_color_rgb01=(1.0, 1.0, 1.0),
        global_thickness=0.01,
        global_line_color_rgb01=(0.0, 0.0, 0.0),
    )
    thickness_key = style_key(STYLE_GLOBAL_THICKNESS)
    thickness_meta = store.get_meta(thickness_key)
    assert thickness_meta is not None
    style_before_value = store.style_revision
    table_before_value = store.table_revision
    assert update_state_from_ui(
        store,
        thickness_key,
        0.005,
        meta=thickness_meta,
    )[0]
    assert store.style_revision == style_before_value + 1
    assert store.table_revision == table_before_value


def test_large_unchanged_snapshot_is_built_once() -> None:
    store = ParamStore()
    records = [_record(index) for index in range(1_000)]
    merge_frame_params(store, records)

    snapshots = [store_snapshot(store) for _ in range(60)]

    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    assert len(snapshots[0]) == 1_000


def test_effective_revision_advances_once_only_when_final_snapshot_changes() -> None:
    store = ParamStore()
    first = replace(_record(1), source="code")

    merge_frame_params(store, [first])
    assert store.effective_revision == 1

    # 同じ record の再 merge と、途中値だけが異なる同一 key の merge は不変。
    merge_frame_params(store, [first])
    merge_frame_params(
        store,
        [
            replace(first, effective=2.0),
            first,
        ],
    )
    assert store.effective_revision == 1

    # effective が複数 key で変わっても、1 frame につき 1 回だけ進む。
    second = replace(_record(2), source="code")
    merge_frame_params(
        store,
        [
            replace(first, effective=3.0),
            second,
        ],
    )
    assert store.effective_revision == 2

    # 値が同じでも source が変われば provenance snapshot は変わる。
    merge_frame_params(
        store,
        [
            replace(first, effective=3.0, source="ui"),
            second,
        ],
    )
    assert store.effective_revision == 3

def test_stable_merge_skips_initial_value_canonicalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParamStore()
    records = [_record(index) for index in range(100)]
    merge_frame_params(store, records)

    def fail_canonicalize(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("stable merge canonicalized an existing state")

    monkeypatch.setattr(
        merge_ops,
        "canonicalize_ui_value",
        fail_canonicalize,
    )

    merge_frame_params(store, records)
    assert len(store_snapshot(store)) == len(records)


def test_stable_merge_reports_only_changed_runtime_key() -> None:
    store = ParamStore()
    records = [replace(_record(index), source="code") for index in range(100)]
    merge_frame_params(store, records)
    revision = store.effective_revision

    merge_frame_params(
        store,
        [
            *records[:-1],
            replace(records[-1], effective=-1.0, source="ui"),
        ],
    )

    assert store.effective_changes_since(revision) == frozenset(
        {records[-1].key}
    )
    assert store.last_effective_value(records[-1].key) == -1.0
    assert store.runtime_view().last_source_by_key[records[-1].key] == "ui"


def test_duplicate_key_runtime_and_explicit_values_are_last_record_wins() -> None:
    store = ParamStore()
    first = replace(_record(1), effective=1.0, source="code", explicit=False)
    merge_frame_params(store, [first])
    revision = store.effective_revision

    merge_frame_params(
        store,
        [
            replace(first, effective=9.0, source="ui", explicit=False),
            replace(first, effective=2.0, source="midi_live", explicit=True),
        ],
    )

    state = store.get_state(first.key)
    assert state is not None
    assert state.override is False
    assert store._read().explicit(first.key) is True
    assert store.last_effective_value(first.key) == 2.0
    assert store.runtime_view().last_source_by_key[first.key] == "midi_live"
    assert store.effective_revision == revision + 1


def test_structure_change_leaves_stable_cache_with_latest_schema() -> None:
    store = ParamStore()
    first = replace(
        _record(1),
        source="code",
    )
    merge_frame_params(store, [first])

    changed_meta = ParamMeta(kind="int", ui_min=0, ui_max=100)
    changed = replace(
        first,
        base=2,
        effective=2,
        meta=changed_meta,
    )
    merge_frame_params(store, [changed])
    merge_frame_params(store, [changed])

    meta = store.get_meta(first.key)
    assert meta is not None
    assert meta.kind == "int"
    assert store.get_effect_step(first.key.op, first.key.site_id) is None


def test_failed_merge_restores_effective_snapshot_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParamStore()
    first = replace(_record(1), source="code")
    merge_frame_params(store, [first])
    runtime = store._read().runtime()
    before_effective = dict(runtime.last_effective_by_key)
    before_source = dict(runtime.last_source_by_key)
    before_revision = runtime.effective_revision

    def fail_follow(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("follow failed")

    monkeypatch.setattr(
        merge_ops,
        "_apply_explicit_override_follow_policy",
        fail_follow,
    )
    with pytest.raises(RuntimeError, match="follow failed"):
        merge_frame_params(
            store,
            [
                replace(
                    first,
                    effective=9.0,
                    source="ui",
                    explicit=True,
                )
            ],
        )

    assert runtime.last_effective_by_key == before_effective
    assert runtime.last_source_by_key == before_source
    assert runtime.effective_revision == before_revision


def test_stable_merge_does_not_call_explicit_follow_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ParamStore()
    record = _record(1)
    merge_frame_params(store, [record])

    def fail_follow(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("empty explicit diff must not reach follow policy")

    monkeypatch.setattr(
        merge_ops,
        "_apply_explicit_override_follow_policy",
        fail_follow,
    )

    merge_frame_params(store, [record])


def test_cached_snapshot_outer_mapping_cannot_be_mutated() -> None:
    store = ParamStore()
    record = _record(1)
    merge_frame_params(store, [record])
    snapshot = store_snapshot(store)

    with pytest.raises(TypeError):
        cast(dict[Any, Any], snapshot)[record.key] = snapshot[record.key]

    assert store_snapshot(store) is snapshot
    assert len(snapshot) == 1


def test_cached_snapshot_state_cannot_be_mutated() -> None:
    store = ParamStore()
    record = _record(1)
    merge_frame_params(store, [record])
    snapshot = store_snapshot(store)
    state = snapshot[record.key][1]

    with pytest.raises(FrozenInstanceError):
        state.ui_value = 999.0  # type: ignore[misc]

    assert store_snapshot(store) is snapshot
    assert snapshot[record.key][1].ui_value == 1.0


def test_one_key_snapshot_rebuilds_only_changed_entry() -> None:
    store = ParamStore()
    records = [_record(index) for index in range(1_000)]
    merge_frame_params(store, records)
    before = store_snapshot(store)
    key = records[500].key

    assert update_state_from_ui(
        store,
        key,
        123.5,
        meta=records[500].meta,
    )[0]
    after = store_snapshot(store)

    assert after is not before
    assert before[key][1].ui_value == 500.0
    assert after[key][1].ui_value == 123.5
    assert after[records[0].key] is before[records[0].key]
    assert store._snapshot_cache_rebuilt_entries == 1
    assert getattr(after, "patch_entries", 0) == 1
    assert materialize_snapshot(after) == {key: after[key] for key in after}


def test_snapshot_patch_is_bounded_and_periodically_materialized() -> None:
    store = ParamStore()
    records = [_record(index) for index in range(100)]
    merge_frame_params(store, records)
    snapshots = [store_snapshot(store)]

    for index, record in enumerate(records[:80]):
        assert update_state_from_ui(
            store,
            record.key,
            float(index) + 0.25,
            meta=record.meta,
        )[0]
        snapshot = store_snapshot(store)
        snapshots.append(snapshot)
        assert getattr(snapshot, "patch_entries", 0) <= 64
        assert store._snapshot_cache_rebuilt_entries == 1

    assert snapshots[0][records[0].key][1].ui_value == 0.0
    assert snapshots[-1][records[0].key][1].ui_value == 0.25
    assert snapshots[-1][records[79].key][1].ui_value == 79.25


def test_favorite_change_reuses_parameter_snapshot_identity() -> None:
    store = ParamStore()
    record = _record(1)
    merge_frame_params(store, [record])
    snapshot = store_snapshot(store)

    assert set_parameters_favorite(store, [record.key], favorite=True) == (
        record.key,
    )

    assert store_snapshot(store) is snapshot


def test_favorite_change_does_not_mark_stale_value_snapshot_current() -> None:
    store = ParamStore()
    record = _record(1)
    merge_frame_params(store, [record])
    before = store_snapshot(store)

    assert update_state_from_ui(
        store,
        record.key,
        2.0,
        meta=record.meta,
    )[0]
    set_parameters_favorite(store, [record.key], favorite=True)
    after = store_snapshot(store)

    assert after is not before
    assert before[record.key][1].ui_value == 1.0
    assert after[record.key][1].ui_value == 2.0
