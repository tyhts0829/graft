# どこで: `src/parameters/frame_params.py`。
# 何を: フレーム内で観測・解決したパラメータを貯めるバッファを定義する。
# なぜ: ParamStore へのマージをフレーム境界でまとめ、スレッド安全に扱うため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

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


class FrameParamsBuffer:
    """フレーム内のパラメータ観測を蓄積する単純なバッファ。"""

    def __init__(self) -> None:
        self._records: List[FrameParamRecord] = []

    def record(
        self,
        *,
        key: ParameterKey,
        base: Any,
        meta: ParamMeta,
        effective: Any | None = None,
        source: str | None = None,
    ) -> None:
        self._records.append(
            FrameParamRecord(
                key=key,
                base=base,
                meta=meta,
                effective=effective,
                source=source,
            )
        )

    @property
    def records(self) -> list[FrameParamRecord]:
        return self._records

    def clear(self) -> None:
        self._records.clear()
