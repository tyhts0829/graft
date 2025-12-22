# どこで: `src/grafix/core/parameters/invariants.py`。
# 何を: ParamStore の不変条件をテストで検証する関数を提供する。
# なぜ: ops 分割後も整合性の知識を 1 箇所へ固定し、踏み抜きを早期検知するため。

from __future__ import annotations

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

    for key, state in store._states.items():
        assert isinstance(key, ParameterKey)
        assert isinstance(state, ParamState)

    for key, meta in store._meta.items():
        assert isinstance(key, ParameterKey)
        assert isinstance(meta, ParamMeta)

    for key, value in store._explicit_by_key.items():
        assert isinstance(key, ParameterKey)
        assert isinstance(value, bool)

    labels = store._labels_ref().as_dict()
    for (op, site_id), label in labels.items():
        assert isinstance(op, str)
        assert isinstance(site_id, str)
        assert isinstance(label, str)
        assert len(label) <= MAX_LABEL_LENGTH

    ordinals_by_op = store._ordinals_ref().as_dict()
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

    effects = store._effects_ref()
    step_info_by_site = effects.step_info_by_site()
    chain_ordinal_by_id = effects.chain_ordinals()
    for (op, site_id), (chain_id, step_index) in step_info_by_site.items():
        assert isinstance(op, str)
        assert isinstance(site_id, str)
        assert isinstance(chain_id, str)
        assert isinstance(step_index, int)
        assert step_index >= 0
        assert chain_id in chain_ordinal_by_id
        assert store._ordinals_ref().get(op, site_id) is not None

    for chain_id, ordinal in chain_ordinal_by_id.items():
        assert isinstance(chain_id, str)
        assert isinstance(ordinal, int)
        assert int(ordinal) >= 1
    ordinals = [int(v) for v in chain_ordinal_by_id.values()]
    assert len(set(ordinals)) == len(ordinals)

    runtime = store._runtime_ref()
    for op, site_id in runtime.loaded_groups:
        assert isinstance(op, str)
        assert isinstance(site_id, str)
    for op, site_id in runtime.observed_groups:
        assert isinstance(op, str)
        assert isinstance(site_id, str)

    # snapshot は pure 前提（= 不足補完をしない）なので、ここで例外が出るのは不変条件違反。
    snapshot = store_snapshot(store)
    for _key, (_meta, state, _ordinal, _label) in snapshot.items():
        assert not isinstance(state.ui_value, (list, dict))


__all__ = ["assert_invariants"]
