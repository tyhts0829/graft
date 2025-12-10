# どこで: `src/parameters/store.py`。
# 何を: ParamStore を実装し、ParamState の永続管理と ordinal 割り当てを行う。
# なぜ: GUI 用の既定状態を保持し、フレーム単位のバッファを統合するため。

from __future__ import annotations

import json
from typing import Any, Dict

from .frame_params import FrameParamRecord
from .key import ParameterKey
from .state import ParamState


class ParamStore:
    """ParameterKey -> ParamState を保持する永続ストア。"""

    def __init__(self) -> None:
        self._states: Dict[ParameterKey, ParamState] = {}
        # op ごとの site_id -> ordinal
        self._ordinals: Dict[str, Dict[str, int]] = {}

    def get_state(self, key: ParameterKey) -> ParamState | None:
        return self._states.get(key)

    def ensure_state(self, key: ParameterKey, *, base_value: Any, meta_ui_min: Any, meta_ui_max: Any) -> ParamState:
        state = self._states.get(key)
        if state is None:
            state = ParamState(
                override=False,
                ui_value=base_value,
                ui_min=meta_ui_min,
                ui_max=meta_ui_max,
                cc=None,
            )
            self._states[key] = state
            self._assign_ordinal(key.op, key.site_id)
        return state

    def _assign_ordinal(self, op: str, site_id: str) -> int:
        mapping = self._ordinals.setdefault(op, {})
        if site_id in mapping:
            return mapping[site_id]
        ordinal = len(mapping) + 1
        mapping[site_id] = ordinal
        return ordinal

    def get_ordinal(self, op: str, site_id: str) -> int:
        mapping = self._ordinals.get(op, {})
        return mapping.get(site_id, self._assign_ordinal(op, site_id))

    def snapshot(self) -> dict[ParameterKey, ParamState]:
        # 浅いコピーで十分（ParamState はミュータブルだが snapshot 内では読み取り専用で扱う）
        return {k: ParamState(**vars(v)) for k, v in self._states.items()}

    def merge_frame_params(self, records: list[FrameParamRecord]) -> None:
        for rec in records:
            # 新規なら生成、既存は保持
            state = self.ensure_state(
                rec.key,
                base_value=rec.base,
                meta_ui_min=rec.meta.ui_min,
                meta_ui_max=rec.meta.ui_max,
            )
            # meta 更新（ui_min/ui_max が指定されている場合のみ上書き）
            if rec.meta.ui_min is not None:
                state.ui_min = rec.meta.ui_min
            if rec.meta.ui_max is not None:
                state.ui_max = rec.meta.ui_max
            # cc/ui_value/override はここでは変更しない

    def to_json(self) -> str:
        data = {
            "states": [
                {
                    "op": k.op,
                    "site_id": k.site_id,
                    "arg": k.arg,
                    "override": v.override,
                    "ui_value": v.ui_value,
                    "ui_min": v.ui_min,
                    "ui_max": v.ui_max,
                    "cc": v.cc,
                }
                for k, v in self._states.items()
            ],
            "ordinals": self._ordinals,
        }
        return json.dumps(data)

    @classmethod
    def from_json(cls, payload: str) -> "ParamStore":
        obj = json.loads(payload)
        store = cls()
        for item in obj.get("states", []):
            key = ParameterKey(op=item["op"], site_id=item["site_id"], arg=item["arg"])
            state = ParamState(
                override=item.get("override", False),
                ui_value=item.get("ui_value"),
                ui_min=item.get("ui_min"),
                ui_max=item.get("ui_max"),
                cc=item.get("cc"),
            )
            store._states[key] = state
        store._ordinals = obj.get("ordinals", {})
        return store

    def ordinals(self) -> Dict[str, Dict[str, int]]:
        return self._ordinals
