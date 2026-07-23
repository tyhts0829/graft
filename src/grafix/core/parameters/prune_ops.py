# どこで: `src/grafix/core/parameters/prune_ops.py`。
# 何を: 実行時に観測されなかったロード済みグループを ParamStore から削除する。
# なぜ: GUI のヘッダ増殖と永続化ファイル肥大化を防ぐため。

"""ParamStore から不要になった parameter グループ/引数を削除する操作群。

このモジュールは、`ParamStore` に溜まっていく「もう使われていない parameter」を
実行の節目で掃除（prune）するための関数を提供する。

対象は大きく 2 種類:

- グループ単位の削除: `(op, site_id)` をキーとする一連の parameter 群
  （例: ある primitive / effect が存在していたが、今回の実行では観測されなかった等）
- 引数単位の削除: application から渡された既知 schema に存在しない `arg` を持つ parameter 群

I/O・副作用
----------
- いずれの関数も `store` を破壊的に変更する（内部辞書や runtime state を削除する）。
- 永続化ファイルの書き換え自体はこのモジュールでは行わない。

読む順番（主要フロー）
----------------------
1. `prune_stale_loaded_groups`: 実行終了時に「ロードされたが観測されなかった」グループを削除
2. `prune_unknown_args_in_known_ops`: 固定済み schema snapshot と照合して未知 arg を削除
3. `prune_groups`: 実際の削除処理（labels/ordinals/effects/collapsed など関連状態も同期）
"""

from __future__ import annotations

from collections.abc import Iterable

from .collapsed_header import (
    effect_chain_collapsed_header_key,
    group_collapsed_header_keys,
)
from .identity import GroupKey
from .key import ParameterKey
from .known_operations import KnownOperationSchemaSnapshot
from .reconcile_ops import reconcile_loaded_groups_for_runtime
from .store import ParamStore


def prune_stale_loaded_groups(store: ParamStore) -> None:
    """実行終了時に「ロード済みだが観測されなかった」グループを削除する。

    Parameters
    ----------
    store : ParamStore
        対象の `ParamStore`。

    Notes
    -----
    - 「ロード済みグループ」は永続化ファイル等から読み込まれた parameter 群を指す。
    - 「観測されたグループ」は今回の実行中に実際に UI/実行系から参照された群を指す。
    - 両者の差分（loaded - observed）を「古い（stale）」とみなし、まとめて削除する。
    """

    read = store._read()
    runtime = read.runtime()
    effects = read.effects()
    if not runtime.loaded_groups and not effects.stale_loaded_chain_ids():
        # 今回の実行でロードされていないなら、比較対象がないので何もしない。
        return

    if runtime.loaded_groups:
        # 保存直前にもう一度だけ再リンクを試みる（最後まで観測した集合で最善を尽くす）。
        reconcile_loaded_groups_for_runtime(store)
        read = store._read()
        runtime = read.runtime()

        from .style import STYLE_OP

        # STYLE は "常に存在する/特別扱い" の前提で、stale 判定から除外する。
        # （STYLE を削除すると、UI 体験や既定スタイルに悪影響が出る可能性がある）
        loaded_targets = {
            (op, site_id) for op, site_id in runtime.loaded_groups if op not in {STYLE_OP}
        }
        observed_targets = {
            (op, site_id) for op, site_id in runtime.observed_groups if op not in {STYLE_OP}
        }

        prune_groups(
            store,
            loaded_targets - observed_targets,
            preserve_observed_effect_topology=True,
        )

    base_revision = store.revision
    read = store._read()
    effects = read.effects()
    stale_chain_ids = effects.prune_stale_loaded_chains()
    if stale_chain_ids:
        collapsed = set(read.collapsed_headers())
        for chain_id in stale_chain_ids:
            collapsed.discard(effect_chain_collapsed_header_key(chain_id))
        store._mutation().commit_effects(
            expected_revision=base_revision,
            effects=effects,
            collapsed_headers=collapsed,
        )


