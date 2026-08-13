"""
Purpose:
    履歴・A/B比較・variationが共有するGUI-owned調整状態の不変境界を定義する。
Use when:
    liveなParamStoreから独立して調整を保存、比較、または再適用する処理を扱う場合。
Constraints:
    - mutableなParamStateやmappingを保持せず、canonicalなfrozen valueだけを所有する。
    - 完全なstore snapshotとして扱わず、適用時は現在のcode-owned構造へmergeする。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .collapsed_header import CollapsedHeaderKey
from .effects import EffectOrder, EffectTopologySignature
from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamStateSnapshot


def _parameter_key_sort_key(key: ParameterKey) -> tuple[str, str, str]:
    return key.op, key.site_id, key.arg


def _is_effect_order(value: object) -> bool:
    return type(value) is tuple and all(
        type(step) is tuple and len(step) == 2 and all(type(component) is str for component in step)
        for step in value
    )


def _is_topology_signature(value: object) -> bool:
    return type(value) is tuple and all(
        type(step) is tuple
        and len(step) == 3
        and type(step[0]) is str
        and type(step[1]) is str
        and type(step[2]) is int
        for step in value
    )


@dataclass(frozen=True, slots=True)
class ParameterAdjustment:
    """一つの parameter に保存する GUI-owned adjustment。"""

    state: ParamStateSnapshot
    meta: ParamMeta

    def __post_init__(self) -> None:
        if type(self.state) is not ParamStateSnapshot:
            raise TypeError("state must be a ParamStateSnapshot")
        if type(self.meta) is not ParamMeta:
            raise TypeError("meta must be a ParamMeta")


@dataclass(frozen=True, slots=True)
class _ParameterAdjustmentSlot:
    """History patch が保持する、存在しない側も含む一 key 分の状態。"""

    state: ParamStateSnapshot | None
    meta: ParamMeta | None


@dataclass(frozen=True, slots=True, init=False)
class ParameterAdjustmentSnapshot:
    """GUI-owned adjustment だけを保持する immutable snapshot。

    Mutable な ``ParamState`` や mapping は保持せず、query は immutable tuple
    または frozen value を返す。現在の code-owned 構造への適用は
    :class:`ParamStore` が一括して行う。
    """

    _adjustments: tuple[tuple[ParameterKey, ParameterAdjustment], ...]
    _collapsed_states: tuple[tuple[CollapsedHeaderKey, bool], ...]
    _effect_orders: tuple[tuple[str, EffectOrder | None], ...]
    _topology_signatures: tuple[tuple[str, EffectTopologySignature], ...]

    def __init__(
        self,
        *,
        adjustments: Mapping[ParameterKey, ParameterAdjustment],
        collapsed_by_header: Mapping[CollapsedHeaderKey, bool],
        effect_order_state: Mapping[str, EffectOrder | None],
        effect_topology_signatures: Mapping[str, EffectTopologySignature],
    ) -> None:
        if any(type(key) is not ParameterKey for key in adjustments):
            raise TypeError("adjustment keys must be ParameterKey values")
        if any(type(adjustment) is not ParameterAdjustment for adjustment in adjustments.values()):
            raise TypeError("adjustments must be ParameterAdjustment values")
        if any(type(key) is not CollapsedHeaderKey for key in collapsed_by_header):
            raise TypeError("collapsed header keys must be CollapsedHeaderKey values")
        if any(type(value) is not bool for value in collapsed_by_header.values()):
            raise TypeError("collapsed header states must be exact bool values")
        if any(type(chain_id) is not str for chain_id in effect_order_state):
            raise TypeError("effect order chain IDs must be exact strings")
        if any(
            order is not None and not _is_effect_order(order)
            for order in effect_order_state.values()
        ):
            raise TypeError("effect orders must be immutable operation/site tuples")
        if any(type(chain_id) is not str for chain_id in effect_topology_signatures):
            raise TypeError("effect topology chain IDs must be exact strings")
        if any(
            not _is_topology_signature(signature)
            for signature in effect_topology_signatures.values()
        ):
            raise TypeError("effect topology signatures must be immutable step tuples")

        object.__setattr__(
            self,
            "_adjustments",
            tuple(
                sorted(
                    adjustments.items(),
                    key=lambda item: _parameter_key_sort_key(item[0]),
                )
            ),
        )
        object.__setattr__(
            self,
            "_collapsed_states",
            tuple(
                sorted(
                    collapsed_by_header.items(),
                    key=lambda item: item[0].sort_key(),
                )
            ),
        )
        object.__setattr__(
            self,
            "_effect_orders",
            tuple(sorted(effect_order_state.items())),
        )
        object.__setattr__(
            self,
            "_topology_signatures",
            tuple(sorted(effect_topology_signatures.items())),
        )

    def __len__(self) -> int:
        return len(self._adjustments)

    def __contains__(self, key: object) -> bool:
        return any(current == key for current, _adjustment in self._adjustments)

    def items(self) -> tuple[tuple[ParameterKey, ParameterAdjustment], ...]:
        """ParameterKey 順の adjustment を immutable tuple で返す。"""

        return self._adjustments

    def keys(self) -> tuple[ParameterKey, ...]:
        """保存済み ParameterKey を安定順で返す。"""

        return tuple(key for key, _adjustment in self._adjustments)

    def get(self, key: ParameterKey) -> ParameterAdjustment | None:
        """``key`` の adjustment を返す。未保存なら ``None``。"""

        if type(key) is not ParameterKey:
            raise TypeError("key must be a ParameterKey")
        return next(
            (adjustment for current_key, adjustment in self._adjustments if current_key == key),
            None,
        )

    def collapsed_items(self) -> tuple[tuple[CollapsedHeaderKey, bool], ...]:
        """既知 header ごとの保存済み collapse 状態を返す。"""

        return self._collapsed_states

    def collapsed_state(self, key: CollapsedHeaderKey) -> bool | None:
        """保存済み collapse 状態を返す。未知 header なら ``None``。"""

        if type(key) is not CollapsedHeaderKey:
            raise TypeError("key must be a CollapsedHeaderKey")
        return next(
            (collapsed for current_key, collapsed in self._collapsed_states if current_key == key),
            None,
        )

    def effect_order_items(self) -> tuple[tuple[str, EffectOrder | None], ...]:
        """Chain ごとの GUI-owned order を返す。"""

        return self._effect_orders

    def effect_topology_items(
        self,
    ) -> tuple[tuple[str, EffectTopologySignature], ...]:
        """Order 適用可否に使う topology signature を返す。"""

        return self._topology_signatures

    def difference_fields(
        self,
        current: ParameterAdjustmentSnapshot,
    ) -> tuple[tuple[ParameterKey, tuple[str, ...]], ...]:
        """保存状態から見た ``current`` の parameter 差分を返す。"""

        if type(current) is not ParameterAdjustmentSnapshot:
            raise TypeError("current must be a ParameterAdjustmentSnapshot")
        saved_by_key = dict(self._adjustments)
        current_by_key = dict(current.items())
        differences: list[tuple[ParameterKey, tuple[str, ...]]] = []
        for key in sorted(
            saved_by_key.keys() | current_by_key.keys(),
            key=_parameter_key_sort_key,
        ):
            saved = saved_by_key.get(key)
            active = current_by_key.get(key)
            fields: list[str] = []
            if saved is None:
                fields.append("added")
            elif active is None:
                fields.append("missing")
            elif saved.meta.kind != active.meta.kind:
                fields.append("kind")
            else:
                if saved.state.override != active.state.override:
                    fields.append("override")
                if saved.state.ui_value != active.state.ui_value:
                    fields.append("ui_value")
                if saved.state.cc_key != active.state.cc_key:
                    fields.append("cc_key")
                if saved.meta.ui_min != active.meta.ui_min:
                    fields.append("ui_min")
                if saved.meta.ui_max != active.meta.ui_max:
                    fields.append("ui_max")
            if fields:
                differences.append((key, tuple(fields)))
        return tuple(differences)

    def with_patch(
        self,
        patch: ParameterAdjustmentPatch,
        *,
        after: bool,
    ) -> ParameterAdjustmentSnapshot:
        """History の基準を ``patch`` の指定側へ更新した新 snapshot を返す。"""

        if type(patch) is not ParameterAdjustmentPatch:
            raise TypeError("patch must be a ParameterAdjustmentPatch")
        adjustments = dict(self._adjustments)
        for key, slot in patch.parameter_items(after=after):
            if slot.state is None or slot.meta is None:
                continue
            adjustments[key] = ParameterAdjustment(state=slot.state, meta=slot.meta)
        collapsed = dict(self._collapsed_states)
        collapsed.update(patch.collapsed_items(after=after))
        return ParameterAdjustmentSnapshot(
            adjustments=adjustments,
            collapsed_by_header=collapsed,
            effect_order_state=dict(self._effect_orders),
            effect_topology_signatures=dict(self._topology_signatures),
        )


@dataclass(frozen=True, slots=True, init=False)
class ParameterAdjustmentPatch:
    """少数 parameter の GUI-owned 差分を保持する Undo/Redo entry。"""

    _before_by_key: tuple[tuple[ParameterKey, _ParameterAdjustmentSlot], ...]
    _after_by_key: tuple[tuple[ParameterKey, _ParameterAdjustmentSlot], ...]
    _collapsed_before: tuple[tuple[CollapsedHeaderKey, bool], ...]
    _collapsed_after: tuple[tuple[CollapsedHeaderKey, bool], ...]

    def __init__(
        self,
        *,
        before_by_key: Mapping[ParameterKey, _ParameterAdjustmentSlot],
        after_by_key: Mapping[ParameterKey, _ParameterAdjustmentSlot],
        collapsed_before: Mapping[CollapsedHeaderKey, bool],
        collapsed_after: Mapping[CollapsedHeaderKey, bool],
    ) -> None:
        object.__setattr__(
            self,
            "_before_by_key",
            tuple(
                sorted(
                    before_by_key.items(),
                    key=lambda item: _parameter_key_sort_key(item[0]),
                )
            ),
        )
        object.__setattr__(
            self,
            "_after_by_key",
            tuple(
                sorted(
                    after_by_key.items(),
                    key=lambda item: _parameter_key_sort_key(item[0]),
                )
            ),
        )
        object.__setattr__(
            self,
            "_collapsed_before",
            tuple(
                sorted(
                    collapsed_before.items(),
                    key=lambda item: item[0].sort_key(),
                )
            ),
        )
        object.__setattr__(
            self,
            "_collapsed_after",
            tuple(
                sorted(
                    collapsed_after.items(),
                    key=lambda item: item[0].sort_key(),
                )
            ),
        )

    @property
    def changed_keys(self) -> frozenset[ParameterKey]:
        """この entry が変更する parameter key を返す。"""

        return frozenset(key for key, _slot in self._before_by_key)

    @property
    def changed_headers(self) -> frozenset[CollapsedHeaderKey]:
        """この entry が変更する collapse header を返す。"""

        return frozenset(key for key, _collapsed in self._collapsed_before)

    def parameter_items(
        self,
        *,
        after: bool,
    ) -> tuple[tuple[ParameterKey, _ParameterAdjustmentSlot], ...]:
        """指定側の parameter slot を返す。"""

        return self._after_by_key if after else self._before_by_key

    def collapsed_items(
        self,
        *,
        after: bool,
    ) -> tuple[tuple[CollapsedHeaderKey, bool], ...]:
        """指定側の collapse 状態を返す。"""

        return self._collapsed_after if after else self._collapsed_before

    def coalesced_with(
        self,
        following: ParameterAdjustmentPatch,
    ) -> ParameterAdjustmentPatch | None:
        """同じ対象の後続 patch と一 Undo 単位へまとめる。"""

        if (
            self.changed_keys != following.changed_keys
            or self.changed_headers != following.changed_headers
        ):
            return None
        return ParameterAdjustmentPatch(
            before_by_key=dict(self.parameter_items(after=False)),
            after_by_key=dict(following.parameter_items(after=True)),
            collapsed_before=dict(self.collapsed_items(after=False)),
            collapsed_after=dict(following.collapsed_items(after=True)),
        )


__all__ = [
    "ParameterAdjustment",
    "ParameterAdjustmentSnapshot",
]
