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
    """ParameterKey -> ParamState を保持する永続ストア。

    Notes
    -----
    - このクラスは「永続データの入れ物」に寄せる。
    - 外部へはミュータブルな参照（ParamState）を渡さない。
      変更は ops 経由で行う想定とする。
    """

    def __init__(self) -> None:
        self._states: dict[ParameterKey, ParamState] = {}
        self._meta: dict[ParameterKey, ParamMeta] = {}
        self._explicit_by_key: dict[ParameterKey, bool] = {}

        self._labels = ParamLabels()
        self._ordinals = GroupOrdinals()
        self._effects = EffectChainIndex()
        self._collapsed_headers: set[str] = set()

        # 永続化しない実行時情報（loaded/observed/reconcile-applied）。
        self._runtime = ParamStoreRuntime()

    def get_state(self, key: ParameterKey) -> ParamState | None:
        """登録済みの ParamState を返す。未登録なら None。"""

        state = self._states.get(key)
        if state is None:
            return None
        return ParamState(**vars(state))

    def get_meta(self, key: ParameterKey) -> ParamMeta | None:
        """登録済みの ParamMeta を返す。未登録なら None。"""

        return self._meta.get(key)

    def get_label(self, op: str, site_id: str) -> str | None:
        """(op, site_id) のラベルを返す。未登録なら None。"""

        return self._labels.get(op, site_id)

    def get_ordinal(self, op: str, site_id: str) -> int | None:
        """(op, site_id) の ordinal を返す。未登録なら None。"""

        return self._ordinals.get(op, site_id)

    def get_effect_step(self, op: str, site_id: str) -> tuple[str, int] | None:
        """(op, site_id) の effect ステップ情報を返す。未登録なら None。"""

        return self._effects.get_step(op, site_id)

    def effect_steps(self) -> dict[tuple[str, str], tuple[str, int]]:
        """(op, site_id) -> (chain_id, step_index) のコピーを返す。"""

        return self._effects.step_info_by_site()

    def chain_ordinals(self) -> dict[str, int]:
        """chain_id -> ordinal のコピーを返す。"""

        return self._effects.chain_ordinals()

    # --- 内部 API（ops/codec からのみ利用する想定）---
    def _get_state_ref(self, key: ParameterKey) -> ParamState | None:
        return self._states.get(key)

    def _ensure_state(
        self,
        key: ParameterKey,
        *,
        base_value: Any,
        initial_override: bool | None = None,
    ) -> ParamState:
        """ParamState を確保し、無ければ base_value で初期化して返す。"""

        state = self._states.get(key)
        if state is not None:
            return state

        state = ParamState(ui_value=base_value)
        if initial_override is not None:
            state.override = bool(initial_override)
        self._states[key] = state
        return state

    def _get_meta_ref(self, key: ParameterKey) -> ParamMeta | None:
        return self._meta.get(key)

    def _set_meta(self, key: ParameterKey, meta: ParamMeta) -> None:
        self._meta[key] = meta

    def _get_explicit_ref(self, key: ParameterKey) -> bool | None:
        return self._explicit_by_key.get(key)

    def _set_explicit(self, key: ParameterKey, value: bool) -> None:
        self._explicit_by_key[key] = bool(value)

    def _labels_ref(self) -> ParamLabels:
        return self._labels

    def _ordinals_ref(self) -> GroupOrdinals:
        return self._ordinals

    def _effects_ref(self) -> EffectChainIndex:
        return self._effects

    def _collapsed_headers_ref(self) -> set[str]:
        return self._collapsed_headers

    def _runtime_ref(self) -> ParamStoreRuntime:
        return self._runtime


__all__ = ["ParamStore"]
