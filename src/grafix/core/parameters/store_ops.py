# どこで: `src/grafix/core/parameters/store_ops.py`。
# 何を: ParamStore への手続き（snapshot/merge/reconcile/prune）を提供する。
# なぜ: God-object 化を避け、仕様の置き場所を読みやすく分離するため。

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .frame_params import FrameParamRecord
from .key import ParameterKey
from .meta import ParamMeta
from .reconcile import build_group_fingerprints, match_groups
from .state import ParamState
from .store import ParamStore

GroupKey = tuple[str, str]  # (op, site_id)


def store_snapshot(
    store: ParamStore,
) -> dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]]:
    """(key -> (meta, state, ordinal, label)) のスナップショットを返す。"""

    result: dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]] = {}
    for key, state in store.states.items():
        meta = store.meta.get(key)
        if meta is None:
            # meta を持たないキーはスナップショットに含めない（実質的に GUI 対象外）
            continue
        label = store.labels.get(key.op, key.site_id)
        ordinal = store.ordinals.get_or_assign(key.op, key.site_id)
        state_copy = ParamState(**vars(state))
        result[key] = (meta, state_copy, ordinal, label)
    return result


def store_snapshot_for_gui(
    store: ParamStore,
) -> dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]]:
    """Parameter GUI 表示用のスナップショットを返す。

    Notes
    -----
    実行中に観測されなかった「ロード済みグループ」は GUI から隠す。
    （ストア内から削除するのは保存時の `prune_stale_loaded_groups()` が担う）
    """

    snapshot = store_snapshot(store)
    if not store.runtime.loaded_groups:
        return snapshot

    from .style import STYLE_OP

    loaded_targets = {
        (op, site_id)
        for op, site_id in store.runtime.loaded_groups
        if op not in {STYLE_OP}
    }
    observed_targets = {
        (op, site_id)
        for op, site_id in store.runtime.observed_groups
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


def merge_frame_params(store: ParamStore, records: list[FrameParamRecord]) -> None:
    """フレーム内で観測したレコードをストアに保存し、関連情報を更新する。"""

    explicit_by_key_this_frame: dict[ParameterKey, bool] = {}

    for rec in records:
        store.runtime.observed_groups.add((str(rec.key.op), str(rec.key.site_id)))
        explicit_by_key_this_frame[rec.key] = bool(rec.explicit)

        store.ordinals.get_or_assign(rec.key.op, rec.key.site_id)
        store.ensure_state(
            rec.key,
            base_value=rec.base,
            initial_override=(not bool(rec.explicit)),
        )

        # meta は初出時に確定し、以後は保持する（GUI 側で編集できるようにする）
        existing_meta = store.meta.get(rec.key)
        if existing_meta is None or existing_meta.kind != rec.meta.kind:
            store.meta[rec.key] = rec.meta

        if rec.chain_id is not None and rec.step_index is not None:
            store.effects.record_step(
                op=str(rec.key.op),
                site_id=str(rec.key.site_id),
                chain_id=str(rec.chain_id),
                step_index=int(rec.step_index),
            )

    _reconcile_loaded_groups_for_runtime(store)
    _apply_explicit_override_follow_policy(store, explicit_by_key_this_frame)


def prune_stale_loaded_groups(store: ParamStore) -> None:
    """実行終了時に、今回の実行で観測されなかったロード済みグループを削除する。"""

    if not store.runtime.loaded_groups:
        return

    # 保存直前にもう一度だけ再リンクを試みる（最後まで観測した集合で最善を尽くす）。
    _reconcile_loaded_groups_for_runtime(store)

    from .style import STYLE_OP

    loaded_targets = {
        (op, site_id)
        for op, site_id in store.runtime.loaded_groups
        if op not in {STYLE_OP}
    }
    observed_targets = {
        (op, site_id)
        for op, site_id in store.runtime.observed_groups
        if op not in {STYLE_OP}
    }
    stale = loaded_targets - observed_targets
    prune_groups(store, stale)


def prune_groups(store: ParamStore, groups_to_remove: Iterable[GroupKey]) -> None:
    """指定された (op, site_id) グループをストアから削除する。"""

    groups = {(str(op), str(site_id)) for op, site_id in groups_to_remove}
    if not groups:
        return

    affected_ops: set[str] = set()

    for key in list(store.states.keys()):
        if (str(key.op), str(key.site_id)) in groups:
            del store.states[key]
    for key in list(store.meta.keys()):
        if (str(key.op), str(key.site_id)) in groups:
            del store.meta[key]
    for key in list(store.explicit_by_key.keys()):
        if (str(key.op), str(key.site_id)) in groups:
            del store.explicit_by_key[key]

    for op, site_id in groups:
        store.labels.delete(op, site_id)

    for op, site_id in groups:
        affected_ops.add(str(op))
        store.ordinals.delete(op, site_id)
        store.effects.delete_step(op, site_id)

        store.runtime.loaded_groups.discard((str(op), str(site_id)))
        store.runtime.observed_groups.discard((str(op), str(site_id)))

    for op in affected_ops:
        store.ordinals.compact(op)

    store.effects.prune_unused_chains()


def migrate_group(store: ParamStore, old_group: GroupKey, new_group: GroupKey) -> None:
    """old_group の GUI 状態/メタを new_group へ可能な範囲で移す。"""

    old_op, old_site_id = old_group
    new_op, new_site_id = new_group
    if str(old_op) != str(new_op):
        raise ValueError(f"op mismatch: {old_group!r} -> {new_group!r}")
    op = str(old_op)

    old_label = store.labels.get(op, str(old_site_id))
    if old_label is not None and store.labels.get(op, str(new_site_id)) is None:
        store.labels.set(op, str(new_site_id), old_label)

    store.ordinals.migrate(op, str(old_site_id), str(new_site_id))

    for old_key in _group_keys(store, op=op, site_id=str(old_site_id)):
        new_key = ParameterKey(op=op, site_id=str(new_site_id), arg=str(old_key.arg))
        old_meta = store.meta.get(old_key)
        new_meta = store.meta.get(new_key)
        if old_meta is None or new_meta is None:
            continue
        if old_meta.kind != new_meta.kind:
            continue

        old_state = store.states.get(old_key)
        new_state = store.states.get(new_key)
        if old_state is not None and new_state is not None:
            new_state.override = bool(old_state.override)
            new_state.ui_value = old_state.ui_value
            new_state.cc_key = old_state.cc_key

        old_explicit = store.explicit_by_key.get(old_key)
        if old_explicit is not None and new_key not in store.explicit_by_key:
            store.explicit_by_key[new_key] = bool(old_explicit)

        ui_min = old_meta.ui_min if old_meta.ui_min is not None else new_meta.ui_min
        ui_max = old_meta.ui_max if old_meta.ui_max is not None else new_meta.ui_max
        if ui_min != new_meta.ui_min or ui_max != new_meta.ui_max:
            store.meta[new_key] = ParamMeta(
                kind=str(new_meta.kind),
                ui_min=ui_min,
                ui_max=ui_max,
                choices=new_meta.choices,
            )


def _group_keys(store: ParamStore, *, op: str, site_id: str) -> list[ParameterKey]:
    keys: set[ParameterKey] = set()
    for key in store.states.keys():
        if str(key.op) == str(op) and str(key.site_id) == str(site_id):
            keys.add(key)
    for key in store.meta.keys():
        if str(key.op) == str(op) and str(key.site_id) == str(site_id):
            keys.add(key)
    return sorted(keys, key=lambda k: str(k.arg))


def _apply_explicit_override_follow_policy(
    store: ParamStore, explicit_by_key_this_frame: Mapping[ParameterKey, bool]
) -> None:
    """explicit/implicit の変化に追従して override を条件付きで更新する。"""

    for key, new_explicit in explicit_by_key_this_frame.items():
        prev_explicit = store.explicit_by_key.get(key)
        new_explicit = bool(new_explicit)

        if prev_explicit is None:
            # 旧 JSON（explicit 情報なし）もあるので、unknown の場合は触らず記録だけ行う。
            store.explicit_by_key[key] = new_explicit
            continue

        prev_explicit = bool(prev_explicit)
        if prev_explicit == new_explicit:
            continue

        state = store.states.get(key)
        if state is None:
            store.explicit_by_key[key] = new_explicit
            continue

        default_override_prev = not prev_explicit
        default_override_new = not new_explicit

        # 既定値のままなら追従して切り替える。既にユーザーが切り替え済みなら触らない。
        if bool(state.override) == bool(default_override_prev):
            state.override = bool(default_override_new)

        store.explicit_by_key[key] = new_explicit


def _reconcile_loaded_groups_for_runtime(store: ParamStore) -> None:
    """ロード済みグループと観測済みグループの差分を再リンクする（削除はしない）。"""

    if not store.runtime.loaded_groups or not store.runtime.observed_groups:
        return

    from .style import STYLE_OP

    loaded_targets = {
        (op, site_id) for op, site_id in store.runtime.loaded_groups if op not in {STYLE_OP}
    }
    observed_targets = {
        (op, site_id) for op, site_id in store.runtime.observed_groups if op not in {STYLE_OP}
    }

    fresh = observed_targets - loaded_targets
    if not fresh:
        return

    stale = loaded_targets - observed_targets
    fresh_ops = {op for op, _site_id in fresh}
    stale_candidates = {g for g in stale if g[0] in fresh_ops}
    if not stale_candidates:
        return

    snapshot = store_snapshot(store)
    fingerprints = build_group_fingerprints(snapshot)
    mapping = match_groups(
        stale=sorted(stale_candidates),
        fresh=sorted(fresh),
        fingerprints=fingerprints,
    )
    for old_group, new_group in mapping.items():
        pair = (old_group, new_group)
        if pair in store.runtime.reconcile_applied:
            continue
        migrate_group(store, old_group, new_group)
        store.runtime.reconcile_applied.add(pair)


__all__ = [
    "store_snapshot",
    "store_snapshot_for_gui",
    "merge_frame_params",
    "prune_stale_loaded_groups",
    "prune_groups",
    "migrate_group",
]

