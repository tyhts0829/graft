# どこで: `src/grafix/core/parameters/state.py`。
# 何を: ParamState を定義する。
# なぜ: GUI 側からの設定と既定値を保持し、snapshot の単位にするため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ParamState:
    """単一 ParameterKey に紐づく GUI 状態（レンジ情報は保持しない）。"""

    override: bool = True
    ui_value: Any = None
    cc_key: int | tuple[int | None, int | None, int | None] | None = None
