# どこで: `src/grafix/core/parameters/effects.py`。
# 何を: effect chain の code topology と GUI-owned order override を管理する。
# なぜ: コード記述順を失わず、GUI が選んだ実効順を描画・表示・永続化で共有するため。

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from grafix.core.value_validation import exact_integer

from .identity import group_key, identity_string

EffectStepKey: TypeAlias = tuple[str, str]
EffectOrder: TypeAlias = tuple[EffectStepKey, ...]
EffectOrderPlacement: TypeAlias = Literal["before", "after"]
EffectTopologySignature: TypeAlias = tuple[tuple[str, str, int], ...]


@dataclass(frozen=True, slots=True)
class EffectStepTopology:
    """effect chain 内の一つのstepを表すcode-owned観測。"""

    op: str
    site_id: str
    n_inputs: int
    code_index: int

    def __post_init__(self) -> None:
        identity_string(self.op, name="EffectStepTopology.op")
        identity_string(self.site_id, name="EffectStepTopology.site_id")
        object.__setattr__(
            self,
            "n_inputs",
            exact_integer(self.n_inputs, name="n_inputs", minimum=1),
        )
        object.__setattr__(
            self,
            "code_index",
            exact_integer(self.code_index, name="code_index", minimum=0),
        )

    @property
    def key(self) -> EffectStepKey:
        """GUI順で使うstable identityを返す。"""

        return self.op, self.site_id


def normalize_effect_order(order: Iterable[EffectStepKey]) -> EffectOrder:
    """step key列を canonical tuple へ検証する。"""

    return tuple(group_key(key, name="effect step key") for key in order)


def topology_signature(
    steps: Sequence[EffectStepTopology],
) -> EffectTopologySignature:
    """コード順を除いたtopology互換性signatureを返す。"""

    return tuple(
        sorted(
            (step.op, step.site_id, step.n_inputs)
            for step in steps
        )
    )


def resolve_effective_steps(
    steps: Sequence[EffectStepTopology],
    override: Sequence[EffectStepKey] | None,
) -> tuple[EffectStepTopology, ...]:
    """検証済み override を code topology へ適用する。

    不完全、重複、未知 step、multi-input 制約違反は内部状態の不整合として
    明示的に拒否する。
    """

    code_steps = tuple(steps)
    if override is None:
        return code_steps
    order = normalize_effect_order(override)
    keys = tuple(step.key for step in code_steps)
    if len(set(keys)) != len(keys):
        raise ValueError("effect step identity must be unique within its chain")
    if len(order) != len(keys) or len(set(order)) != len(order) or set(order) != set(keys):
        raise ValueError("effect order must be an exact permutation of the chain")
    by_key = {step.key: step for step in code_steps}
    effective = tuple(by_key[key] for key in order)
    if any(step.n_inputs > 1 and index != 0 for index, step in enumerate(effective)):
        raise ValueError("multi-input effect must remain at the start of its chain")
    return effective


def moved_effect_order(
    order: Sequence[EffectStepKey],
    *,
    source: EffectStepKey,
    target: EffectStepKey,
    placement: EffectOrderPlacement,
) -> EffectOrder:
    """sourceをtargetの前後へ移したpermutationを返す。"""

    if placement not in {"before", "after"}:
        raise ValueError("placement must be 'before' or 'after'")
    normalized = list(normalize_effect_order(order))
    source_key = group_key(source, name="source")
    target_key = group_key(target, name="target")
    if source_key not in normalized:
        raise KeyError(f"unknown source effect step: {source_key!r}")
    if target_key not in normalized:
        raise KeyError(f"unknown target effect step: {target_key!r}")
    if source_key == target_key:
        return tuple(normalized)

    normalized.remove(source_key)
    destination = normalized.index(target_key)
    if placement == "after":
        destination += 1
    normalized.insert(destination, source_key)
    return tuple(normalized)


