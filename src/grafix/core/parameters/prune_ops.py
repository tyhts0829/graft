# どこで: `src/grafix/core/parameters/prune_ops.py`。
# 何を: 実行時に観測されなかったロード済みグループを ParamStore から削除する。
# なぜ: GUI のヘッダ増殖と永続化ファイル肥大化を防ぐため。

from __future__ import annotations

from collections.abc import Iterable

from grafix.core.effect_registry import effect_registry
from grafix.core.primitive_registry import primitive_registry

from .key import ParameterKey
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


def prune_unknown_args_in_known_ops(store: ParamStore) -> list[ParameterKey]:
    """登録済み primitive/effect の未登録引数（arg）をストアから削除する。

    Notes
    -----
    - `op` が未登録（primitive/effect どちらでもない）のものは削除しない。
      （プラグイン未ロード等の可能性があるため）
    - 判定は registry の meta keys を基準にする（`param_order` は並び専用）。
    """

    removed: list[ParameterKey] = []

    primitive_known_args_by_op: dict[str, set[str]] = {}
    effect_known_args_by_op: dict[str, set[str]] = {}

    keys = set(store._states) | set(store._meta) | set(store._explicit_by_key)
    for key in sorted(keys, key=lambda k: (str(k.op), str(k.site_id), str(k.arg))):
        op = str(key.op)
        arg = str(key.arg)

        if op in primitive_registry:
            known_args = primitive_known_args_by_op.get(op)
            if known_args is None:
                known_args = set(primitive_registry.get_meta(op).keys())
                primitive_known_args_by_op[op] = known_args
            if arg in known_args:
                continue

        elif op in effect_registry:
            known_args = effect_known_args_by_op.get(op)
            if known_args is None:
                known_args = set(effect_registry.get_meta(op).keys())
                effect_known_args_by_op[op] = known_args
            if arg in known_args:
                continue

        else:
            continue

        removed.append(key)
        store._states.pop(key, None)
        store._meta.pop(key, None)
        store._explicit_by_key.pop(key, None)

    return removed


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


__all__ = ["prune_stale_loaded_groups", "prune_unknown_args_in_known_ops", "prune_groups"]
