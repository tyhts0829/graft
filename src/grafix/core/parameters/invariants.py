# どこで: `src/grafix/core/parameters/invariants.py`。
# 何を: ParamStore の不変条件をテストで検証する関数を提供する。
# なぜ: ops 分割後も整合性の知識を 1 箇所へ固定し、踏み抜きを早期検知するため。

from __future__ import annotations

from .collapsed_header import CollapsedHeaderKey
from .key import ParameterKey
from .labels import MAX_LABEL_LENGTH
from .meta import ParamMeta
from .snapshot_ops import store_snapshot
from .state import ParamState
from .store import ParamStore


def assert_invariants(store: ParamStore) -> None:
    """ParamStore の不変条件を検査する。

    Notes
    -----
    テスト専用の検査関数。実行時に常時呼ぶことは想定しない。
    """

    read = store._read()
    states = read.states()
    meta_by_key = read.all_meta()
    explicit_by_key = read.all_explicit()

    for key, state in states.items():
        assert isinstance(key, ParameterKey)
        assert isinstance(state, ParamState)
        assert key in explicit_by_key

    for key, meta in meta_by_key.items():
        assert isinstance(key, ParameterKey)
        assert isinstance(meta, ParamMeta)

    for key, value in explicit_by_key.items():
        assert isinstance(key, ParameterKey)
        assert isinstance(value, bool)

    for key in read.locked_keys():
        assert isinstance(key, ParameterKey)
        assert key in states
        assert key in meta_by_key

    for key in read.favorite_keys():
        assert isinstance(key, ParameterKey)
        assert key in states
        assert key in meta_by_key

    for header in read.collapsed_headers():
        assert type(header) is CollapsedHeaderKey

    labels = read.label_items()
    for (op, site_id), label in labels.items():
        assert isinstance(op, str)
        assert isinstance(site_id, str)
        assert isinstance(label, str)
        assert len(label) <= MAX_LABEL_LENGTH

    ordinal_model = read.ordinals()
    ordinals_by_op = ordinal_model.as_dict()
    for op, mapping in ordinals_by_op.items():
        assert isinstance(op, str)
        assert isinstance(mapping, dict)
        for site_id, ordinal in mapping.items():
            assert isinstance(site_id, str)
            assert isinstance(ordinal, int)
            assert int(ordinal) >= 1
        if mapping:
            values = [int(v) for v in mapping.values()]
            assert set(values) == set(range(1, len(mapping) + 1))

    effects = read.effects()
    step_info_by_site = effects.step_info_by_site()
    chain_ordinal_by_id = effects.chain_ordinals()
    for (op, site_id), (chain_id, step_index) in step_info_by_site.items():
        assert isinstance(op, str)
        assert isinstance(site_id, str)
        assert isinstance(chain_id, str)
        assert isinstance(step_index, int)
        assert step_index >= 0
        assert chain_id in chain_ordinal_by_id
        has_parameter = any(
            key.op == op and key.site_id == site_id
            for key in set(states) | set(meta_by_key)
        )
        if has_parameter:
            assert ordinal_model.get(op, site_id) is not None

    for chain_id, ordinal in chain_ordinal_by_id.items():
        assert isinstance(chain_id, str)
        assert isinstance(ordinal, int)
        assert int(ordinal) >= 1
    ordinals = [int(v) for v in chain_ordinal_by_id.values()]
    assert len(set(ordinals)) == len(ordinals)

    for chain_id, topology in effects.topologies().items():
        assert isinstance(chain_id, str)
        assert chain_id
        assert chain_id in chain_ordinal_by_id
        assert [step.code_index for step in topology] == list(range(len(topology)))
        keys = [step.key for step in topology]
        if len(set(keys)) != len(keys):
            # shared site等でidentityが曖昧なchainは描画自体を許し、
            # GUI reorderだけを無効にする。
            assert chain_id not in effects.order_overrides()
        for step in topology:
            assert isinstance(step.op, str)
            assert step.op
            assert isinstance(step.site_id, str)
            assert step.site_id
            assert isinstance(step.n_inputs, int)
            assert step.n_inputs >= 1

    for chain_id, order in effects.order_overrides().items():
        # load直後はcode topology未観測でもoverrideを保持する契約なので、
        # 未知chainではJSONとしての局所不変条件だけを検査する。
        assert isinstance(chain_id, str)
        assert chain_id
        assert order
        assert len(set(order)) == len(order)
        for op, site_id in order:
            assert isinstance(op, str)
            assert op
            assert isinstance(site_id, str)
            assert site_id
        current_topology = effects.topology(chain_id)
        if current_topology is not None:
            code_keys = tuple(step.key for step in current_topology)
            assert len(order) == len(code_keys)
            assert set(order) == set(code_keys)
            assert effects.effective_order(chain_id) == order

    runtime = read.runtime()
    for op, site_id in runtime.loaded_groups:
        assert isinstance(op, str)
        assert isinstance(site_id, str)
    for op, site_id in runtime.observed_groups:
        assert isinstance(op, str)
        assert isinstance(site_id, str)
    for new_group, orphan in runtime.reconcile_orphans.items():
        assert new_group == orphan.new_group
        assert orphan.candidate_old_groups
        assert all(
            old_group[0] == new_group[0]
            for old_group in orphan.candidate_old_groups
        )
        assert all(
            old_group != new_group for old_group in orphan.candidate_old_groups
        )

    # snapshot は pure 前提（= 不足補完をしない）なので、ここで例外が出るのは不変条件違反。
    snapshot = store_snapshot(store)
    for _key, (_meta, snapshot_state, _ordinal, _label) in snapshot.items():
        assert not isinstance(snapshot_state.ui_value, (list, dict))


__all__ = ["assert_invariants"]
