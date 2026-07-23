from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from grafix.core.parameters.collapsed_header import CollapsedHeaderKey
from grafix.core.parameters.effects import EffectStepTopology
from grafix.core.parameters.frame_params import FrameEffectChainRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.effect_order_ops import merge_frame_effect_chains
from grafix.core.parameters.runtime import ParamStoreRuntime
from grafix.core.parameters.state import ParamState
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.variations import Variation

T = TypeVar("T")
_KEEP = object()


def record_effect_chain(
    store: ParamStore,
    *,
    chain_id: str,
    steps: tuple[EffectStepTopology, ...],
) -> bool:
    return merge_frame_effect_chains(
        store,
        [FrameEffectChainRecord(chain_id=chain_id, steps=steps)],
        observation_complete=False,
    )


def runtime_state(store: ParamStore) -> ParamStoreRuntime:
    return store._read().runtime()


def mutate_runtime(
    store: ParamStore,
    mutate: Callable[[ParamStoreRuntime], T],
) -> T:
    base_revision = store.revision
    runtime = store._read().runtime()
    before_effective = dict(runtime.last_effective_by_key)
    before_source = dict(runtime.last_source_by_key)
    result = mutate(runtime)
    missing = object()
    value_keys = tuple(
        set(before_effective)
        | set(before_source)
        | set(runtime.last_effective_by_key)
        | set(runtime.last_source_by_key)
    )
    changed_value_keys = tuple(
        key
        for key in value_keys
        if before_effective.get(key, missing)
        != runtime.last_effective_by_key.get(key, missing)
        or before_source.get(key, missing)
        != runtime.last_source_by_key.get(key, missing)
    )
    store._mutation().commit_runtime(
        expected_revision=base_revision,
        runtime=runtime,
        runtime_value_keys=changed_value_keys,
    )
    return result


def mutate_parameter_state(
    store: ParamStore,
    key: ParameterKey,
    mutate: Callable[[ParamState], None],
) -> None:
    base_revision = store.revision
    read = store._read()
    state = read.state(key)
    if state is None:
        raise KeyError(key)
    mutate(state)
    states = read.states()
    states[key] = state
    store._mutation().commit_parameter_state(
        expected_revision=base_revision,
        states=states,
        meta=read.all_meta(),
        explicit_by_key=read.all_explicit(),
        structure=False,
        value_keys=(key,),
    )


def replace_parameter_state_for_test(
    store: ParamStore,
    key: ParameterKey,
    *,
    ui_value: object = _KEEP,
    override: object = _KEEP,
    cc_key: object = _KEEP,
) -> None:
    def mutate(state: ParamState) -> None:
        if ui_value is not _KEEP:
            state.ui_value = ui_value
        if override is not _KEEP:
            state.override = override  # type: ignore[assignment]
        if cc_key is not _KEEP:
            state.cc_key = cc_key  # type: ignore[assignment]

    mutate_parameter_state(store, key, mutate)


def set_explicit_for_test(
    store: ParamStore,
    key: ParameterKey,
    explicit: bool,
) -> None:
    base_revision = store.revision
    read = store._read()
    explicit_by_key = read.all_explicit()
    explicit_by_key[key] = explicit
    store._mutation().commit_parameter_state(
        expected_revision=base_revision,
        states=read.states(),
        meta=read.all_meta(),
        explicit_by_key=explicit_by_key,
        structure=True,
        value_keys=(),
    )


def replace_locks_for_test(
    store: ParamStore,
    keys: Iterable[ParameterKey],
) -> None:
    store._mutation().commit_locks(
        expected_revision=store.revision,
        locked_keys=set(keys),
    )


def replace_favorites_for_test(
    store: ParamStore,
    keys: Iterable[ParameterKey],
) -> None:
    next_keys = set(keys)
    if next_keys == set(store._read().favorite_keys()):
        return
    store._mutation().commit_favorites(
        expected_revision=store.revision,
        favorite_keys=next_keys,
    )


def replace_variations_for_test(
    store: ParamStore,
    variations: Iterable[Variation],
) -> None:
    store._mutation().commit_variations(
        expected_revision=store.revision,
        variations={variation.name: variation for variation in variations},
    )


def replace_collapsed_for_test(
    store: ParamStore,
    headers: Iterable[CollapsedHeaderKey],
) -> None:
    store.replace_collapsed_headers(headers)


def publish_structure_change_for_test(store: ParamStore) -> None:
    store._commit_prepared_mutation(
        touched=True,
        structure=True,
        value_keys=(),
        favorites=False,
    )


def publish_value_change_for_test(store: ParamStore) -> None:
    store._commit_prepared_mutation(
        touched=True,
        structure=False,
        value_keys=(),
        favorites=False,
    )


__all__ = [
    "merge_frame_params",
    "mutate_parameter_state",
    "mutate_runtime",
    "publish_structure_change_for_test",
    "publish_value_change_for_test",
    "record_effect_chain",
    "replace_parameter_state_for_test",
    "replace_collapsed_for_test",
    "replace_favorites_for_test",
    "replace_locks_for_test",
    "replace_variations_for_test",
    "runtime_state",
    "set_explicit_for_test",
]
