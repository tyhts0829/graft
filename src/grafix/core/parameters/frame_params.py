# どこで: `src/grafix/core/parameters/frame_params.py`。
# 何を: フレーム内で観測・解決したパラメータを貯めるバッファを定義する。
# なぜ: ParamStore へのマージをフレーム境界でまとめ、スレッド安全に扱うため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .key import ParameterKey
from .meta import ParamMeta


@dataclass
class FrameParamRecord:
    """1 引数ぶんの観測・解決結果。"""

    key: ParameterKey
    base: Any
    meta: ParamMeta
    effective: Any | None = None
    source: str | None = None  # "base" | "gui" | "cc"
    explicit: bool = True
    chain_id: str | None = None
    step_index: int | None = None


@dataclass
class FrameLabelRecord:
    """(op, site_id) に紐づくラベル設定の記録。"""

    op: str
    site_id: str
    label: str


class FrameParamsBuffer:
    """フレーム内のパラメータ観測を蓄積する単純なバッファ。"""

    def __init__(self) -> None:
        self._records: list[FrameParamRecord] = []
        self._labels: list[FrameLabelRecord] = []

    def record(
        self,
        *,
        key: ParameterKey,
        base: Any,
        meta: ParamMeta,
        effective: Any | None = None,
        source: str | None = None,
        explicit: bool = True,
        chain_id: str | None = None,
        step_index: int | None = None,
    ) -> None:
        self._records.append(
            FrameParamRecord(
                key=key,
                base=base,
                meta=meta,
                effective=effective,
                source=source,
                explicit=bool(explicit),
                chain_id=chain_id,
                step_index=step_index,
            )
        )

    def set_label(self, *, op: str, site_id: str, label: str) -> None:
        self._labels.append(
            FrameLabelRecord(op=str(op), site_id=str(site_id), label=str(label))
        )

    @property
    def records(self) -> list[FrameParamRecord]:
        return self._records

    @property
    def labels(self) -> list[FrameLabelRecord]:
        return self._labels

    def clear(self) -> None:
        self._records.clear()
        self._labels.clear()