def prune_unknown_args_in_known_ops(
    store: ParamStore,
    known_operations: KnownOperationSchemaSnapshot,
) -> list[ParameterKey]:
    """既知 operation の schema にない引数をストアから削除する。

    Parameters
    ----------
    store : ParamStore
        対象の `ParamStore`。
    known_operations : KnownOperationSchemaSnapshot
        session が固定した既知 operation schema。

    Returns
    -------
    list[ParameterKey]
        削除したキーの一覧（元の `ParameterKey` を返す）。

    Notes
    -----
    - `op` が未登録（primitive/effect どちらでもない）のものは削除しない。
      （プラグイン未ロード等の可能性があるため）
    - 判定は渡された snapshot の argument 集合だけを基準にする。
    """

    if type(known_operations) is not KnownOperationSchemaSnapshot:
        raise TypeError(
            "known_operations は exact KnownOperationSchemaSnapshot である必要があります"
        )

    removed: list[ParameterKey] = []

    base_revision = store.revision
    read = store._read()
    states = read.states()
    meta_by_key = read.all_meta()
    explicit_by_key = read.all_explicit()
    favorites = set(read.favorite_keys())
    favorites_before = frozenset(favorites)
    locked = set(read.locked_keys())
    keys = (
        set(states)
        | set(meta_by_key)
        | set(explicit_by_key)
        | set(locked)
        | favorites
    )
    # 何が削除されたかをデバッグしやすいよう、順序を固定して走査する。
    for key in sorted(keys, key=lambda k: (k.op, k.site_id, k.arg)):
        op = key.op
        arg = key.arg

        known_args = known_operations.args_for(op)
        if known_args is None:
            # op が未登録なら、削除せず残す（プラグイン未ロード等の可能性がある）。
            continue
        if arg in known_args:
            continue

        # op は登録済みだが arg は未知、というケースなのでストアから消す。
        removed.append(key)
        states.pop(key, None)
        meta_by_key.pop(key, None)
        explicit_by_key.pop(key, None)
        locked.discard(key)
        favorites.discard(key)

    if removed:
        store._mutation().commit_parameter_prune(
            expected_revision=base_revision,
            states=states,
            meta=meta_by_key,
            explicit_by_key=explicit_by_key,
            locked_keys=locked,
            favorite_keys=favorites,
            favorites_changed=favorites != favorites_before,
        )
    return removed


