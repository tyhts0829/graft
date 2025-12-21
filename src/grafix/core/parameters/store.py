# どこで: `src/grafix/core/parameters/store.py`。
# 何を: ParamStore（永続データの核）を定義する。
# なぜ: God-object 化を避け、周辺ロジック（ordinal/reconcile/永続化など）を別モジュールへ分離するため。

from __future__ import annotations

from typing import Any

from .effects import EffectChainIndex
from .key import ParameterKey
from .labels import ParamLabels
from .meta import ParamMeta
from .ordinals import GroupOrdinals
from .runtime import ParamStoreRuntime
from .state import ParamState


class ParamStore:
    """ParameterKey -> ParamState を保持する永続ストア。"""

    def __init__(self) -> None:
        self.states: dict[ParameterKey, ParamState] = {}
        self.meta: dict[ParameterKey, ParamMeta] = {}
        self.explicit_by_key: dict[ParameterKey, bool] = {}

        self.labels = ParamLabels()
        self.ordinals = GroupOrdinals()
        self.effects = EffectChainIndex()

        # 永続化しない実行時情報（loaded/observed/reconcile-applied）。
        self.runtime = ParamStoreRuntime()

    def get_state(self, key: ParameterKey) -> ParamState | None:
        """登録済みの ParamState を返す。未登録なら None。"""

        return self.states.get(key)

    def get_meta(self, key: ParameterKey) -> ParamMeta | None:
        """登録済みの ParamMeta を返す。未登録なら None。"""

        return self.meta.get(key)

    def set_meta(self, key: ParameterKey, meta: ParamMeta) -> None:
        """ParamMeta を上書き保存する。"""

        self.meta[key] = meta

    def ensure_state(
        self,
        key: ParameterKey,
        *,
        base_value: Any,
        initial_override: bool | None = None,
    ) -> ParamState:
        """ParamState を確保し、無ければ base_value で初期化して返す。"""

        state = self.states.get(key)
        if state is not None:
            return state

        state = ParamState(ui_value=base_value)
        if initial_override is not None:
            state.override = bool(initial_override)
        self.states[key] = state
        return state


__all__ = ["ParamStore"]

