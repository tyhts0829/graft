# どこで: `src/grafix/core/parameters/store.py`。
# 何を: ParamStore（永続データの核）を定義する。
# なぜ: God-object 化を避け、周辺ロジック（ordinal/reconcile/永続化など）を別モジュールへ分離するため。

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, MutableSet
from copy import deepcopy
from dataclasses import dataclass, field, replace
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, Any

from .adjustment_snapshot import (
    ParameterAdjustment,
    ParameterAdjustmentPatch,
    ParameterAdjustmentSnapshot,
    _ParameterAdjustmentSlot,
)
from .collapsed_header import (
    CollapsedHeaderKey,
    STYLE_COLLAPSED_HEADER_KEY,
    effect_chain_collapsed_header_key,
    group_collapsed_header_keys,
)
from .effects import EffectChainIndex, EffectOrder, EffectStepTopology
from .key import ParameterKey
from .labels import ParamLabels
from .meta import ParamMeta
from .ordinals import GroupOrdinals
from .runtime import (
    LoadProvenance,
    ParamRuntimeView,
    ParamStoreLoadDiagnostic,
    ParamStoreRuntime,
)
from .state import ParamState, ParamStateSnapshot

if TYPE_CHECKING:
    from .variations import Variation


@dataclass(frozen=True, slots=True)
class _TransientParamStoreState:
    """Transient rollback が所有する ParamStore の論理状態。"""

    states: dict[ParameterKey, ParamState]
    meta: dict[ParameterKey, ParamMeta]
    explicit_by_key: dict[ParameterKey, bool]
    labels: ParamLabels
    ordinals: GroupOrdinals
    effects: EffectChainIndex
    collapsed_headers: set[CollapsedHeaderKey]
    locked_keys: set[ParameterKey]
    favorite_keys: set[ParameterKey]
    variations: dict[str, Variation]
    runtime: ParamStoreRuntime
    revision: int
    table_revision: int
    value_revision: int
    style_revision: int
    favorite_revision: int
    value_change_log: deque[tuple[int, tuple[ParameterKey, ...]]]


@dataclass(slots=True)
class _PendingStoreMutation:
    """一つの core command 内でまとめる revision 更新。"""

    owner: object
    touched: bool = False
    structure: bool = False
    value_keys: list[ParameterKey] = field(default_factory=list)
    favorites: bool = False


def _known_adjustment_headers(
    states: Mapping[ParameterKey, ParamState],
    effects: EffectChainIndex,
) -> set[CollapsedHeaderKey]:
    """現在の store 構造から存在し得る GUI header ID を返す。"""

    groups = {(key.op, key.site_id) for key in states}
    known: set[CollapsedHeaderKey] = set()
    style_ops = {"__style__", "__layer_style__"}
    if any(op in style_ops for op, _site_id in groups):
        known.add(STYLE_COLLAPSED_HEADER_KEY)
    for op, site_id in groups:
        if op in style_ops:
            continue
        # core は preset registry に依存しないため、同じ group が取り得る
        # primitive/preset の両 identity を保存する。
        known.update(group_collapsed_header_keys((op, site_id)))

    for group, (chain_id, _step_index) in effects.step_info_by_site().items():
        if group in groups:
            known.add(effect_chain_collapsed_header_key(chain_id))
    return known


class ParamStoreRollback:
    """一つの ParamStore に属する one-shot transient rollback scope。"""

    __slots__ = ("_active", "_state", "_store", "_used")

    def __init__(self, store: ParamStore) -> None:
        self._store = store
        self._state: _TransientParamStoreState | None = None
        self._active = False
        self._used = False

    def __enter__(self) -> ParamStoreRollback:
        """開始時の論理状態を退避し、この scope を有効にする。"""

        if self._used:
            raise RuntimeError("ParamStoreRollback is one-shot")
        self._used = True
        store = self._store
        store._begin_transient_rollback(self)
        try:
            self._state = store._capture_transient_state()
        except BaseException:
            store._end_transient_rollback(self)
            raise
        self._active = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """終了理由にかかわらず開始時の論理状態へ戻す。"""

        if not self._active:
            raise RuntimeError("ParamStoreRollback is not active")
        store = self._store
        try:
            store._restore_transient_rollback(self)
        finally:
            store._end_transient_rollback(self)
            self._active = False
            self._state = None


class _FavoriteKeySet(MutableSet[ParameterKey]):
    """favorite mutation を store revision へ接続する mutable view。"""

    __slots__ = ("_store",)

    def __init__(self, store: ParamStore) -> None:
        self._store = store

    def __contains__(self, key: object) -> bool:
        return key in self._store._favorite_keys_data

    def __iter__(self) -> Iterator[ParameterKey]:
        return iter(self._store._favorite_keys_data)

    def __len__(self) -> int:
        return len(self._store._favorite_keys_data)

    def add(self, key: ParameterKey) -> None:
        data = self._store._favorite_keys_data
        if key in data:
            return
        data.add(key)
        self._store._touch_favorites()

    def discard(self, key: ParameterKey) -> None:
        data = self._store._favorite_keys_data
        if key not in data:
            return
        data.discard(key)
        self._store._touch_favorites()