def prune_groups(
    store: ParamStore,
    groups_to_remove: Iterable[GroupKey],
    *,
    preserve_observed_effect_topology: bool = False,
) -> None:
    """指定された `(op, site_id)` グループをストアから削除する。

    Parameters
    ----------
    store : ParamStore
        対象の `ParamStore`。
    groups_to_remove : Iterable[GroupKey]
        削除対象の `(op, site_id)` の反復可能。
    preserve_observed_effect_topology : bool, optional
        成功frameで観測済みのcode topologyはparameter groupと独立に保持する。

    Notes
    -----
    - parameter の実体（`_states` / `_meta` / `_explicit_by_key`）だけでなく、
      GUI/実行系が参照する周辺状態（labels/ordinals/effects/collapsed/runtime）も同期して削除する。
    """

    groups = set(groups_to_remove)
    if any(
        type(group) is not tuple
        or len(group) != 2
        or any(type(part) is not str or not part for part in group)
        for group in groups
    ):
        raise TypeError(
            "groups_to_remove must contain non-empty (op, site_id) tuple[str, str] values"
        )
    if not groups:
        return

    base_revision = store.revision
    read = store._read()
    states = read.states()
    meta_by_key = read.all_meta()
    explicit_by_key = read.all_explicit()
    runtime = read.runtime()
    labels = read.labels()
    ordinals = read.ordinals()
    effects = read.effects()
    collapsed = set(read.collapsed_headers())
    locked = set(read.locked_keys())
    favorites = set(read.favorite_keys())

    labels_before = labels.as_dict()
    ordinals_before = ordinals.as_dict()
    effect_topologies_before = effects.topologies()
    effect_chain_ordinals_before = effects.chain_ordinals()
    effect_orders_before = effects.order_overrides()
    collapsed_before = frozenset(collapsed)
    runtime_groups_before = (
        frozenset(runtime.loaded_groups),
        frozenset(runtime.observed_groups),
    )
    states_before = dict(states)
    meta_before = dict(meta_by_key)
    explicit_before = dict(explicit_by_key)
    locked_before = frozenset(locked)
    favorites_before = frozenset(favorites)

    persistent_groups = (
        {
            (key.op, key.site_id)
            for key in (
                set(states)
                | set(meta_by_key)
                | set(explicit_by_key)
                | locked
                | favorites
            )
        }
        | set(labels_before)
        | {
            (op, site_id)
            for op, by_site in ordinals_before.items()
            for site_id in by_site
        }
        | set(effects.step_info_by_site())
        | {
            (header.op, header.site_id)
            for header in collapsed
            if header.op is not None and header.site_id is not None
        }
    )
    runtime_groups = set(runtime.loaded_groups) | set(runtime.observed_groups)
    groups.intersection_update(persistent_groups | runtime_groups)
    if not groups:
        return
    persistent_targets = groups & persistent_groups
    effect_targets = persistent_targets & set(effects.step_info_by_site())
    chain_ids_before = set(effect_chain_ordinals_before)

    affected_ops: set[str] = set()

    # 走査中に dict を削除するため、keys() を list 化してから回す。
    for key in list(states):
        if (key.op, key.site_id) in persistent_targets:
            del states[key]
    for key in list(meta_by_key):
        if (key.op, key.site_id) in persistent_targets:
            del meta_by_key[key]
    for key in list(explicit_by_key):
        if (key.op, key.site_id) in persistent_targets:
            del explicit_by_key[key]
    for key in list(locked):
        if (key.op, key.site_id) in persistent_targets:
            locked.discard(key)
    for key in tuple(favorites):
        if (key.op, key.site_id) in persistent_targets:
            favorites.discard(key)

    # 表示ラベルは parameter の有無とは独立に残ってしまうので、グループ単位で明示削除する。
    for op, site_id in persistent_targets:
        labels.delete(op, site_id)

    # ordinals/effects/collapsed は「永続グループの存在」を前提にした情報なのでまとめて消す。
    for op, site_id in persistent_targets:
        if ordinals.get(op, site_id) is not None:
            affected_ops.add(op)
        ordinals.delete(op, site_id)
        effects.delete_step(
            op,
            site_id,
            preserve_observed_topology=preserve_observed_effect_topology,
        )
        collapsed.difference_update(group_collapsed_header_keys((op, site_id)))

    # runtime state は永続状態と独立に消す。runtime-only group ではこの差分だけを commit する。
    for op, site_id in groups:
        runtime.loaded_groups.discard((op, site_id))
        runtime.observed_groups.discard((op, site_id))

    # 同じ op の中で site_id が間引かれると ordinal に穴が空くので、op 単位で詰め直す。
    for op in affected_ops:
        ordinals.compact(op)

    # ステップ削除の結果、参照されなくなった effect chain を落とす。
    if effect_targets:
        effects.prune_unused_chains()
    chain_ids_after = set(effects.chain_ordinals().keys())
    # 消えた chain に対応する collapsed 状態も取り除き、UI 側にゴミが残らないようにする。
    for removed_chain_id in chain_ids_before - chain_ids_after:
        collapsed.discard(effect_chain_collapsed_header_key(removed_chain_id))

    persistent_changed = (
        states != states_before
        or meta_by_key != meta_before
        or explicit_by_key != explicit_before
        or labels.as_dict() != labels_before
        or ordinals.as_dict() != ordinals_before
        or effects.topologies() != effect_topologies_before
        or effects.chain_ordinals() != effect_chain_ordinals_before
        or effects.order_overrides() != effect_orders_before
        or frozenset(collapsed) != collapsed_before
        or frozenset(locked) != locked_before
        or frozenset(favorites) != favorites_before
    )
    runtime_changed = (
        frozenset(runtime.loaded_groups),
        frozenset(runtime.observed_groups),
    ) != runtime_groups_before

    if persistent_changed:
        store._mutation().commit_prune(
            expected_revision=base_revision,
            states=states,
            meta=meta_by_key,
            explicit_by_key=explicit_by_key,
            labels=labels,
            ordinals=ordinals,
            effects=effects,
            collapsed_headers=collapsed,
            locked_keys=locked,
            favorite_keys=favorites,
            runtime=runtime,
            favorites_changed=favorites != favorites_before,
        )
    elif runtime_changed:
        store._mutation().commit_runtime(
            expected_revision=base_revision,
            runtime=runtime,
        )


__all__ = ["prune_stale_loaded_groups", "prune_unknown_args_in_known_ops", "prune_groups"]
