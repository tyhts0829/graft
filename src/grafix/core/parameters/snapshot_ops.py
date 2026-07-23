# どこで: `src/grafix/core/parameters/snapshot_ops.py`。
# 何を: ParamStore の “pure snapshot”（副作用なし）生成を提供する。
# なぜ: 「読むつもりが書く」を排除し、不変条件の管理を ops に寄せるため。

from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import TypeAlias

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamStateSnapshot
from .store import ParamStore

ParamSnapshotEntry: TypeAlias = tuple[ParamMeta, ParamStateSnapshot, int, str | None]
ParamSnapshot: TypeAlias = Mapping[ParameterKey, ParamSnapshotEntry]

_MAX_SNAPSHOT_PATCH_ENTRIES = 64
_MISSING = object()


class _SnapshotOverlay(Mapping[ParameterKey, ParamSnapshotEntry]):
    """full base と小さな差分だけを所有する immutable snapshot。"""

    __slots__ = ("_base", "_overrides")

    def __init__(
        self,
        base: Mapping[ParameterKey, ParamSnapshotEntry],
        overrides: dict[ParameterKey, ParamSnapshotEntry],
    ) -> None:
        self._base = base
        self._overrides = MappingProxyType(overrides)

    def __getitem__(self, key: ParameterKey) -> ParamSnapshotEntry:
        entry = self._overrides.get(key, _MISSING)
        if entry is _MISSING:
            return self._base[key]
        return entry  # type: ignore[return-value]

    def __iter__(self) -> Iterator[ParameterKey]:
        return iter(self._base)

    def __len__(self) -> int:
        return len(self._base)

    @property
    def patch_entries(self) -> int:
        """現在の full base に対する差分 entry 数。"""

        return len(self._overrides)


def store_snapshot(
    store: ParamStore,
) -> ParamSnapshot:
    """(key -> (meta, state, ordinal, label)) のスナップショットを返す（副作用なし）。"""

    cached = store._get_snapshot_cache()
    if cached is not None:
        return cached  # type: ignore[return-value]

    seed = store._get_snapshot_cache_seed()
    if seed is not None:
        cached_snapshot, cached_value_revision = seed
        changed_keys = store.value_changes_since(cached_value_revision)
        if changed_keys is not None:
            incremental = _snapshot_with_value_changes(
                store,
                cached_snapshot,  # type: ignore[arg-type]
                changed_keys,
            )
            if incremental is not None:
                store._set_snapshot_cache(
                    incremental,
                    rebuilt_entries=len(changed_keys),
                )
                return incremental

    snapshot = _full_snapshot(store)
    store._set_snapshot_cache(snapshot, rebuilt_entries=len(snapshot))
    return snapshot


def _full_snapshot(store: ParamStore) -> ParamSnapshot:
    """store 全体から独立した immutable mapping を構築する。"""

    return MappingProxyType(store._read().snapshot_rows())


def _snapshot_with_value_changes(
    store: ParamStore,
    cached: ParamSnapshot,
    changed_keys: frozenset[ParameterKey],
) -> ParamSnapshot | None:
    """value-only change を full base 上の bounded overlay へ反映する。"""

    if not changed_keys:
        return cached

    if isinstance(cached, _SnapshotOverlay):
        base = cached._base
        overrides = dict(cached._overrides)
    else:
        base = cached
        overrides = {}

    for key in changed_keys:
        # value-only log の key は既存 snapshot entry である必要がある。
        # 不変条件が崩れていれば差分化せず、安全な full rebuild へ戻す。
        if key not in base:
            return None
        entry = _snapshot_entry(store, key)
        if entry is None:
            return None
        if entry == base[key]:
            overrides.pop(key, None)
        else:
            overrides[key] = entry

    if not overrides:
        # revision が進んだ snapshot は、値が偶然 base と一致しても旧 frame と
        # identity を共有しない。既存の cache invalidation contract を維持する。
        return _SnapshotOverlay(base, {})
    if len(overrides) > _MAX_SNAPSHOT_PATCH_ENTRIES:
        materialized = dict(base)
        materialized.update(overrides)
        return MappingProxyType(materialized)
    return _SnapshotOverlay(base, overrides)


def _snapshot_entry(
    store: ParamStore,
    key: ParameterKey,
) -> ParamSnapshotEntry | None:
    """既存 key 1 件だけを snapshot entry へ固定する。"""

    return store._read().snapshot_row(key)


def store_snapshot_for_gui(
    store: ParamStore,
) -> ParamSnapshot:
    """Parameter GUI 表示用のスナップショットを返す（副作用なし）。"""

    snapshot = store_snapshot(store)
    loaded_groups, observed_groups = store._read().visible_groups()
    if not loaded_groups:
        return snapshot

    from .style import STYLE_OP

    loaded_targets = {
        (op, site_id) for op, site_id in loaded_groups if op not in {STYLE_OP}
    }
    observed_targets = {
        (op, site_id) for op, site_id in observed_groups if op not in {STYLE_OP}
    }

    hide_groups = loaded_targets - observed_targets
    if not hide_groups:
        return snapshot

    return MappingProxyType(
        {key: value for key, value in snapshot.items() if (key.op, key.site_id) not in hide_groups}
    )


def materialize_snapshot(snapshot: ParamSnapshot) -> dict[ParameterKey, ParamSnapshotEntry]:
    """worker/serialization 用の plain dict を overlay-aware に構築する。"""

    if isinstance(snapshot, _SnapshotOverlay):
        materialized = dict(snapshot._base)
        materialized.update(snapshot._overrides)
        return materialized
    return dict(snapshot)


__all__ = [
    "materialize_snapshot",
    "store_snapshot",
    "store_snapshot_for_gui",
]
