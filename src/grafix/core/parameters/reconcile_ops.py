# どこで: `src/grafix/core/parameters/reconcile_ops.py`。
# 何を: loaded/observed の差分を再リンクし、グループの migrate を適用する。
# なぜ: site_id の揺れを吸収し、GUI の増殖と調整値の喪失を抑えるため。

from __future__ import annotations

from .key import ParameterKey
from .meta import ParamMeta
from .reconcile import build_group_fingerprints, match_groups
from .snapshot_ops import store_snapshot
from .store import ParamStore

GroupKey = tuple[str, str]  # (op, site_id)


def reconcile_loaded_groups_for_runtime(store: ParamStore) -> None:
    """ロード済みグループと観測済みグループの差分を再リンクする（削除はしない）。"""

    runtime = store._runtime_ref()
    if not runtime.loaded_groups or not runtime.observed_groups:
        return

    from .style import STYLE_OP

    loaded_targets = {
        (op, site_id) for op, site_id in runtime.loaded_groups if op not in {STYLE_OP}
    }
    observed_targets = {
        (op, site_id) for op, site_id in runtime.observed_groups if op not in {STYLE_OP}
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
        if pair in runtime.reconcile_applied:
            continue
        migrate_group(store, old_group, new_group)
        runtime.reconcile_applied.add(pair)


def migrate_group(store: ParamStore, old_group: GroupKey, new_group: GroupKey) -> None:
    """old_group の GUI 状態/メタを new_group へ可能な範囲で移す。"""

    old_op, old_site_id = old_group
    new_op, new_site_id = new_group
    if str(old_op) != str(new_op):
        raise ValueError(f"op mismatch: {old_group!r} -> {new_group!r}")
    op = str(old_op)

    labels = store._labels_ref()
    ordinals = store._ordinals_ref()

    old_label = labels.get(op, str(old_site_id))
    if old_label is not None and labels.get(op, str(new_site_id)) is None:
        labels.set(op, str(new_site_id), old_label)

    ordinals.migrate(op, str(old_site_id), str(new_site_id))

    collapsed = store._collapsed_headers_ref()
    old_collapse_key = f"primitive:{op}:{old_site_id}"
    if old_collapse_key in collapsed:
        collapsed.discard(old_collapse_key)
        collapsed.add(f"primitive:{op}:{new_site_id}")

    for old_key in _group_keys(store, op=op, site_id=str(old_site_id)):
        new_key = ParameterKey(op=op, site_id=str(new_site_id), arg=str(old_key.arg))
        old_meta = store._meta.get(old_key)
        new_meta = store._meta.get(new_key)
        if old_meta is None or new_meta is None:
            continue
        if old_meta.kind != new_meta.kind:
            continue

        old_state = store._states.get(old_key)
        new_state = store._states.get(new_key)
        if old_state is not None and new_state is not None:
            new_state.override = bool(old_state.override)
            new_state.ui_value = old_state.ui_value
            new_state.cc_key = old_state.cc_key

        old_explicit = store._explicit_by_key.get(old_key)
        if old_explicit is not None and new_key not in store._explicit_by_key:
            store._explicit_by_key[new_key] = bool(old_explicit)

        ui_min = old_meta.ui_min if old_meta.ui_min is not None else new_meta.ui_min
        ui_max = old_meta.ui_max if old_meta.ui_max is not None else new_meta.ui_max
        if ui_min != new_meta.ui_min or ui_max != new_meta.ui_max:
            store._meta[new_key] = ParamMeta(
                kind=str(new_meta.kind),
                ui_min=ui_min,
                ui_max=ui_max,
                choices=new_meta.choices,
            )


def _group_keys(store: ParamStore, *, op: str, site_id: str) -> list[ParameterKey]:
    keys: set[ParameterKey] = set()
    for key in store._states.keys():
        if str(key.op) == str(op) and str(key.site_id) == str(site_id):
            keys.add(key)
    for key in store._meta.keys():
        if str(key.op) == str(op) and str(key.site_id) == str(site_id):
            keys.add(key)
    return sorted(keys, key=lambda k: str(k.arg))


__all__ = ["GroupKey", "reconcile_loaded_groups_for_runtime", "migrate_group"]
