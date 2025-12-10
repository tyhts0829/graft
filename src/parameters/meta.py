# どこで: `src/parameters/meta.py`。
# 何を: ParamMeta と簡易推定ロジックを提供する。
# なぜ: GUI 生成と値検証に必要な型・レンジ情報を一元管理するため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True, slots=True)
class ParamMeta:
    """パラメータの UI/検証用メタ情報。

    ui_min/ui_max はスライダー初期レンジを示すだけで、実値をクランプしない。
    """

    kind: str  # "float" | "int" | "bool" | "str" | "enum" | "vec3"
    ui_min: Any | None = None
    ui_max: Any | None = None
    step: Any | None = None
    choices: Sequence[str] | None = None


def infer_meta_from_value(value: Any) -> ParamMeta:
    """値から簡易に ParamMeta を推定する（ユーザー定義 primitive/effect 用）。"""

    if isinstance(value, bool):
        return ParamMeta(kind="bool")
    if isinstance(value, int):
        # int はそのまま扱う
        return ParamMeta(kind="int", ui_min=None, ui_max=None, step=1)
    if isinstance(value, float):
        span = abs(value) if value != 0.0 else 1.0
        return ParamMeta(kind="float", ui_min=value - span, ui_max=value + span, step=span / 100.0)
    if isinstance(value, str):
        return ParamMeta(kind="str")
    if isinstance(value, Iterable):
        # vec3 を優先判定
        seq = list(value)
        if len(seq) == 3 and all(isinstance(v, (int, float)) for v in seq):
            return ParamMeta(kind="vec3", ui_min=None, ui_max=None, step=0.01)
        if not seq:
            return ParamMeta(kind="str")
        inner = infer_meta_from_value(seq[0])
        return ParamMeta(kind=inner.kind, ui_min=inner.ui_min, ui_max=inner.ui_max, step=inner.step)
    return ParamMeta(kind="str")
