"""
Purpose:
    effect chainの観測topologyとGUI-owned適用順をParamStoreへ問い合わせ・更新する。
Use when:
    effectの並べ替え、reset、frame観測merge、またはsource generation交換を扱う場合。
Constraints:
    - order overrideは現在topologyの完全なpermutationとし、multi-input stepを先頭に保つ。
    - generation交換後のcanonical topologyは最初の完全な成功観測でのみ確定する。
    - 失敗観測やno-opから既存chain、revision、collapse状態を不用意に変えない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import TypeAlias

from .collapsed_header import effect_chain_collapsed_header_key
from .effects import (
    EffectOrder,
    EffectOrderPlacement,
    EffectStepKey,
    moved_effect_order,
)
from .frame_params import FrameEffectChainRecord
from .store import ParamStore

EffectOrderSnapshot: TypeAlias = Mapping[str, EffectOrder]

_EMPTY_EFFECT_ORDER_SNAPSHOT: EffectOrderSnapshot = MappingProxyType({})


def store_effect_order_snapshot(store: ParamStore) -> EffectOrderSnapshot:
    """現在のGUI-owned order overrideをimmutable snapshotへ固定する。"""

    overrides = store._read().effects().order_overrides()
    if not overrides:
        return _EMPTY_EFFECT_ORDER_SNAPSHOT
    return MappingProxyType(overrides)


def begin_effect_chain_generation(store: ParamStore) -> None:
    """source reload後の次の成功evaluationをcanonical topologyにする。

    通常frameの条件分岐ではchainを蓄積したままにするため、このoperationは
    source generation交換時だけ呼ぶ。開始自体は公開状態を変えないのでrevisionを
    進めず、失敗evaluationでは保留状態も既存chainも維持する。
    """

    base_revision = store.revision
    read = store._read()
    collapsed_chain_ids = {
        header.chain_id
        for header in read.collapsed_headers()
        if header.kind == "effect_chain" and header.chain_id is not None
    }
    effects = read.effects()
    effects.begin_observation_generation(
        additional_chain_ids=collapsed_chain_ids,
    )
    store._mutation().commit_effect_observation_start(
        expected_revision=base_revision,
        effects=effects,
    )


def merge_frame_effect_chains(
    store: ParamStore,
    records: Sequence[FrameEffectChainRecord],
    *,
    observation_complete: bool,
) -> bool:
    """成功frameで観測したeffect topologyをstoreへmergeする。

    ``observation_complete`` は実際に一つのevaluationが成功した場合だけ指定する。
    source reload後の最初の完全な成功観測では、そのrecord集合をcanonicalとし、
    旧generationにしかないchainとcollapse状態を一度だけ削除する。
    """

    latest_by_chain: dict[str, FrameEffectChainRecord] = {}
    for record in records:
        latest_by_chain[record.chain_id] = record
    base_revision = store.revision
    read = store._read()
    changed = False
    effects = read.effects()
    collapsed = set(read.collapsed_headers())
    for record in latest_by_chain.values():
        changed = (
            effects.record_chain(
                chain_id=record.chain_id,
                steps=record.steps,
            )
            or changed
        )
    if observation_complete:
        stale_chain_ids = effects.complete_observation_generation(
            latest_by_chain,
        )
        if stale_chain_ids:
            for chain_id in stale_chain_ids:
                collapsed.discard(
                    effect_chain_collapsed_header_key(chain_id)
                )
            changed = True
    if changed:
        store._mutation().commit_effects(
            expected_revision=base_revision,
            effects=effects,
            collapsed_headers=collapsed,
        )
    elif records:
        # record_chain は topology が同一でも observed generation を更新する。
        # これは runtime-only state のため永続 revision は進めない。
        store._mutation().commit_effect_observation_start(
            expected_revision=base_revision,
            effects=effects,
        )
    return changed


def set_effect_order(
    store: ParamStore,
    *,
    chain_id: str,
    order: Sequence[EffectStepKey],
) -> bool:
    """指定chainのGUI順を完全なpermutationで設定する。"""

    base_revision = store.revision
    read = store._read()
    effects = read.effects()
    changed = effects.set_order_override(chain_id, order)
    if changed:
        store._mutation().commit_effects(
            expected_revision=base_revision,
            effects=effects,
            collapsed_headers=set(read.collapsed_headers()),
        )
    return changed


def move_effect_step(
    store: ParamStore,
    *,
    chain_id: str,
    source: EffectStepKey,
    target: EffectStepKey,
    placement: EffectOrderPlacement,
) -> bool:
    """同一chain内のstepをtargetの前後へ移動する。"""

    effects = store._read().effects()
    current = effects.effective_order(chain_id)
    if current is None:
        raise KeyError(f"unknown effect chain: {chain_id!r}")
    moved = moved_effect_order(
        current,
        source=source,
        target=target,
        placement=placement,
    )
    return set_effect_order(store, chain_id=chain_id, order=moved)


def reset_effect_order(store: ParamStore, *, chain_id: str) -> bool:
    """指定chainをコード記述順へ戻す。"""

    base_revision = store.revision
    read = store._read()
    effects = read.effects()
    changed = effects.reset_order(chain_id)
    if changed:
        store._mutation().commit_effects(
            expected_revision=base_revision,
            effects=effects,
            collapsed_headers=set(read.collapsed_headers()),
        )
    return changed


__all__ = [
    "EffectOrderSnapshot",
    "begin_effect_chain_generation",
    "merge_frame_effect_chains",
    "move_effect_step",
    "reset_effect_order",
    "set_effect_order",
    "store_effect_order_snapshot",
]
