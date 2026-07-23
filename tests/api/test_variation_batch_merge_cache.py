from __future__ import annotations

from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.source import ValueSource
from grafix.core.parameters.store import ParamStore


def test_transient_rollback_invalidates_transient_merge_cache() -> None:
    store = ParamStore()
    key = ParameterKey(op="batch", site_id="main", arg="amount")
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=10.0)

    def merge(value: float, source: ValueSource) -> None:
        merge_frame_params(
            store,
            [
                FrameParamRecord(
                    key=key,
                    base=value,
                    meta=meta,
                    effective=value,
                    source=source,
                    explicit=True,
                )
            ],
        )

    merge(1.0, "code")
    original_runtime = store._read().runtime_token()

    with store.begin_transient_rollback():
        merge(2.0, "ui")
    restored_runtime = store._read().runtime_token()
    assert restored_runtime != original_runtime
    restored_state = store._read().runtime()
    assert restored_state.last_effective_by_key[key] == 1.0
    assert restored_state.last_source_by_key[key] == "code"
    restored_revision = restored_state.effective_revision

    # table revision は capture 時点へ巻き戻っていても、runtime identity が異なる
    # ため transient cache を再利用せず、復元後の runtime へ値を書き込む。
    merge(2.0, "ui")

    assert store._read().runtime_token() == restored_runtime
    restored_state = store._read().runtime()
    assert restored_state.last_effective_by_key[key] == 2.0
    assert restored_state.last_source_by_key[key] == "ui"
    assert restored_state.effective_revision == restored_revision + 1
