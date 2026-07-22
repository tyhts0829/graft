# どこで: `src/grafix/__init__.py`。
# 何を: ルート `grafix` パッケージを定義する。
# なぜ: import 起点を `grafix` に統一するため。

from __future__ import annotations

from grafix.api import (
    Color,
    E,
    ExportFormat,
    ExportResult,
    Frame,
    G,
    L,
    OperationInfo,
    P,
    RenderOptions,
    RenderSession,
    RenderSessionMetadata,
    ResourceBudget,
    ResourceLimitError,
    RuntimeLimitProfiles,
    RuntimeLimits,
    VariationBatchResult,
    VariationRenderResult,
    effect,
    export,
    preset,
    primitive,
    render,
    render_variation_batch,
    run,
)
from grafix.cc import cc

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
    "cc",
    "effect",
    "export",
    "preset",
    "primitive",
    "render",
    "render_variation_batch",
    "run",
]
