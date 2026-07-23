from __future__ import annotations

import pytest

from grafix import E
from grafix.api.effects import EffectBuilder
from grafix.core.operation_authoring import effect
from grafix.core.geometry import Geometry
from grafix.core.parameters.collapsed_header import effect_chain_collapsed_header_key
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.effect_order_ops import (
    begin_effect_chain_generation,
    merge_frame_effect_chains,
    move_effect_step,
    reset_effect_order,
    set_effect_order,
)
from grafix.core.parameters.frame_params import FrameEffectChainRecord
from grafix.core.parameters.effects import (
    EffectStepKey,
    EffectStepTopology,
    resolve_effective_steps,
)
from grafix.core.parameters.store import ParamStore
from grafix.core.realized_geometry import GeomTuple


@effect
def effect_order_test_first(g: GeomTuple) -> GeomTuple:
    return g


@effect
def effect_order_test_second(g: GeomTuple) -> GeomTuple:
    return g


@effect
def effect_order_test_third(g: GeomTuple) -> GeomTuple:
    return g


def _topology_keys(store: ParamStore, chain_id: str) -> tuple[EffectStepKey, ...]:
    topology = store.effect_chain_topologies()[chain_id]
    return tuple((step.op, step.site_id) for step in topology)


def _observe(
    store: ParamStore,
    builder: EffectBuilder,
    *sources: Geometry,
) -> Geometry:
    with parameter_context(store):
        return builder(*sources)


def _recipe_ops(geometry: Geometry) -> list[str]:
    result: list[str] = []
    current = geometry
    while len(current.inputs) == 1:
        result.append(current.op)
        current = current.inputs[0]
    result.append(current.op)
    return result


def test_move_set_and_reset_are_revision_aware_and_no_op_safe() -> None:
    source = Geometry.create(op="effect-order-core-source")
    builder = (
        E.effect_order_test_first(key="first")
        .effect_order_test_second(key="second")
        .effect_order_test_third(key="third")
    )
    store = ParamStore()
    _observe(store, builder, source)
    first, second, third = _topology_keys(store, builder.chain_id)

    revision = store.revision
    table_revision = store.table_revision
    assert move_effect_step(
        store,
        chain_id=builder.chain_id,
        source=first,
        target=second,
        placement="after",
    )
    expected = (second, first, third)
    assert store.effect_order_overrides()[builder.chain_id] == expected
    assert store.revision == revision + 1
    assert store.table_revision == table_revision + 1

    revision = store.revision
    table_revision = store.table_revision
    assert not move_effect_step(
        store,
        chain_id=builder.chain_id,
        source=first,
        target=second,
        placement="after",
    )
    assert not set_effect_order(
        store,
        chain_id=builder.chain_id,
        order=expected,
    )
    assert store.revision == revision
    assert store.table_revision == table_revision

    assert reset_effect_order(store, chain_id=builder.chain_id)
    assert builder.chain_id not in store.effect_order_overrides()
    revision = store.revision
    assert not reset_effect_order(store, chain_id=builder.chain_id)
    assert not set_effect_order(
        store,
        chain_id=builder.chain_id,
        order=(first, second, third),
    )
    assert store.revision == revision


def test_core_rejects_order_that_moves_multi_input_step_from_front() -> None:
    first_source = Geometry.create(op="effect-order-input-a")
    second_source = Geometry.create(op="effect-order-input-b")
    builder = E.boolean(mode="union", key="binary").effect_order_test_first(
        key="unary"
    )
    store = ParamStore()
    _observe(store, builder, first_source, second_source)
    binary, unary = _topology_keys(store, builder.chain_id)
    revision = store.revision

    with pytest.raises(ValueError, match="multi-input|先頭"):
        set_effect_order(
            store,
            chain_id=builder.chain_id,
            order=(unary, binary),
        )
    with pytest.raises(ValueError, match="multi-input|先頭"):
        move_effect_step(
            store,
            chain_id=builder.chain_id,
            source=binary,
            target=unary,
            placement="after",
        )

    assert store.effect_order_overrides() == {}
    assert store.revision == revision


def test_topology_replacement_discards_stale_override_on_successful_merge() -> None:
    source = Geometry.create(op="effect-order-stale-source")
    first_two = E.effect_order_test_first(
        key="stale-first"
    ).effect_order_test_second(key="stale-second")
    with_third = first_two.effect_order_test_third(key="stale-third")
    store = ParamStore()
    _observe(store, first_two, source)
    first, second = _topology_keys(store, first_two.chain_id)
    assert set_effect_order(
        store,
        chain_id=first_two.chain_id,
        order=(second, first),
    )

    geometry = _observe(store, with_third, source)

    assert _recipe_ops(geometry) == [
        "effect_order_test_third",
        "effect_order_test_second",
        "effect_order_test_first",
        "effect-order-stale-source",
    ]
    assert first_two.chain_id not in store.effect_order_overrides()
    assert len(store.effect_chain_topologies()[first_two.chain_id]) == 3


