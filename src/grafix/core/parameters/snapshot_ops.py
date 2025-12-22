# どこで: `src/grafix/core/parameters/snapshot_ops.py`。
# 何を: ParamStore の “pure snapshot”（副作用なし）生成を提供する。
# なぜ: 「読むつもりが書く」を排除し、不変条件の管理を ops に寄せるため。

from __future__ import annotations

from typing import TypeAlias

from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState
from .store import ParamStore

ParamSnapshotEntry: TypeAlias = tuple[ParamMeta, ParamState, int, str | None]
ParamSnapshot: TypeAlias = dict[ParameterKey, ParamSnapshotEntry]


def store_snapshot(
    store: ParamStore,
) -> ParamSnapshot:
    """(key -> (meta, state, ordinal, label)) のスナップショットを返す（副作用なし）。"""

    labels = store._labels_ref()
    ordinals = store._ordinals_ref()

    result: ParamSnapshot = {}
    for key, state in store._states.items():
        meta = store._meta.get(key)
        if meta is None:
            # meta を持たないキーはスナップショットに含めない（実質的に GUI 対象外）
            continue

        ordinal = ordinals.get(key.op, key.site_id)
        if ordinal is None:
            raise RuntimeError(
                "ParamStore の不変条件違反: ordinal が未割り当ての group がある"
                f": op={key.op!r}, site_id={key.site_id!r}"
            )

        label = labels.get(key.op, key.site_id)
        state_copy = ParamState(**vars(state))
        result[key] = (meta, state_copy, int(ordinal), label)
    return result


def store_snapshot_for_gui(
    store: ParamStore,
) -> ParamSnapshot:
    """Parameter GUI 表示用のスナップショットを返す（副作用なし）。"""

    snapshot = store_snapshot(store)
    runtime = store._runtime_ref()
    if not runtime.loaded_groups:
        return snapshot

    from .style import STYLE_OP

    loaded_targets = {
        (op, site_id) for op, site_id in runtime.loaded_groups if op not in {STYLE_OP}
    }
    observed_targets = {
        (op, site_id)
        for op, site_id in runtime.observed_groups
        if op not in {STYLE_OP}
    }

    hide_groups = loaded_targets - observed_targets
    if not hide_groups:
        return snapshot

    return {
        key: value
        for key, value in snapshot.items()
        if (str(key.op), str(key.site_id)) not in hide_groups
    }


__all__ = ["store_snapshot", "store_snapshot_for_gui"]
