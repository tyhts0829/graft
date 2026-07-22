# どこで: `src/grafix/api/__init__.py`。
# 何を: 公開 API パッケージのエントリポイントとして G/E/L/run と、ユーザー定義登録用の primitive/effect を再エクスポートする。
# なぜ: ユーザーコードからシンプルに API を import できるようにするため。

from __future__ import annotations

from typing import TYPE_CHECKING

from .effects import E
from .export import export
from .layers import L
from .operation_info import OperationInfo as OperationInfo
from .preset import preset
from .presets import P
from .primitives import G
from .render import (
    Color,
    ExportFormat,
    ExportResult,
    Frame,
    RenderOptions,
    RenderSession,
    RenderSessionMetadata,
    render,
)
from .variation_batch import (
    VariationBatchResult,
    VariationRenderResult,
    render_variation_batch,
)
from grafix.core.operation_authoring import effect, primitive
from grafix.core.resource_budget import ResourceBudget, ResourceLimitError
from grafix.core.runtime_limits import RuntimeLimitProfiles, RuntimeLimits

if TYPE_CHECKING:
    from .runner import run as run

__all__ = [
    "Color",
    "E",
    "ExportFormat",
    "ExportResult",
    "Frame",
    "G",
    "L",
    "OperationInfo",
    "P",
    "RenderOptions",
    "RenderSession",
    "RenderSessionMetadata",
    "ResourceBudget",
    "ResourceLimitError",
    "RuntimeLimitProfiles",
    "RuntimeLimits",
    "VariationBatchResult",
    "VariationRenderResult",
    "effect",
    "export",
    "preset",
    "primitive",
    "render",
    "render_variation_batch",
    "run",
]


if not TYPE_CHECKING:

    def run(*args: object, **kwargs: object) -> None:
        """公開 run API へのラッパ（遅延インポートで GUI 依存を後回しにする）。"""

        from .runner import run as _run

        _run(*args, **kwargs)