def test_stable_topology_does_not_advance_revision_but_change_does() -> None:
    source = Geometry.create(op="effect-order-topology-source")
    first_two = E.effect_order_test_first(
        key="revision-first"
    ).effect_order_test_second(key="revision-second")
    with_third = first_two.effect_order_test_third(key="revision-third")
    store = ParamStore()

    _observe(store, first_two, source)
    revision = store.revision
    table_revision = store.table_revision
    _observe(store, first_two, source)
    assert store.revision == revision
    assert store.table_revision == table_revision

    _observe(store, with_third, source)
    assert store.revision == revision + 1
    assert store.table_revision == table_revision + 1
    revision = store.revision
    _observe(store, with_third, source)
    assert store.revision == revision


def test_resolve_effective_steps_uses_only_an_exact_permutation() -> None:
    first = EffectStepTopology(
        op="first",
        site_id="site-first",
        n_inputs=1,
        code_index=0,
    )
    second = EffectStepTopology(
        op="second",
        site_id="site-second",
        n_inputs=1,
        code_index=1,
    )
    steps = (first, second)
    first_key: EffectStepKey = ("first", "site-first")
    second_key: EffectStepKey = ("second", "site-second")

    assert resolve_effective_steps(steps, (second_key, first_key)) == (
        second,
        first,
    )
    assert resolve_effective_steps(steps, None) == steps
    with pytest.raises(ValueError, match="exact permutation"):
        resolve_effective_steps(steps, (first_key,))
    with pytest.raises(ValueError, match="exact permutation"):
        resolve_effective_steps(steps, (first_key, first_key))
    with pytest.raises(ValueError, match="exact permutation"):
        resolve_effective_steps(
            steps,
            (first_key, ("removed", "old-site")),
        )


def _chain_record(
    chain_id: str,
    *steps: tuple[str, str],
) -> FrameEffectChainRecord:
    return FrameEffectChainRecord(
        chain_id=chain_id,
        steps=tuple(
            EffectStepTopology(
                op=op,
                site_id=site_id,
                n_inputs=1,
                code_index=index,
            )
            for index, (op, site_id) in enumerate(steps)
        ),
    )


def test_reload_generation_prunes_only_once_from_canonical_success_topology() -> None:
    store = ParamStore()
    keep = _chain_record(
        "keep-chain",
        ("keep-first", "keep-site-first"),
        ("keep-second", "keep-site-second"),
    )
    stale = _chain_record(
        "stale-chain",
        ("stale-first", "stale-site-first"),
        ("stale-second", "stale-site-second"),
    )
    assert merge_frame_effect_chains(
        store,
        [keep, stale],
        observation_complete=False,
    )
    assert set_effect_order(
        store,
        chain_id=stale.chain_id,
        order=tuple(step.key for step in reversed(stale.steps)),
    )
    store.set_all_collapsed(
        {
            effect_chain_collapsed_header_key("keep-chain"),
            effect_chain_collapsed_header_key("stale-chain"),
            effect_chain_collapsed_header_key("orphan-chain"),
        },
        collapsed=True,
    )

    revision = store.revision
    begin_effect_chain_generation(store)
    assert store.revision == revision

    added = _chain_record("added-chain", ("added", "added-site"))
    assert merge_frame_effect_chains(
        store,
        [keep, added],
        observation_complete=True,
    )
    assert store.revision == revision + 1
    assert set(store.effect_chain_topologies()) == {"keep-chain", "added-chain"}
    assert set(store.chain_ordinals()) == {"keep-chain", "added-chain"}
    assert "stale-chain" not in store.effect_order_overrides()
    collapsed = store.collapsed_headers()
    assert effect_chain_collapsed_header_key("keep-chain") in collapsed
    assert effect_chain_collapsed_header_key("stale-chain") not in collapsed
    assert effect_chain_collapsed_header_key("orphan-chain") not in collapsed

    # generation確定後の通常frameは条件分岐の不在をprune根拠にしない。
    revision = store.revision
    assert not merge_frame_effect_chains(
        store,
        [added],
        observation_complete=True,
    )
    assert "keep-chain" in store.effect_chain_topologies()
    assert store.revision == revision