class EffectChainIndex:
    """effect chain topology、実効step index、GUI順を管理する。"""

    def __init__(self) -> None:
        self._step_by_site: dict[EffectStepKey, tuple[str, int]] = {}
        self._chain_ordinals: dict[str, int] = {}
        self._topology_by_chain: dict[str, tuple[EffectStepTopology, ...]] = {}
        self._order_overrides: dict[str, EffectOrder] = {}
        self._loaded_chain_ids: set[str] = set()
        self._observed_chain_ids: set[str] = set()
        self._pending_generation_chain_ids: set[str] | None = None

    def record_chain(
        self,
        *,
        chain_id: str,
        steps: Sequence[EffectStepTopology],
    ) -> bool:
        """一つのchainのcode topologyを観測し、実変更の有無を返す。"""

        chain = identity_string(chain_id, name="chain_id")
        normalized = tuple(
            EffectStepTopology(
                op=step.op,
                site_id=step.site_id,
                n_inputs=step.n_inputs,
                code_index=index,
            )
            for index, step in enumerate(steps)
        )
        if not normalized:
            raise ValueError("effect chain topology must contain at least one step")
        if len({step.key for step in normalized}) != len(normalized):
            raise ValueError("effect step identity must be unique within its chain")
        self._observed_chain_ids.add(chain)
        previous = self._topology_by_chain.get(chain)
        if previous == normalized and chain in self._chain_ordinals:
            return False
        changed = previous != normalized
        if chain not in self._chain_ordinals:
            self._chain_ordinals[chain] = max(
                self._chain_ordinals.values(), default=0
            ) + 1
            changed = True

        previous_signature = (
            None if previous is None else topology_signature(previous)
        )
        current_signature = topology_signature(normalized)
        if (
            chain in self._order_overrides
            and previous is not None
            and previous_signature != current_signature
        ):
            del self._order_overrides[chain]
            changed = True

        self._topology_by_chain[chain] = normalized
        override = self._order_overrides.get(chain)
        if override is not None:
            resolve_effective_steps(normalized, override)
        self._rebuild_step_index()
        return changed

    def get_step(self, op: str, site_id: str) -> tuple[str, int] | None:
        """(op, site_id) の実効step情報を返す。"""

        return self._step_by_site.get(
            (
                identity_string(op, name="op"),
                identity_string(site_id, name="site_id"),
            )
        )

    def step_info_by_site(self) -> dict[EffectStepKey, tuple[str, int]]:
        """(op, site_id) -> (chain_id, effective_index) のコピーを返す。"""

        return dict(self._step_by_site)

    def chain_ordinals(self) -> dict[str, int]:
        """chain_id -> ordinal のコピーを返す。"""

        return dict(self._chain_ordinals)

    def topologies(self) -> dict[str, tuple[EffectStepTopology, ...]]:
        """chain_id -> code topology のコピーを返す。"""

        return dict(self._topology_by_chain)

    def topology(self, chain_id: str) -> tuple[EffectStepTopology, ...] | None:
        """指定chainのcode topologyを返す。"""

        return self._topology_by_chain.get(
            identity_string(chain_id, name="chain_id")
        )

    def code_order(self, chain_id: str) -> EffectOrder | None:
        """指定chainのコード記述順を返す。"""

        steps = self.topology(chain_id)
        if steps is None:
            return None
        return tuple(step.key for step in steps)

    def effective_steps(
        self, chain_id: str
    ) -> tuple[EffectStepTopology, ...] | None:
        """指定chainの実効step列を返す。"""

        chain = identity_string(chain_id, name="chain_id")
        steps = self._topology_by_chain.get(chain)
        if steps is None:
            return None
        return resolve_effective_steps(steps, self._order_overrides.get(chain))

    def effective_order(self, chain_id: str) -> EffectOrder | None:
        """指定chainの実効順を返す。"""

        steps = self.effective_steps(chain_id)
        if steps is None:
            return None
        return tuple(step.key for step in steps)

    def order_overrides(self) -> dict[str, EffectOrder]:
        """GUI-owned order overrideのコピーを返す。"""

        return dict(self._order_overrides)

    def order_state_by_chain(self) -> dict[str, EffectOrder | None]:
        """既知chainごとのGUI順状態を返す。Noneはコード順を表す。"""

        return {
            chain_id: self._order_overrides.get(chain_id)
            for chain_id in self._topology_by_chain
        }

    def topology_signatures(self) -> dict[str, EffectTopologySignature]:
        """既知chainごとのorder互換性signatureを返す。"""

        return {
            chain_id: topology_signature(steps)
            for chain_id, steps in self._topology_by_chain.items()
        }

    def set_order_override(
        self,
        chain_id: str,
        order: Sequence[EffectStepKey],
    ) -> bool:
        """検証済みGUI順を設定し、実変更の有無を返す。"""

        chain = identity_string(chain_id, name="chain_id")
        steps = self._topology_by_chain.get(chain)
        if steps is None:
            raise KeyError(f"unknown effect chain: {chain!r}")
        keys = tuple(step.key for step in steps)
        normalized = normalize_effect_order(order)
        if len(set(keys)) != len(keys):
            raise ValueError(f"effect step identity is duplicated: {chain!r}")
        if (
            len(normalized) != len(keys)
            or len(set(normalized)) != len(normalized)
            or set(normalized) != set(keys)
        ):
            raise ValueError("effect order must be an exact permutation of the chain")
        resolve_effective_steps(steps, normalized)

        desired = None if normalized == keys else normalized
        current = self._order_overrides.get(chain)
        if desired is None:
            if chain not in self._order_overrides:
                return False
            del self._order_overrides[chain]
        else:
            if current == desired:
                return False
            self._order_overrides[chain] = desired
        self._rebuild_step_index()
        return True

    def reset_order(self, chain_id: str) -> bool:
        """指定chainをコード記述順へ戻す。"""

        chain = identity_string(chain_id, name="chain_id")
        if chain not in self._order_overrides:
            return False
        del self._order_overrides[chain]
        self._rebuild_step_index()
        return True

    def restore_order_state(
        self,
        state_by_chain: Mapping[str, Sequence[EffectStepKey] | None],
        *,
        topology_signatures: Mapping[str, EffectTopologySignature],
    ) -> bool:
        """Snapshot 由来の GUI 順を同一 topology の chain へ merge する。"""

        changed = False
        for raw_chain_id, saved_order in state_by_chain.items():
            chain_id = identity_string(raw_chain_id, name="chain_id")
            if chain_id not in self._topology_by_chain:
                continue
            saved_signature = topology_signatures.get(chain_id)
            if (
                saved_signature is None
                or saved_signature
                != topology_signature(self._topology_by_chain[chain_id])
            ):
                continue
            if saved_order is None:
                if chain_id in self._order_overrides:
                    del self._order_overrides[chain_id]
                    changed = True
                continue
            steps = self._topology_by_chain[chain_id]
            normalized = normalize_effect_order(saved_order)
            resolve_effective_steps(steps, normalized)
            code_order = tuple(step.key for step in steps)
            desired = None if normalized == code_order else normalized
            if desired is None:
                if chain_id in self._order_overrides:
                    del self._order_overrides[chain_id]
                    changed = True
            elif self._order_overrides.get(chain_id) != desired:
                self._order_overrides[chain_id] = desired
                changed = True
        if changed:
            self._rebuild_step_index()
        return changed

    def delete_step(
        self,
        op: str,
        site_id: str,
        *,
        preserve_observed_topology: bool = False,
    ) -> None:
        """指定stepをcode topologyと実効indexから削除する。"""

        key = (
            identity_string(op, name="op"),
            identity_string(site_id, name="site_id"),
        )
        self._step_by_site.pop(key, None)
        for chain_id, steps in tuple(self._topology_by_chain.items()):
            if (
                preserve_observed_topology
                and chain_id in self._observed_chain_ids
            ):
                continue
            remaining = tuple(step for step in steps if step.key != key)
            if remaining == steps:
                continue
            if not remaining:
                del self._topology_by_chain[chain_id]
                self._chain_ordinals.pop(chain_id, None)
                self._order_overrides.pop(chain_id, None)
                self._loaded_chain_ids.discard(chain_id)
                self._observed_chain_ids.discard(chain_id)
                continue
            self._topology_by_chain[chain_id] = tuple(
                EffectStepTopology(
                    op=step.op,
                    site_id=step.site_id,
                    n_inputs=step.n_inputs,
                    code_index=index,
                )
                for index, step in enumerate(remaining)
            )
            override = self._order_overrides.get(chain_id)
            if override is not None and set(override) != {
                step.key for step in remaining
            }:
                self._order_overrides.pop(chain_id, None)
        self._rebuild_step_index()

    def prune_unused_chains(self) -> None:
        """topologyから消えたchainのordinalとoverrideを削除する。"""

        used_chain_ids = set(self._topology_by_chain)
        for chain_id in tuple(self._chain_ordinals):
            if chain_id not in used_chain_ids:
                del self._chain_ordinals[chain_id]
                self._order_overrides.pop(chain_id, None)
        for chain_id in tuple(self._order_overrides):
            if chain_id not in used_chain_ids:
                del self._order_overrides[chain_id]
        self._loaded_chain_ids.intersection_update(used_chain_ids)
        self._observed_chain_ids.intersection_update(used_chain_ids)

    def known_chain_ids(self) -> frozenset[str]:
        """canonical topology が保持するchain IDを返す。"""

        return frozenset(self._topology_by_chain)

    def begin_observation_generation(
        self,
        *,
        additional_chain_ids: Iterable[str] = (),
    ) -> None:
        """次の完全な成功観測を新しいsource generationの基準にする。

        source reload専用の一度限りの境界であり、通常frameでは呼ばない。
        新generationの最初の成功evaluationが完了するまでは既存chainを保持する。
        """

        self._pending_generation_chain_ids = set(self.known_chain_ids()) | {
            identity_string(chain_id, name="chain_id")
            for chain_id in additional_chain_ids
        }

    def complete_observation_generation(
        self,
        observed_chain_ids: Iterable[str],
    ) -> frozenset[str]:
        """成功evaluationに無かった旧generation chainを一度だけ削除する。

        この境界では一つの成功evaluationがcanonical topologyである。従って
        条件分岐内でそのframeに現れなかったchainも削除され、後で再出現した場合は
        新規chainとして扱われる。
        """

        pending = self._pending_generation_chain_ids
        if pending is None:
            return frozenset()
        self._pending_generation_chain_ids = None
        observed = {
            identity_string(chain_id, name="chain_id")
            for chain_id in observed_chain_ids
        }
        stale = frozenset(pending - observed)
        if not stale:
            return stale

        for chain_id in stale:
            self._topology_by_chain.pop(chain_id, None)
            self._chain_ordinals.pop(chain_id, None)
            self._order_overrides.pop(chain_id, None)
        self._loaded_chain_ids.difference_update(stale)
        self._observed_chain_ids.difference_update(stale)
        self._rebuild_step_index()
        return stale

    def stale_loaded_chain_ids(self) -> frozenset[str]:
        """load後に一度も成功frameで観測されていないchain IDを返す。"""

        return frozenset(self._loaded_chain_ids - self._observed_chain_ids)

    def prune_stale_loaded_chains(self) -> frozenset[str]:
        """load後に未観測のchain構造とGUI順を削除する。"""

        stale = self.stale_loaded_chain_ids()
        if not stale:
            return frozenset()
        for chain_id in stale:
            self._topology_by_chain.pop(chain_id, None)
            self._chain_ordinals.pop(chain_id, None)
            self._order_overrides.pop(chain_id, None)
        self._loaded_chain_ids.difference_update(stale)
        self._observed_chain_ids.difference_update(stale)
        self._rebuild_step_index()
        return stale

    def replace_persisted_state(
        self,
        *,
        topologies: dict[str, tuple[EffectStepTopology, ...]],
        chain_ordinals: dict[str, int],
        order_overrides: dict[str, EffectOrder],
    ) -> None:
        """検証済みの永続化状態を型変換せず一度に置き換える。"""

        topology_by_chain = {
            chain_id: tuple(steps)
            for chain_id, steps in topologies.items()
        }

        self._topology_by_chain = topology_by_chain
        self._chain_ordinals = dict(chain_ordinals)
        self._order_overrides = dict(order_overrides)
        self._loaded_chain_ids = set(chain_ordinals) | set(topologies)
        self._observed_chain_ids = set()
        self._pending_generation_chain_ids = None
        self._rebuild_step_index()

    def _rebuild_step_index(self) -> None:
        """code topologyとoverrideから実効step indexを再構築する。"""

        step_by_site: dict[EffectStepKey, tuple[str, int]] = {}
        for chain_id, code_steps in self._topology_by_chain.items():
            effective = resolve_effective_steps(
                code_steps,
                self._order_overrides.get(chain_id),
            )
            for step_index, step in enumerate(effective):
                step_by_site[step.key] = (chain_id, step_index)
        self._step_by_site = step_by_site


__all__ = [
    "EffectChainIndex",
    "EffectOrder",
    "EffectOrderPlacement",
    "EffectStepKey",
    "EffectStepTopology",
    "EffectTopologySignature",
    "moved_effect_order",
    "normalize_effect_order",
    "resolve_effective_steps",
    "topology_signature",
]
