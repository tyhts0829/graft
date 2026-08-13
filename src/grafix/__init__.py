"""
Purpose:
    Grafixの公開名を、定義moduleへ遅延接続するroot facadeとして固定する。
Use when:
    root公開API、遅延import、または標準namespaceの意味を変更する場合。
Constraints:
    - 各公開名を一つの定義module/attributeに対応させる。
    - submoduleとcallableへ同じdotted nameを割り当てず、`import grafix`を軽量に保つ。
See:
    `architecture.md` §2.1。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grafix.api.cc import CcView, cc
    from grafix.api.effects import E
    from grafix.api.export import save
    from grafix.api.layers import L
    from grafix.api.operation_info import OperationInfo
    from grafix.api.preset import preset
    from grafix.api.presets import P
    from grafix.api.primitives import G
    from grafix.api.render import (
        CaptureProvenance,
        Color,
        ColorInput,
        ExportFormat,
        ExportResult,
        Frame,
        FrameStyle,
        LoadProvenance,
        ParameterLoadMode,
        ParameterLoadState,
        ParamStoreLoadDiagnostic,
        RGB01,
        RGB8,
        RealizedLayer,
        RenderOptions,
        RenderSession,
        RenderSessionMetadata,
        RuntimeConfig,
        SessionProvenance,
        render,
    )
    from grafix.api.runner import run
    from grafix.api.variation_batch import render_variation_batch
    from grafix.core.canvas_sizes import (
        A2,
        A2_LANDSCAPE,
        A3,
        A3_LANDSCAPE,
        A4,
        A4_LANDSCAPE,
        A5,
        A5_LANDSCAPE,
        A6,
        A6_LANDSCAPE,
        SQUARE,
    )
    from grafix.export.variation_batch import (
        VariationBatchResult,
        VariationRenderResult,
        VariationRenderStatus,
    )
    from grafix.core.operation_authoring import effect, primitive
    from grafix.core.resource_budget import ResourceBudget, ResourceLimitError
    from grafix.core.runtime_limits import RuntimeLimitProfiles, RuntimeLimits

__all__ = [
    "A2",
    "A2_LANDSCAPE",
    "A3",
    "A3_LANDSCAPE",
    "A4",
    "A4_LANDSCAPE",
    "A5",
    "A5_LANDSCAPE",
    "A6",
    "A6_LANDSCAPE",
    "CaptureProvenance",
    "CcView",
    "Color",
    "ColorInput",
    "E",
    "ExportFormat",
    "ExportResult",
    "Frame",
    "FrameStyle",
    "G",
    "L",
    "LoadProvenance",
    "OperationInfo",
    "P",
    "ParameterLoadMode",
    "ParameterLoadState",
    "ParamStoreLoadDiagnostic",
    "RGB01",
    "RGB8",
    "RealizedLayer",
    "RenderOptions",
    "RenderSession",
    "RenderSessionMetadata",
    "ResourceBudget",
    "ResourceLimitError",
    "RuntimeConfig",
    "RuntimeLimitProfiles",
    "RuntimeLimits",
    "SQUARE",
    "SessionProvenance",
    "VariationBatchResult",
    "VariationRenderResult",
    "VariationRenderStatus",
    "cc",
    "effect",
    "preset",
    "primitive",
    "render",
    "render_variation_batch",
    "run",
    "save",
]

_PUBLIC_NAMES: dict[str, tuple[str, str]] = {
    "A2": ("grafix.core.canvas_sizes", "A2"),
    "A2_LANDSCAPE": ("grafix.core.canvas_sizes", "A2_LANDSCAPE"),
    "A3": ("grafix.core.canvas_sizes", "A3"),
    "A3_LANDSCAPE": ("grafix.core.canvas_sizes", "A3_LANDSCAPE"),
    "A4": ("grafix.core.canvas_sizes", "A4"),
    "A4_LANDSCAPE": ("grafix.core.canvas_sizes", "A4_LANDSCAPE"),
    "A5": ("grafix.core.canvas_sizes", "A5"),
    "A5_LANDSCAPE": ("grafix.core.canvas_sizes", "A5_LANDSCAPE"),
    "A6": ("grafix.core.canvas_sizes", "A6"),
    "A6_LANDSCAPE": ("grafix.core.canvas_sizes", "A6_LANDSCAPE"),
    "CaptureProvenance": ("grafix.core.capture_provenance", "CaptureProvenance"),
    "CcView": ("grafix.api.cc", "CcView"),
    "Color": ("grafix.core.render_options", "Color"),
    "ColorInput": ("grafix.core.render_options", "ColorInput"),
    "E": ("grafix.api.effects", "E"),
    "ExportFormat": ("grafix.core.export_format", "ExportFormat"),
    "ExportResult": ("grafix.core.export_result", "ExportResult"),
    "Frame": ("grafix.api.render", "Frame"),
    "FrameStyle": ("grafix.core.parameters.style_resolver", "FrameStyle"),
    "G": ("grafix.api.primitives", "G"),
    "L": ("grafix.api.layers", "L"),
    "LoadProvenance": ("grafix.core.parameters.runtime", "LoadProvenance"),
    "OperationInfo": ("grafix.api.operation_info", "OperationInfo"),
    "P": ("grafix.api.presets", "P"),
    "ParameterLoadMode": ("grafix.core.parameters.source", "ParameterLoadMode"),
    "ParameterLoadState": ("grafix.core.parameters.runtime", "ParameterLoadState"),
    "ParamStoreLoadDiagnostic": (
        "grafix.core.parameters.runtime",
        "ParamStoreLoadDiagnostic",
    ),
    "RGB01": ("grafix.core.render_options", "RGB01"),
    "RGB8": ("grafix.core.render_options", "RGB8"),
    "RealizedLayer": ("grafix.core.pipeline", "RealizedLayer"),
    "RenderOptions": ("grafix.core.render_options", "RenderOptions"),
    "RenderSession": ("grafix.api.render", "RenderSession"),
    "RenderSessionMetadata": ("grafix.api.render", "RenderSessionMetadata"),
    "ResourceBudget": ("grafix.core.resource_budget", "ResourceBudget"),
    "ResourceLimitError": ("grafix.core.resource_budget", "ResourceLimitError"),
    "RuntimeConfig": ("grafix.core.runtime_config", "RuntimeConfig"),
    "RuntimeLimitProfiles": (
        "grafix.core.runtime_limits",
        "RuntimeLimitProfiles",
    ),
    "RuntimeLimits": ("grafix.core.runtime_limits", "RuntimeLimits"),
    "SQUARE": ("grafix.core.canvas_sizes", "SQUARE"),
    "SessionProvenance": ("grafix.core.capture_provenance", "SessionProvenance"),
    "VariationBatchResult": (
        "grafix.export.variation_batch",
        "VariationBatchResult",
    ),
    "VariationRenderResult": (
        "grafix.export.variation_batch",
        "VariationRenderResult",
    ),
    "VariationRenderStatus": (
        "grafix.export.variation_batch",
        "VariationRenderStatus",
    ),
    "cc": ("grafix.api.cc", "cc"),
    "effect": ("grafix.core.operation_authoring", "effect"),
    "preset": ("grafix.api.preset", "preset"),
    "primitive": ("grafix.core.operation_authoring", "primitive"),
    "render": ("grafix.api.render", "render"),
    "render_variation_batch": (
        "grafix.api.variation_batch",
        "render_variation_batch",
    ),
    "run": ("grafix.api.runner", "run"),
    "save": ("grafix.api.export", "save"),
}


def __getattr__(name: str) -> Any:
    """公開名を、その値を定義する module から遅延解決する。"""

    try:
        module_name, attribute_name = _PUBLIC_NAMES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """通常の module 名と公開 facade 名を返す。"""

    return sorted(set(globals()) | set(__all__))
