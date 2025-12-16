# どこで: `src/graft/core/parameters/meta.py`。
# 何を: ParamMeta（GUI 表示/検証のためのメタ情報）を提供する。
# なぜ: GUI 生成と値検証に必要な型・レンジ情報を一元管理するため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class ParamMeta:
    """パラメータの UI/検証用メタ情報。

    ui_min/ui_max はスライダー初期レンジを示すだけで、実値をクランプしない。
    """

    kind: str  # "float" | "int" | "bool" | "str" | "choice" | "vec3"
    ui_min: Any | None = None
    ui_max: Any | None = None
    choices: Sequence[str] | None = None
