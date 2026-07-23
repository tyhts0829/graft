from __future__ import annotations

from grafix.core.parameters.frame_params import FrameLabelRecord, FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.labels_ops import merge_frame_labels, set_label
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.prune_ops import prune_groups
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.store import ParamStore
from tests.param_store_test_support import mutate_runtime


def _record() -> FrameParamRecord:
    return FrameParamRecord(
        key=ParameterKey(op="line", site_id="persistent", arg="length"),
        base=1.0,
        meta=ParamMeta(kind="float", ui_min=0.0, ui_max=10.0),
        effective=1.0,
        source="code",
        explicit=False,
    )


def _persistent_revisions(store: ParamStore) -> tuple[int, int, int, int, int]:
    return (
        store.revision,
        store.table_revision,
        store.value_revision,
        store.style_revision,
        store.favorite_revision,
    )


def test_merge_frame_labels_last_wins_return_to_initial_value_is_noop() -> None:
    store = ParamStore()
    merge_frame_params(store, [_record()])
    set_label(store, op="line", site_id="persistent", label="initial")
    snapshot = store_snapshot(store)
    revisions = _persistent_revisions(store)
    runtime_token = store._read().runtime_token()

    merge_frame_labels(
        store,
        [
            FrameLabelRecord("line", "persistent", "temporary"),
            FrameLabelRecord("line", "persistent", "initial"),
        ],
    )

    assert store.get_label("line", "persistent") == "initial"
    assert _persistent_revisions(store) == revisions
    assert store_snapshot(store) is snapshot
    assert store._read().runtime_token() == runtime_token


def test_prune_runtime_only_group_preserves_persistent_revisions_and_cache() -> None:
    store = ParamStore()
    record = _record()
    merge_frame_params(store, [record])
    runtime_group = ("runtime-only", "site")

    def seed_runtime(runtime: object) -> None:
        runtime.loaded_groups.add(runtime_group)  # type: ignore[attr-defined]
        runtime.observed_groups.add(runtime_group)  # type: ignore[attr-defined]

    mutate_runtime(store, seed_runtime)
    snapshot = store_snapshot(store)
    favorites = store._read().favorite_keys()
    revisions = _persistent_revisions(store)
    effective_revision = store.effective_revision
    runtime_token = store._read().runtime_token()
    visibility_revision = store.runtime_view().visibility_revision

    prune_groups(store, [runtime_group])

    runtime = store.runtime_view()
    assert runtime_group not in runtime.loaded_groups
    assert runtime_group not in runtime.observed_groups
    assert runtime.visibility_revision > visibility_revision
    assert store.get_state(record.key) is not None
    assert _persistent_revisions(store) == revisions
    assert store.effective_revision == effective_revision
    assert store_snapshot(store) is snapshot
    assert store._read().favorite_keys() is favorites
    assert store._read().runtime_token() == runtime_token


def test_prune_unknown_group_is_complete_identity_noop() -> None:
    store = ParamStore()
    snapshot = store_snapshot(store)
    favorites = store._read().favorite_keys()
    revisions = _persistent_revisions(store)
    runtime = store.runtime_view()
    runtime_token = store._read().runtime_token()

    prune_groups(store, [("ghost", "site")])

    assert _persistent_revisions(store) == revisions
    assert store.runtime_view() == runtime
    assert store_snapshot(store) is snapshot
    assert store._read().favorite_keys() is favorites
    assert store._read().runtime_token() == runtime_token
