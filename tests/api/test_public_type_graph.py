from __future__ import annotations

from dataclasses import is_dataclass
from importlib import import_module
import inspect
from typing import get_args, get_origin, get_type_hints

import grafix
import grafix.api as api
from grafix.api.render import (
    CaptureProvenance,
    ConfigProvenance,
    FrameProvenance,
    GitProvenance,
    ParameterSnapshotProvenance,
    SourceProvenance,
)
from grafix.core.gcode_params import GCodeParams
from grafix.core.geometry import Geometry
from grafix.core.layer import Layer
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.realize import GeometryCacheKey
from grafix.core.realized_geometry import RealizedGeometry


def _grafix_annotation_types(annotation: object) -> tuple[type[object], ...]:
    """annotation container 内の Grafix class を再帰的に列挙する。"""

    origin = get_origin(annotation)
    if origin is not None:
        return tuple(
            nested
            for argument in get_args(annotation)
            for nested in _grafix_annotation_types(argument)
        )
    if inspect.isclass(annotation) and annotation.__module__.startswith("grafix."):
        return (annotation,)
    return ()


def _has_formal_public_path(value: type[object]) -> bool:
    """root/API/定義 module のいずれかが型を identity 公開するか返す。"""

    name = value.__name__
    definition_module = import_module(value.__module__)
    return any(
        name in getattr(module, "__all__", ())
        and getattr(module, name, None) is value
        for module in (grafix, api, definition_module)
    )


def test_common_signature_types_are_available_from_root_and_api() -> None:
    common_names = (
        "CaptureProvenance",
        "Color",
        "ColorInput",
        "ExportFormat",
        "ExportResult",
        "Frame",
        "FrameStyle",
        "LoadProvenance",
        "ParameterLoadMode",
        "ParameterLoadState",
        "ParamStoreLoadDiagnostic",
        "RGB01",
        "RGB8",
        "RealizedLayer",
        "RenderOptions",
        "RenderSession",
        "RenderSessionMetadata",
        "RuntimeConfig",
        "RuntimeLimitProfiles",
        "RuntimeLimits",
        "SessionProvenance",
        "VariationBatchResult",
        "VariationRenderResult",
        "VariationRenderStatus",
    )

    for name in common_names:
        assert name in grafix.__all__
        assert name in api.__all__
        assert getattr(grafix, name) is getattr(api, name)


def test_nested_provenance_field_types_have_stable_api_paths() -> None:
    expected = {
        "CaptureProvenance": CaptureProvenance,
        "ConfigProvenance": ConfigProvenance,
        "FrameProvenance": FrameProvenance,
        "GitProvenance": GitProvenance,
        "ParameterSnapshotProvenance": ParameterSnapshotProvenance,
        "SourceProvenance": SourceProvenance,
    }

    for name, value in expected.items():
        assert name in api.__all__
        assert getattr(api, name) is value


def test_direct_nested_value_types_have_stable_api_paths() -> None:
    expected = {
        "GCodeParams": GCodeParams,
        "Geometry": Geometry,
        "GeometryCacheKey": GeometryCacheKey,
        "Layer": Layer,
        "ParamMeta": ParamMeta,
        "RealizedGeometry": RealizedGeometry,
    }

    for name, value in expected.items():
        assert name in api.__all__
        assert getattr(api, name) is value


def test_public_dataclass_annotation_graph_has_formal_paths() -> None:
    pending = [
        value
        for name in api.__all__
        if inspect.isclass(value := getattr(api, name))
    ]
    seen: set[type[object]] = set()

    while pending:
        public_type = pending.pop()
        if public_type in seen:
            continue
        seen.add(public_type)
        assert _has_formal_public_path(public_type), (
            f"{public_type.__module__}.{public_type.__name__} に正式 public path がありません"
        )
        if not is_dataclass(public_type):
            continue
        for annotation in get_type_hints(public_type).values():
            pending.extend(_grafix_annotation_types(annotation))


def test_public_application_signatures_do_not_expose_internal_composition() -> None:
    assert "definitions" not in inspect.signature(grafix.RenderSession).parameters
    assert "definitions" not in inspect.signature(grafix.render).parameters
    assert "config_fallback" not in inspect.signature(grafix.run).parameters
    assert (
        "capture_service"
        not in inspect.signature(grafix.render_variation_batch).parameters
    )
    assert not hasattr(grafix.RenderSession, "param_store")

    assert "ParamStore" not in grafix.__all__
    assert "ParamStore" not in api.__all__
    assert not hasattr(grafix, "ParamStore")
    assert not hasattr(api, "ParamStore")
    assert ParamStore.__module__ == "grafix.core.parameters.store"


def test_api_package_does_not_reexport_application_callables() -> None:
    for name in ("render", "save", "run", "render_variation_batch"):
        assert name not in api.__all__


def test_root_and_api_all_are_explicit_public_contracts() -> None:
    expected_root = {
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
    }
    expected_api = {
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
    }

    assert len(grafix.__all__) == len(expected_root)
    assert set(grafix.__all__) == expected_root
    assert len(api.__all__) == len(expected_api)
    assert set(api.__all__) == expected_api
