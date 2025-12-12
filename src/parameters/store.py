# どこで: `src/parameters/store.py`。
# 何を: ParamStore を実装し、ParamState と ParamMeta の永続管理および ordinal 割り当てを行う。
# なぜ: GUI 用の既定状態とメタ情報を保持し、フレーム単位のバッファを統合するため。

from __future__ import annotations

import json
from typing import Any, Iterable

from .frame_params import FrameParamRecord
from .key import ParameterKey
from .meta import ParamMeta
from .state import ParamState

MAX_LABEL_LENGTH = 64


class ParamStore:
    """ParameterKey -> ParamState を保持する永続ストア。"""

    def __init__(self) -> None:
        self._states: dict[ParameterKey, ParamState] = {}
        self._meta: dict[ParameterKey, ParamMeta] = {}
        self._labels: dict[tuple[str, str], str] = {}
        # op ごとの site_id -> ordinal
        self._ordinals: dict[str, dict[str, int]] = {}

    def get_state(self, key: ParameterKey) -> ParamState | None:
        """登録済みの ParamState を返す。未登録なら None。"""
        return self._states.get(key)

    def ensure_state(self, key: ParameterKey, *, base_value: Any) -> ParamState:
        """ParamState を確保し、無ければ base_value で初期化して返す。"""
        state = self._states.get(key)
        if state is None:
            state = ParamState(
                override=False,
                ui_value=base_value,
                cc_key=None,
            )
            self._states[key] = state
            self._assign_ordinal(key.op, key.site_id)
        return state

    def _assign_ordinal(self, op: str, site_id: str) -> int:
        """op 単位で初出順に ordinal を付与する内部関数。"""
        mapping = self._ordinals.setdefault(op, {})
        if site_id in mapping:
            return mapping[site_id]
        ordinal = len(mapping) + 1
        mapping[site_id] = ordinal
        return ordinal

    def get_ordinal(self, op: str, site_id: str) -> int:
        """既存 ordinal を返し、未登録なら採番して返す。"""
        mapping = self._ordinals.get(op, {})
        return mapping.get(site_id, self._assign_ordinal(op, site_id))

    def snapshot(
        self,
    ) -> dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]]:
        """(key -> (meta, state, ordinal, label)) のスナップショットを返す。"""

        result: dict[ParameterKey, tuple[ParamMeta, ParamState, int, str | None]] = {}
        for key, state in self._states.items():
            meta = self._meta.get(key)
            if meta is None:
                # meta を持たないキーはスナップショットに含めない（実質的に GUI 対象外）
                continue
            label = self._labels.get((key.op, key.site_id))
            # ParamState はミュータブルなのでコピーを返す
            state_copy = ParamState(**vars(state))
            result[key] = (
                meta,
                state_copy,
                self.get_ordinal(key.op, key.site_id),
                label,
            )
        return result

    def store_frame_params(self, records: list[FrameParamRecord]) -> None:
        """フレーム内で観測したレコードをストアに保存し、meta を最新化する。"""
        for rec in records:
            # 新規なら生成、既存は保持
            self.ensure_state(
                rec.key,
                base_value=rec.base,
            )
            # 最新 meta を保存（破壊的に置き換え）
            self._meta[rec.key] = rec.meta
            # cc/ui_value/override はここでは変更しない

    def to_json(self) -> str:
        """状態・メタ・ordinal を JSON 文字列として保存する。"""
        data = {
            "states": [
                {
                    "op": k.op,
                    "site_id": k.site_id,
                    "arg": k.arg,
                    "override": v.override,
                    "ui_value": v.ui_value,
                    "cc_key": v.cc_key,
                }
                for k, v in self._states.items()
            ],
            "meta": [
                {
                    "op": k.op,
                    "site_id": k.site_id,
                    "arg": k.arg,
                    "kind": m.kind,
                    "ui_min": m.ui_min,
                    "ui_max": m.ui_max,
                    "choices": list(m.choices) if m.choices is not None else None,
                }
                for k, m in self._meta.items()
            ],
            "labels": [
                {"op": op, "site_id": site_id, "label": label}
                for (op, site_id), label in self._labels.items()
            ],
            "ordinals": self._ordinals,
        }
        return json.dumps(data)

    @classmethod
    def from_json(cls, payload: str) -> "ParamStore":
        """to_json の出力からストアを復元する。"""
        obj = json.loads(payload)
        store = cls()
        for item in obj.get("states", []):
            key = ParameterKey(op=item["op"], site_id=item["site_id"], arg=item["arg"])
            state = ParamState(
                override=item.get("override", False),
                ui_value=item.get("ui_value"),
                cc_key=item.get("cc_key"),
            )
            store._states[key] = state

        for item in obj.get("meta", []):
            key = ParameterKey(op=item["op"], site_id=item["site_id"], arg=item["arg"])
            meta = ParamMeta(
                kind=item["kind"],
                ui_min=item.get("ui_min"),
                ui_max=item.get("ui_max"),
                choices=item.get("choices"),
            )
            store._meta[key] = meta
        for item in obj.get("labels", []):
            store._labels[(item["op"], item["site_id"])] = item["label"]
        store._ordinals = obj.get("ordinals", {})
        return store

    def ordinals(self) -> dict[str, dict[str, int]]:
        """op ごとの ordinal マップを返す。"""
        return self._ordinals

    def meta_items(self) -> Iterable[tuple[ParameterKey, ParamMeta]]:
        """(ParameterKey, ParamMeta) のイテレータを返す。"""
        return self._meta.items()

    def set_label(self, op: str, site_id: str, label: str) -> None:
        """ヘッダ表示用のラベルを設定（上書き可）。"""
        self._labels[(op, site_id)] = self._trim_label(label)

    def get_label(self, op: str, site_id: str) -> str | None:
        return self._labels.get((op, site_id))

    @staticmethod
    def _trim_label(label: str) -> str:
        if len(label) <= MAX_LABEL_LENGTH:
            return label
        return label[:MAX_LABEL_LENGTH]