class ParamStore:
    """ParameterKey -> ParamState を保持する永続ストア。

    Notes
    -----
    - このクラスは「永続データの入れ物」に寄せる。
    - parameter lock / favorite は永続 UI state として保持する。
    - 外部へはミュータブルな参照（ParamState）を渡さない。
      変更は ops 経由で行う想定とする。
    """

    def __init__(self) -> None:
        self._states: dict[ParameterKey, ParamState] = {}
        self._meta: dict[ParameterKey, ParamMeta] = {}
        self._explicit_by_key: dict[ParameterKey, bool] = {}

        self._labels = ParamLabels()
        self._ordinals = GroupOrdinals()
        self._effects = EffectChainIndex()
        self._collapsed_headers: set[CollapsedHeaderKey] = set()
        self._locked_keys: set[ParameterKey] = set()
        self._favorite_keys_data: set[ParameterKey] = set()
        self._variations: dict[str, Variation] = {}

        # 永続化しない実行時情報（loaded/observed/reconcile-applied）。
        self._runtime = ParamStoreRuntime()
        self._revision = 0
        self._table_revision = 0
        self._value_revision = 0
        self._style_revision = 0
        self._favorite_revision = 0
        self._favorite_snapshot_revision = -1
        self._favorite_snapshot: frozenset[ParameterKey] = frozenset()
        self._favorite_tuple: tuple[ParameterKey, ...] = ()
        self._value_change_log: deque[tuple[int, tuple[ParameterKey, ...]]] = deque(
            maxlen=4096
        )
        self._history_key_observer: Callable[[ParameterKey], None] | None = None
        self._history_headers_observer: (
            Callable[[frozenset[CollapsedHeaderKey] | None], None] | None
        ) = None
        self._history_transaction_owner: object | None = None
        self._active_transient_rollback: ParamStoreRollback | None = None
        self._pending_mutation: _PendingStoreMutation | None = None
        self._snapshot_cache_revision = -1
        self._snapshot_cache_value_revision = -1
        self._snapshot_cache_rebuilt_entries = 0
        self._snapshot_cache: object | None = None

    @property
    def revision(self) -> int:
        """snapshot/model に影響する永続状態の変更時だけ増える単調 revision。"""

        return self._revision

    @property
    def effective_revision(self) -> int:
        """直近 frame の effective/source snapshot が変わるたびに増える revision。"""

        return self._runtime.effective_revision

    @property
    def table_revision(self) -> int:
        """Parameter GUI の行構造・静的属性が変わったときだけ増える revision。"""

        return self._table_revision

    @property
    def value_revision(self) -> int:
        """既存 parameter の表示値が変わったときだけ増える revision。"""

        return self._value_revision

    @property
    def style_revision(self) -> int:
        """global/layer style の値または関連し得る構造が変わる revision。"""

        return self._style_revision

    @property
    def favorite_revision(self) -> int:
        """favorite 集合が変化したときだけ増える単調 revision。"""

        return self._favorite_revision

    def _replace_favorite_keys(self, keys: Iterable[ParameterKey]) -> bool:
        """favorite 集合を置換し、変更時だけ revision を一度進める。"""

        normalized = set(keys)
        if normalized == self._favorite_keys_data:
            return False
        self._favorite_keys_data = normalized
        self._touch_favorites()
        return True

    def replace_contents_from(self, source: ParamStore) -> None:
        """object identity を保ち、別 store の全内容へ一度に置換する。

        Parameters
        ----------
        source : ParamStore
            置換後の内容を所有する別の store。

        Raises
        ------
        TypeError
            ``source`` が exact ``ParamStore`` でない場合。
        ValueError
            自分自身を ``source`` に指定した場合。
        RuntimeError
            history transaction の途中で置換しようとした場合。
        """

        if type(source) is not ParamStore:
            raise TypeError("source must be a ParamStore")
        if source is self:
            raise ValueError("source must be a different ParamStore")
        if (
            self._history_transaction_owner is not None
            or self._history_key_observer is not None
            or self._history_headers_observer is not None
        ):
            raise RuntimeError("cannot replace ParamStore during a history transaction")

        next_revision = self._revision + 1
        next_table_revision = self._table_revision + 1
        next_value_revision = self._value_revision + 1
        next_style_revision = self._style_revision + 1
        next_favorite_revision = self._favorite_revision + 1
        old_runtime = self._runtime
        next_effective_revision = old_runtime.effective_revision + 1
        next_visibility_revision = old_runtime.visibility_revision + 1

        (
            states,
            meta,
            explicit_by_key,
            labels,
            ordinals,
            effects,
            collapsed_headers,
            locked_keys,
            favorite_keys,
            variations,
            runtime,
        ) = deepcopy(
            (
                source._states,
                source._meta,
                source._explicit_by_key,
                source._labels,
                source._ordinals,
                source._effects,
                source._collapsed_headers,
                source._locked_keys,
                set(source._favorite_keys_snapshot()),
                source._variations,
                source._runtime,
            )
        )

        self._states = states
        self._meta = meta
        self._explicit_by_key = explicit_by_key
        self._labels = labels
        self._ordinals = ordinals
        self._effects = effects
        self._collapsed_headers = collapsed_headers
        self._locked_keys = locked_keys
        self._favorite_keys_data = favorite_keys
        self._variations = variations
        self._runtime = runtime

        self._revision = next_revision
        self._table_revision = next_table_revision
        self._value_revision = next_value_revision
        self._style_revision = next_style_revision
        self._favorite_revision = next_favorite_revision
        self._favorite_snapshot_revision = -1
        self._favorite_snapshot = frozenset()
        self._favorite_tuple = ()
        self._value_change_log.clear()
        self._snapshot_cache_revision = -1
        self._snapshot_cache_value_revision = -1
        self._snapshot_cache_rebuilt_entries = 0
        self._snapshot_cache = None

        runtime = self._runtime
        runtime.effective_revision = next_effective_revision
        runtime._effective_change_revision = -1
        runtime._effective_changed_keys = ()
        runtime._visibility_tracker.revision = next_visibility_revision

    def begin_transient_rollback(self) -> ParamStoreRollback:
        """終了時に現在の論理状態へ正確に戻す one-shot scope を返す。

        Returns
        -------
        ParamStoreRollback
            正常終了・例外終了の双方で開始時の状態へ戻す context manager。

        Notes
        -----
        この scope は variation batch のような一時評価用であり、Undo/Redo
        history には記録しない。scope と active history transaction は相互に
        nest できない。
        """

        return ParamStoreRollback(self)

    @property
    def load_provenance(self) -> LoadProvenance:
        """現在のデータを復元した load 経路を返す。"""

        return self._runtime.load_provenance

    @property
    def load_diagnostics(self) -> tuple[ParamStoreLoadDiagnostic, ...]:
        """load 中の recovery/quarantine 診断を返す。"""

        return self._runtime.load_diagnostics

    def runtime_view(self) -> ParamRuntimeView:
        """GUI が必要とする runtime 情報の read-only view を返す。"""

        runtime = self._runtime
        # key/value は FrameParamRecord 境界で canonical immutable value に
        # 固定済みなので、mapping 自体の浅い snapshot だけを所有すればよい。
        return ParamRuntimeView(
            loaded_groups=frozenset(runtime.loaded_groups),
            observed_groups=frozenset(runtime.observed_groups),
            display_order_by_group=MappingProxyType(
                dict(runtime.display_order_by_group)
            ),
            last_effective_by_key=MappingProxyType(
                dict(runtime.last_effective_by_key)
            ),
            last_source_by_key=MappingProxyType(
                dict(runtime.last_source_by_key)
            ),
            effective_revision=int(runtime.effective_revision),
            visibility_revision=int(runtime.visibility_revision),
        )

    def effective_changes_since(
        self,
        revision: int,
    ) -> frozenset[ParameterKey] | None:
        """指定 runtime revision 以降の effective/source 変更 key を返す。"""

        return self._runtime.effective_changes_since(revision)

    def last_effective_value(self, key: ParameterKey) -> object | None:
        """直近 frame の effective value を返す。未観測なら ``None``。"""

        return self._runtime.last_effective_by_key.get(key)

    def record_unknown_argument_warnings(
        self,
        pairs: Iterable[tuple[str, str]],
    ) -> frozenset[tuple[str, str]]:
        """未警告の operation/argument 組を記録し、新規分だけ返す。"""

        warned = self._runtime.warned_unknown_args
        new_pairs = frozenset(pairs) - warned
        warned.update(new_pairs)
        return new_pairs

    def accept_loaded_state(self) -> bool:
        """recovery 済み runtime 診断を primary として受理する。"""

        runtime = self._runtime
        if runtime.load_provenance == "primary" and not runtime.load_diagnostics:
            return False
        runtime.load_provenance = "primary"
        runtime.load_diagnostics = ()
        return True

    def collapsed_headers(self) -> frozenset[CollapsedHeaderKey]:
        """現在の折りたたみ header の immutable snapshot を返す。"""

        return frozenset(self._collapsed_headers)

    def set_collapsed(
        self,
        header: CollapsedHeaderKey,
        *,
        collapsed: bool,
    ) -> bool:
        """一つの header の折りたたみ状態を変更する。"""

        return bool(self.set_all_collapsed((header,), collapsed=collapsed))

    def set_all_collapsed(
        self,
        headers: Iterable[CollapsedHeaderKey],
        *,
        collapsed: bool,
    ) -> tuple[CollapsedHeaderKey, ...]:
        """複数 header を一括変更し、実際に変わった header を返す。"""

        if type(collapsed) is not bool:
            raise TypeError("collapsed must be an exact bool")
        ordered = tuple(dict.fromkeys(headers))
        if not all(isinstance(header, CollapsedHeaderKey) for header in ordered):
            raise TypeError("headers must contain only CollapsedHeaderKey values")
        current = self._collapsed_headers
        changed = tuple(
            header
            for header in ordered
            if (header in current) != collapsed
        )
        if not changed:
            return ()
        before = frozenset(current)
        self._observe_history_headers_before(before)
        if collapsed:
            current.update(changed)
        else:
            current.difference_update(changed)
        self._touch(structure=False)
        return changed

    def replace_collapsed_headers(
        self,
        headers: Iterable[CollapsedHeaderKey],
    ) -> bool:
        """折りたたみ header 全体を一度の command として置換する。"""

        normalized = set(headers)
        if not all(isinstance(header, CollapsedHeaderKey) for header in normalized):
            raise TypeError("headers must contain only CollapsedHeaderKey values")
        before = frozenset(self._collapsed_headers)
        if normalized == self._collapsed_headers:
            return False
        self._observe_history_headers_before(before)
        self._collapsed_headers = normalized
        self._touch(structure=False)
        return True

    def variation_count(self) -> int:
        """保存済み named variation の件数を返す。"""

        return len(self._variations)

    def get_state(self, key: ParameterKey) -> ParamState | None:
        """登録済みの ParamState を返す。未登録なら None。"""

        state = self._states.get(key)
        if state is None:
            return None
        return ParamState(**vars(state))

    def get_meta(self, key: ParameterKey) -> ParamMeta | None:
        """登録済みの ParamMeta を返す。未登録なら None。"""

        return self._meta.get(key)

    def capture_adjustment_snapshot(self) -> ParameterAdjustmentSnapshot:
        """現在の GUI-owned adjustment を immutable snapshot で返す。"""

        known_headers = _known_adjustment_headers(self._states, self._effects)
        return ParameterAdjustmentSnapshot(
            adjustments={
                key: ParameterAdjustment(
                    state=ParamStateSnapshot.from_state(state),
                    meta=self._meta[key],
                )
                for key, state in self._states.items()
                if key in self._meta
            },
            collapsed_by_header={
                header: header in self._collapsed_headers
                for header in known_headers
            },
            effect_order_state=self._effects.order_state_by_chain(),
            effect_topology_signatures=self._effects.topology_signatures(),
        )

    def adjustment_snapshot_matches(
        self,
        snapshot: ParameterAdjustmentSnapshot,
    ) -> bool:
        """``snapshot`` の merge 適用で adjustment が変わらなければ True。"""

        if type(snapshot) is not ParameterAdjustmentSnapshot:
            raise TypeError("snapshot must be a ParameterAdjustmentSnapshot")

        for _key, saved, current_state, current_meta in self._applicable_adjustments(
            snapshot
        ):
            if (
                current_state.override != saved.state.override
                or current_state.ui_value != saved.state.ui_value
                or current_state.cc_key != saved.state.cc_key
                or current_meta.ui_min != saved.meta.ui_min
                or current_meta.ui_max != saved.meta.ui_max
            ):
                return False

        known_headers = _known_adjustment_headers(self._states, self._effects)
        for header, saved_collapsed in snapshot.collapsed_items():
            if header not in known_headers:
                continue
            if (header in self._collapsed_headers) != saved_collapsed:
                return False

        candidate_effects = deepcopy(self._effects)
        if candidate_effects.restore_order_state(
            dict(snapshot.effect_order_items()),
            topology_signatures=dict(snapshot.effect_topology_items()),
        ):
            return False
        return True

    def apply_adjustment_snapshot(
        self,
        snapshot: ParameterAdjustmentSnapshot,
    ) -> bool:
        """Snapshot を現在の code-owned 構造へ原子的に merge 適用する。"""

        if type(snapshot) is not ParameterAdjustmentSnapshot:
            raise TypeError("snapshot must be a ParameterAdjustmentSnapshot")
        if self.adjustment_snapshot_matches(snapshot):
            return False

        changed_value_keys: list[ParameterKey] = []
        structure_changed = False
        for key, saved, current_state, current_meta in self._applicable_adjustments(
            snapshot
        ):
            if (
                current_state.override != saved.state.override
                or current_state.ui_value != saved.state.ui_value
                or current_state.cc_key != saved.state.cc_key
            ):
                current_state.override = saved.state.override
                current_state.ui_value = saved.state.ui_value
                current_state.cc_key = saved.state.cc_key
                changed_value_keys.append(key)

            if (
                current_meta.ui_min != saved.meta.ui_min
                or current_meta.ui_max != saved.meta.ui_max
            ):
                self._meta[key] = replace(
                    current_meta,
                    ui_min=saved.meta.ui_min,
                    ui_max=saved.meta.ui_max,
                )
                structure_changed = True

        known_headers = _known_adjustment_headers(self._states, self._effects)
        for header, saved_collapsed in snapshot.collapsed_items():
            if header not in known_headers:
                continue
            if saved_collapsed and header not in self._collapsed_headers:
                self._collapsed_headers.add(header)
                structure_changed = True
            elif not saved_collapsed and header in self._collapsed_headers:
                self._collapsed_headers.discard(header)
                structure_changed = True

        if self._effects.restore_order_state(
            dict(snapshot.effect_order_items()),
            topology_signatures=dict(snapshot.effect_topology_items()),
        ):
            structure_changed = True

        changed = structure_changed or bool(changed_value_keys)
        if changed:
            # Revision は過去値へ戻さず、restore 全体で一度だけ進める。
            self._touch(
                structure=structure_changed,
                value_keys=changed_value_keys,
            )
        return changed

    def get_label(self, op: str, site_id: str) -> str | None:
        """(op, site_id) のラベルを返す。未登録なら None。"""

        return self._labels.get(op, site_id)

    def get_ordinal(self, op: str, site_id: str) -> int | None:
        """(op, site_id) の ordinal を返す。未登録なら None。"""

        return self._ordinals.get(op, site_id)

    def get_effect_step(self, op: str, site_id: str) -> tuple[str, int] | None:
        """(op, site_id) の effect ステップ情報を返す。未登録なら None。"""

        return self._effects.get_step(op, site_id)

    def effect_steps(self) -> dict[tuple[str, str], tuple[str, int]]:
        """(op, site_id) -> (chain_id, effective_index) のコピーを返す。"""

        return self._effects.step_info_by_site()

    def effect_chain_topologies(
        self,
    ) -> dict[str, tuple[EffectStepTopology, ...]]:
        """chain_id -> code topology のコピーを返す。"""

        return self._effects.topologies()

    def effect_order_overrides(self) -> dict[str, EffectOrder]:
        """chain_id -> GUI-owned order override のコピーを返す。"""

        return self._effects.order_overrides()

    def chain_ordinals(self) -> dict[str, int]:
        """chain_id -> ordinal のコピーを返す。"""

        return self._effects.chain_ordinals()

    # --- 内部 API（ops/codec からのみ利用する想定）---
    def _applicable_adjustments(
        self,
        snapshot: ParameterAdjustmentSnapshot,
    ) -> list[
        tuple[ParameterKey, ParameterAdjustment, ParamState, ParamMeta]
    ]:
        """現在の code-owned 構造へ安全に適用できる entry を返す。"""

        applicable: list[
            tuple[ParameterKey, ParameterAdjustment, ParamState, ParamMeta]
        ] = []
        for key, saved in snapshot.items():
            current_state = self._states.get(key)
            current_meta = self._meta.get(key)
            if current_state is None or current_meta is None:
                continue
            if saved.meta.kind != current_meta.kind:
                continue
            applicable.append((key, saved, current_state, current_meta))
        return applicable

    def _capture_adjustment_slot(
        self,
        key: ParameterKey,
    ) -> _ParameterAdjustmentSlot:
        """History patch 用に一 key の現在値を immutable 化する。"""

        state = self._states.get(key)
        return _ParameterAdjustmentSlot(
            state=None if state is None else ParamStateSnapshot.from_state(state),
            meta=self._meta.get(key),
        )

    def _apply_adjustment_patch(
        self,
        patch: ParameterAdjustmentPatch,
        *,
        after: bool,
    ) -> bool:
        """History patch の変更前または変更後を merge 適用する。"""

        if type(patch) is not ParameterAdjustmentPatch:
            raise TypeError("patch must be a ParameterAdjustmentPatch")

        changed_value_keys: list[ParameterKey] = []
        meta_changed = False
        for key, saved in patch.parameter_items(after=after):
            saved_state = saved.state
            saved_meta = saved.meta
            current_state = self._states.get(key)
            current_meta = self._meta.get(key)
            if (
                saved_state is None
                or saved_meta is None
                or current_state is None
                or current_meta is None
                or saved_meta.kind != current_meta.kind
            ):
                continue

            state_changed = (
                current_state.override != saved_state.override
                or current_state.ui_value != saved_state.ui_value
                or current_state.cc_key != saved_state.cc_key
            )
            if state_changed:
                current_state.override = saved_state.override
                current_state.ui_value = saved_state.ui_value
                current_state.cc_key = saved_state.cc_key
                changed_value_keys.append(key)

            if (
                current_meta.ui_min != saved_meta.ui_min
                or current_meta.ui_max != saved_meta.ui_max
            ):
                self._meta[key] = replace(
                    current_meta,
                    ui_min=saved_meta.ui_min,
                    ui_max=saved_meta.ui_max,
                )
                meta_changed = True

        headers_changed = False
        header_states = patch.collapsed_items(after=after)
        if header_states:
            known_headers = _known_adjustment_headers(self._states, self._effects)
            for header, should_collapse in header_states:
                if header not in known_headers:
                    continue
                if should_collapse and header not in self._collapsed_headers:
                    self._collapsed_headers.add(header)
                    headers_changed = True
                elif not should_collapse and header in self._collapsed_headers:
                    self._collapsed_headers.discard(header)
                    headers_changed = True

        if not changed_value_keys and not meta_changed and not headers_changed:
            return False
        self._touch(
            structure=bool(meta_changed or headers_changed),
            value_keys=changed_value_keys,
        )
        return True

    def _apply_adjustment_values(
        self,
        adjustments: Iterable[tuple[ParameterKey, ParameterAdjustment]],
    ) -> tuple[ParameterKey, ...]:
        """GUI value/ownership/MIDI だけを一 revision で適用する。"""

        entries = tuple(adjustments)
        if any(type(key) is not ParameterKey for key, _adjustment in entries):
            raise TypeError("adjustment keys must be ParameterKey values")
        if any(
            type(adjustment) is not ParameterAdjustment
            for _key, adjustment in entries
        ):
            raise TypeError("adjustments must be ParameterAdjustment values")

        changed: list[ParameterKey] = []
        for key, adjustment in entries:
            current_state = self._states.get(key)
            current_meta = self._meta.get(key)
            if (
                current_state is None
                or current_meta is None
                or current_meta.kind != adjustment.meta.kind
            ):
                continue
            saved_state = adjustment.state
            if (
                current_state.override == saved_state.override
                and current_state.ui_value == saved_state.ui_value
                and current_state.cc_key == saved_state.cc_key
            ):
                continue
            self._observe_history_key_before(key)
            current_state.override = saved_state.override
            current_state.ui_value = saved_state.ui_value
            current_state.cc_key = saved_state.cc_key
            changed.append(key)
        if changed:
            self._touch(structure=False, value_keys=changed)
        return tuple(changed)

    def _load_persisted_parameters(
        self,
        *,
        states: Mapping[ParameterKey, ParamStateSnapshot],
        meta: Mapping[ParameterKey, ParamMeta],
        explicit_by_key: Mapping[ParameterKey, bool],
        preserve_explicit_overrides: bool,
    ) -> None:
        """Codec parser の canonical parameter records を新しい store へ格納する。"""

        self._meta.update(meta)
        self._states.update(
            {
                key: ParamState(
                    override=(
                        state.override
                        if preserve_explicit_overrides
                        or not explicit_by_key[key]
                        else False
                    ),
                    ui_value=state.ui_value,
                    cc_key=state.cc_key,
                )
                for key, state in states.items()
                if key in meta
            }
        )
        self._explicit_by_key.update(explicit_by_key)
        self._runtime.loaded_groups = {
            (key.op, key.site_id) for key in self._states
        }

    def _get_state_ref(self, key: ParameterKey) -> ParamState | None:
        return self._states.get(key)

    def _ensure_state(
        self,
        key: ParameterKey,
        *,
        base_value: Any,
        explicit: bool,
        initial_override: bool | None = None,
    ) -> ParamState:
        """ParamState を確保し、無ければ base_value で初期化して返す。"""

        if type(explicit) is not bool:
            raise TypeError("explicit must be an exact bool")
        if initial_override is not None and type(initial_override) is not bool:
            raise TypeError("initial_override must be an exact bool or None")
        state = self._states.get(key)
        if state is not None:
            return state

        self._observe_history_key_before(key)
        state = ParamState(ui_value=base_value)
        if initial_override is not None:
            state.override = initial_override
        self._states[key] = state
        self._explicit_by_key[key] = explicit
        self._touch()
        return state

    def _set_meta(self, key: ParameterKey, meta: ParamMeta) -> None:
        if self._meta.get(key) == meta:
            return
        self._observe_history_key_before(key)
        self._meta[key] = meta
        self._touch()

    def _get_explicit_ref(self, key: ParameterKey) -> bool | None:
        return self._explicit_by_key.get(key)

    def _set_explicit(self, key: ParameterKey, value: bool) -> None:
        if type(value) is not bool:
            raise TypeError("explicit must be an exact bool")
        if self._explicit_by_key.get(key) == value:
            return
        self._explicit_by_key[key] = value
        self._touch()

    def _labels_ref(self) -> ParamLabels:
        return self._labels

    def _ordinals_ref(self) -> GroupOrdinals:
        return self._ordinals

    def _effects_ref(self) -> EffectChainIndex:
        return self._effects

    def _collapsed_headers_ref(self) -> set[CollapsedHeaderKey]:
        return self._collapsed_headers

    def _locked_keys_ref(self) -> set[ParameterKey]:
        return self._locked_keys

    def _favorite_keys_ref(self) -> MutableSet[ParameterKey]:
        # self 参照を永続属性に置くと exact-store deepcopy/restore の所有権が
        # 壊れるため、mutation 境界でだけ lightweight view を作る。
        return _FavoriteKeySet(self)

    def _favorite_keys_snapshot(self) -> frozenset[ParameterKey]:
        """revision 内で同一 identity の immutable favorite 集合を返す。"""

        if self._favorite_snapshot_revision != self._favorite_revision:
            snapshot = frozenset(self._favorite_keys_data)
            self._favorite_snapshot = snapshot
            self._favorite_tuple = tuple(
                sorted(
                    snapshot,
                    key=lambda key: (key.op, key.site_id, key.arg),
                )
            )
            self._favorite_snapshot_revision = self._favorite_revision
        return self._favorite_snapshot

    def _favorite_keys_tuple(self) -> tuple[ParameterKey, ...]:
        self._favorite_keys_snapshot()
        return self._favorite_tuple

    def _variations_ref(self) -> dict[str, Variation]:
        return self._variations

    def _runtime_ref(self) -> ParamStoreRuntime:
        return self._runtime

    def _touch(
        self,
        *,
        structure: bool = True,
        value_keys: Iterable[ParameterKey] = (),
    ) -> None:
        """永続 revision と用途別 revision を一度に更新する。

        ``structure=False`` は、既存行の値だけが変わる hot path でのみ使う。
        呼び出し側が指定を忘れた場合は静的モデルを再構築する安全側へ倒す。
        """

        changed_keys = tuple(dict.fromkeys(value_keys))
        pending = self._pending_mutation
        if pending is not None:
            pending.touched = True
            pending.structure = pending.structure or bool(structure)
            pending.value_keys.extend(changed_keys)
            return
        self._commit_mutation(
            touched=True,
            structure=bool(structure),
            value_keys=changed_keys,
            favorites=False,
        )

    def _commit_mutation(
        self,
        *,
        touched: bool,
        structure: bool,
        value_keys: Iterable[ParameterKey],
        favorites: bool,
    ) -> None:
        """集約済み mutation を revision/cache へ一度だけ反映する。"""

        changed_keys = tuple(dict.fromkeys(value_keys))
        if not touched and not favorites:
            return
        self._revision += 1
        if favorites:
            self._favorite_revision += 1
            self._favorite_snapshot_revision = -1

        if structure:
            self._table_revision += 1
            # 構造変更には style parameter の追加・削除や復元も含まれる。
            # 呼び出し側が値 key を列挙できない bulk 経路でも、保持中 scene の
            # style overlay を取りこぼさないよう安全側へ倒す。
            self._style_revision += 1
        if changed_keys:
            self._value_revision += 1
            if not structure and any(
                key.op in {"__style__", "__layer_style__"}
                for key in changed_keys
            ):
                self._style_revision += 1
            self._value_change_log.append((self._value_revision, changed_keys))
        if structure:
            # label/meta/ordinal/entry cardinality が変わり得るため、value patch の
            # base としても使わない。value-only 変更では immutable な旧 snapshot
            # を次回差分構築の seed として保持する。
            self._snapshot_cache = None
            self._snapshot_cache_revision = -1
            self._snapshot_cache_value_revision = -1
        elif (
            not changed_keys
            and self._snapshot_cache is not None
            and self._snapshot_cache_value_revision == self._value_revision
        ):
            # collapse state など ParamSnapshot に含まれない変更では、同じ
            # immutable mapping をそのまま現 revision の cache として扱える。
            self._snapshot_cache_revision = self._revision

    def _touch_favorites(self) -> None:
        """favorite overlay と永続保存だけを無効化する。"""

        pending = self._pending_mutation
        if pending is not None:
            pending.favorites = True
            self._favorite_snapshot_revision = -1
            return
        self._commit_mutation(
            touched=False,
            structure=False,
            value_keys=(),
            favorites=True,
        )

    def _begin_mutation_batch(self, owner: object) -> None:
        """core command 用の revision 集約を開始する。"""

        if self._pending_mutation is not None:
            raise RuntimeError("ParamStore mutation batch is already active")
        self._pending_mutation = _PendingStoreMutation(owner=owner)

    def _end_mutation_batch(self, owner: object) -> None:
        """core command の mutation を一度の revision 更新として確定する。"""

        pending = self._pending_mutation
        if pending is None or pending.owner is not owner:
            raise RuntimeError("ParamStore mutation batch owner does not match")
        self._pending_mutation = None
        self._commit_mutation(
            touched=pending.touched,
            structure=pending.structure,
            value_keys=pending.value_keys,
            favorites=pending.favorites,
        )

    def value_changes_since(
        self,
        revision: int,
    ) -> frozenset[ParameterKey] | None:
        """指定 value revision 以降の key を返す。log 欠落時は ``None``。"""

        since = int(revision)
        if since == self._value_revision:
            return frozenset()
        if since < 0 or since > self._value_revision:
            return None
        if not self._value_change_log:
            return None
        first_revision = self._value_change_log[0][0]
        if since < first_revision - 1:
            return None
        changed: set[ParameterKey] = set()
        for change_revision, keys in reversed(self._value_change_log):
            if change_revision <= since:
                break
            changed.update(keys)
        return frozenset(changed)

    def _capture_transient_state(self) -> _TransientParamStoreState:
        """observer/cache を除く論理状態と counter の独立 copy を返す。"""

        (
            states,
            meta,
            explicit_by_key,
            labels,
            ordinals,
            effects,
            collapsed_headers,
            locked_keys,
            favorite_keys,
            variations,
            runtime,
            value_change_log,
        ) = deepcopy(
            (
                self._states,
                self._meta,
                self._explicit_by_key,
                self._labels,
                self._ordinals,
                self._effects,
                self._collapsed_headers,
                self._locked_keys,
                self._favorite_keys_data,
                self._variations,
                self._runtime,
                self._value_change_log,
            )
        )
        return _TransientParamStoreState(
            states=states,
            meta=meta,
            explicit_by_key=explicit_by_key,
            labels=labels,
            ordinals=ordinals,
            effects=effects,
            collapsed_headers=collapsed_headers,
            locked_keys=locked_keys,
            favorite_keys=favorite_keys,
            variations=variations,
            runtime=runtime,
            revision=self._revision,
            table_revision=self._table_revision,
            value_revision=self._value_revision,
            style_revision=self._style_revision,
            favorite_revision=self._favorite_revision,
            value_change_log=value_change_log,
        )

    def _begin_transient_rollback(self, rollback: ParamStoreRollback) -> None:
        """rollback の owner/nesting を検証し、active scope として登録する。"""

        if rollback._store is not self:
            raise ValueError("rollback belongs to a different ParamStore")
        if self._history_transaction_owner is not None:
            raise RuntimeError(
                "cannot begin transient rollback during a history transaction"
            )
        if (
            self._history_key_observer is not None
            or self._history_headers_observer is not None
        ):
            raise RuntimeError(
                "cannot begin transient rollback during a history transaction"
            )
        if self._active_transient_rollback is not None:
            raise RuntimeError("transient rollback is already active")
        self._active_transient_rollback = rollback

    def _restore_transient_rollback(self, rollback: ParamStoreRollback) -> None:
        """owner の active rollback が保持する論理状態を直接復元する。"""

        if type(rollback) is not ParamStoreRollback:
            raise TypeError("rollback must be a ParamStoreRollback")
        if rollback._store is not self:
            raise ValueError("rollback belongs to a different ParamStore")
        if self._active_transient_rollback is not rollback or not rollback._active:
            raise RuntimeError("rollback is not active for this ParamStore")
        state = rollback._state
        if state is None:
            raise RuntimeError("rollback has no captured state")

        self._states = state.states
        self._meta = state.meta
        self._explicit_by_key = state.explicit_by_key
        self._labels = state.labels
        self._ordinals = state.ordinals
        self._effects = state.effects
        self._collapsed_headers = state.collapsed_headers
        self._locked_keys = state.locked_keys
        self._favorite_keys_data = state.favorite_keys
        self._variations = state.variations
        self._runtime = state.runtime
        self._revision = state.revision
        self._table_revision = state.table_revision
        self._value_revision = state.value_revision
        self._style_revision = state.style_revision
        self._favorite_revision = state.favorite_revision
        self._value_change_log = state.value_change_log

        # snapshot/favorite cache は scope 内で構築された値も開始前の値も再利用しない。
        # 復元した mutable state から次回 query 時に必ず再構築する。
        self._favorite_snapshot_revision = -1
        self._favorite_snapshot = frozenset()
        self._favorite_tuple = ()
        self._snapshot_cache_revision = -1
        self._snapshot_cache_value_revision = -1
        self._snapshot_cache_rebuilt_entries = 0
        self._snapshot_cache = None

    def _end_transient_rollback(self, rollback: ParamStoreRollback) -> None:
        """owner の active rollback marker を解除する。"""

        if rollback._store is not self:
            raise ValueError("rollback belongs to a different ParamStore")
        if self._active_transient_rollback is not rollback:
            raise RuntimeError("rollback is not active for this ParamStore")
        self._active_transient_rollback = None

    def _begin_history_transaction(self, owner: object) -> None:
        """history transaction owner を登録し、不正 nesting を拒否する。"""

        if self._active_transient_rollback is not None:
            raise RuntimeError(
                "cannot begin history transaction during a transient rollback"
            )
        if self._history_transaction_owner is not None:
            raise RuntimeError("history transaction is already active")
        self._history_transaction_owner = owner

    def _end_history_transaction(self, owner: object) -> None:
        """一致する history transaction owner の登録を解除する。"""

        if self._history_transaction_owner is not owner:
            raise RuntimeError("history transaction owner does not match")
        self._history_transaction_owner = None

    def _begin_history_patch_capture(
        self,
        *,
        observe_key: Callable[[ParameterKey], None],
        observe_headers: Callable[[frozenset[CollapsedHeaderKey] | None], None],
    ) -> None:
        """単一 GUI transaction の変更前値 observer を登録する。"""

        if self._history_key_observer is not None:
            raise RuntimeError("history patch capture is already active")
        self._history_key_observer = observe_key
        self._history_headers_observer = observe_headers

    def _end_history_patch_capture(self) -> None:
        """現在の GUI transaction observer を解除する。"""

        self._history_key_observer = None
        self._history_headers_observer = None

    def _observe_history_key_before(self, key: ParameterKey) -> None:
        observer = self._history_key_observer
        if observer is not None:
            observer(key)

    def _observe_history_headers_before(
        self,
        headers: frozenset[CollapsedHeaderKey] | None = None,
    ) -> None:
        observer = self._history_headers_observer
        if observer is not None:
            observer(headers)

    def _get_snapshot_cache(self) -> object | None:
        if self._snapshot_cache_revision != self._revision:
            return None
        return self._snapshot_cache

    def _get_snapshot_cache_seed(self) -> tuple[object, int] | None:
        """structure change 以降の immutable snapshot と value revision を返す。"""

        snapshot = self._snapshot_cache
        if snapshot is None or self._snapshot_cache_value_revision < 0:
            return None
        return snapshot, self._snapshot_cache_value_revision

    def _set_snapshot_cache(
        self,
        snapshot: object,
        *,
        rebuilt_entries: int,
    ) -> None:
        self._snapshot_cache = snapshot
        self._snapshot_cache_revision = self._revision
        self._snapshot_cache_value_revision = self._value_revision
        self._snapshot_cache_rebuilt_entries = int(rebuilt_entries)


__all__ = ["ParamStore", "ParamStoreRollback"]
