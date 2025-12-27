# どこで: `src/grafix/core/parameters/prune_ops.py`。
# 何を: 実行時に観測されなかったロード済みグループを ParamStore から削除する。
# なぜ: GUI のヘッダ増殖と永続化ファイル肥大化を防ぐため。

from __future__ import annotations

from collections.abc import Iterable

from .reconcile_ops import GroupKey, reconcile_loaded_groups_for_runtime
from .store import ParamStore


def prune_stale_loaded_groups(store: ParamStore) -> None:
    """実行終了時に、今回の実行で観測されなかったロード済みグループを削除する。"""

    runtime = store._runtime_ref()
    if not runtime.loaded_groups:
        return

    # 保存直前にもう一度だけ再リンクを試みる（最後まで観測した集合で最善を尽くす）。
    reconcile_loaded_groups_for_runtime(store)

    from .style import STYLE_OP

    loaded_targets = {
        (op, site_id) for op, site_id in runtime.loaded_groups if op not in {STYLE_OP}
    }
    observed_targets = {
        (op, site_id) for op, site_id in runtime.observed_groups if op not in {STYLE_OP}
    }

    stale = loaded_targets - observed_targets
    prune_groups(store, stale)


def prune_groups(store: ParamStore, groups_to_remove: Iterable[GroupKey]) -> None:
    """指定された (op, site_id) グループをストアから削除する。"""

    groups = {(str(op), str(site_id)) for op, site_id in groups_to_remove}
    if not groups:
        return

    runtime = store._runtime_ref()
    labels = store._labels_ref()
    ordinals = store._ordinals_ref()
    effects = store._effects_ref()
    collapsed = store._collapsed_headers_ref()
    chain_ids_before = set(effects.chain_ordinals().keys())

    affected_ops: set[str] = set()

    for key in list(store._states.keys()):
        if (str(key.op), str(key.site_id)) in groups:
            del store._states[key]
    for key in list(store._meta.keys()):
        if (str(key.op), str(key.site_id)) in groups:
            del store._meta[key]
    for key in list(store._explicit_by_key.keys()):
        if (str(key.op), str(key.site_id)) in groups:
            del store._explicit_by_key[key]

    for op, site_id in groups:
        labels.delete(op, site_id)

    for op, site_id in groups:
        affected_ops.add(str(op))
        ordinals.delete(op, site_id)
        effects.delete_step(op, site_id)
        collapsed.discard(f"primitive:{op}:{site_id}")
        runtime.loaded_groups.discard((str(op), str(site_id)))
        runtime.observed_groups.discard((str(op), str(site_id)))

    for op in affected_ops:
        ordinals.compact(op)

    effects.prune_unused_chains()
    chain_ids_after = set(effects.chain_ordinals().keys())
    for removed_chain_id in chain_ids_before - chain_ids_after:
        collapsed.discard(f"effect_chain:{removed_chain_id}")


__all__ = ["prune_stale_loaded_groups", "prune_groups"]
