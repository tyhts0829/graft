"""
Purpose:
    Grafixのauthoring DSLと公開value typeを、標準package namespaceへまとめる。
Use when:
    `grafix.api`の公開面、遅延型export、またはG/E/L/P decorator導線を変更する場合。
Constraints:
    - application callableは各定義moduleまたはrootから取得し、package直下へ再公開しない。
    - submodule名の意味をcallableと兼用せず、通常のModuleTypeを維持する。
See:
    `architecture.md` §2.1。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from grafix.core.operation_authoring import effect, primitive

from .effects import E
from .layers import L
from .operation_info import OperationInfo
from .preset import preset
from .presets import P
from .primitives import G

if TYPE_CHECKING:
    from grafix.api.cc import CcView
    from grafix.api.render import (
        CaptureProvenance,
        Color,
        ColorInput,
        ConfigProvenance,
        ExportFormat,
        ExportResult,
        Frame,
        FrameProvenance,
        FrameStyle,
        GitProvenance,
        LoadProvenance,
        ParameterLoadMode,
        ParameterLoadState,
        ParameterSnapshotProvenance,
        ParamStoreLoadDiagnostic,
        RGB01,
        RGB8,
        RealizedLayer,
        RenderOptions,
        RenderSession,
        RenderSessionMetadata,
        RuntimeConfig,
        SessionProvenance,
        SourceProvenance,
    )
    from grafix.export.variation_batch import (
        VariationBatchResult,
        VariationRenderResult,
        VariationRenderStatus,
    )
    from grafix.core.gcode_params import GCodeParams
    from grafix.core.geometry import Geometry
    from grafix.core.layer import Layer
    from grafix.core.parameters.meta import ParamMeta
    from grafix.core.realize import GeometryCacheKey
    from grafix.core.realized_geometry import RealizedGeometry
    from grafix.core.resource_budget import ResourceBudget, ResourceLimitError
    from grafix.core.runtime_limits import RuntimeLimitProfiles, RuntimeLimits
    from grafix.core.scene import SceneItem

__all__ = [
    "CaptureProvenance",
    "CcView",
    "Color",
    "ColorInput",
    "ConfigProvenance",
    "E",
    "ExportFormat",
    "ExportResult",
    "Frame",
    "FrameProvenance",
    "FrameStyle",
    "G",
    "GCodeParams",
    "Geometry",
    "GeometryCacheKey",
    "GitProvenance",
    "L",
    "Layer",
    "LoadProvenance",
    "OperationInfo",
    "P",
    "ParamMeta",
    "ParameterLoadMode",
    "ParameterLoadState",
    "ParameterSnapshotProvenance",
    "ParamStoreLoadDiagnostic",
    "RGB01",
    "RGB8",
    "RealizedGeometry",
    "RealizedLayer",
    "RenderOptions",
    "RenderSession",
    "RenderSessionMetadata",
    "ResourceBudget",
    "ResourceLimitError",
    "RuntimeConfig",
    "RuntimeLimitProfiles",
    "RuntimeLimits",
    "SceneItem",
    "SessionProvenance",
    "SourceProvenance",
    "VariationBatchResult",
    "VariationRenderResult",
    "VariationRenderStatus",
    "effect",
    "preset",
    "primitive",
]

_PUBLIC_TYPES: dict[str, tuple[str, str]] = {
    "CaptureProvenance": ("grafix.core.capture_provenance", "CaptureProvenance"),
    "CcView": ("grafix.api.cc", "CcView"),
    "Color": ("grafix.core.render_options", "Color"),
    "ColorInput": ("grafix.core.render_options", "ColorInput"),
    "ConfigProvenance": ("grafix.core.capture_provenance", "ConfigProvenance"),
    "ExportFormat": ("grafix.core.export_format", "ExportFormat"),
    "ExportResult": ("grafix.core.export_result", "ExportResult"),
    "Frame": ("grafix.api.render", "Frame"),
    "FrameProvenance": ("grafix.core.capture_provenance", "FrameProvenance"),
    "FrameStyle": ("grafix.core.parameters.style_resolver", "FrameStyle"),
    "GCodeParams": ("grafix.core.gcode_params", "GCodeParams"),
    "Geometry": ("grafix.core.geometry", "Geometry"),
    "GeometryCacheKey": ("grafix.core.realize", "GeometryCacheKey"),
    "GitProvenance": ("grafix.core.capture_provenance", "GitProvenance"),
    "Layer": ("grafix.core.layer", "Layer"),
    "LoadProvenance": ("grafix.core.parameters.runtime", "LoadProvenance"),
    "ParamMeta": ("grafix.core.parameters.meta", "ParamMeta"),
    "ParameterLoadMode": ("grafix.core.parameters.source", "ParameterLoadMode"),
    "ParameterLoadState": ("grafix.core.parameters.runtime", "ParameterLoadState"),
    "ParameterSnapshotProvenance": (
        "grafix.core.capture_provenance",
        "ParameterSnapshotProvenance",
    ),
    "ParamStoreLoadDiagnostic": (
        "grafix.core.parameters.runtime",
        "ParamStoreLoadDiagnostic",
    ),
    "RGB01": ("grafix.core.render_options", "RGB01"),
    "RGB8": ("grafix.core.render_options", "RGB8"),
    "RealizedGeometry": (
        "grafix.core.realized_geometry",
        "RealizedGeometry",
    ),
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
    "SceneItem": ("grafix.core.scene", "SceneItem"),
    "SessionProvenance": ("grafix.core.capture_provenance", "SessionProvenance"),
    "SourceProvenance": ("grafix.core.capture_provenance", "SourceProvenance"),
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
}


def __getattr__(name: str) -> Any:
    """公開 value type を定義 module から遅延解決する。"""

    try:
        module_name, attribute_name = _PUBLIC_TYPES[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """通常の module 名と公開名を返す。"""

    return sorted(set(globals()) | set(__all__))
