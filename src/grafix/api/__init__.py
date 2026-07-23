"""Grafix の公開 Python API facade。"""

from __future__ import annotations

from importlib import import_module
import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

from .effects import E
from .layers import L
from .operation_info import OperationInfo as OperationInfo
from .preset import preset
from .presets import P
from .primitives import G
from grafix.core.operation_authoring import effect, primitive
from grafix.core.resource_budget import ResourceBudget, ResourceLimitError
from grafix.core.runtime_limits import RuntimeLimitProfiles, RuntimeLimits

if TYPE_CHECKING:
    from .export import export
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
    from .runner import run
    from .variation_batch import (
        VariationBatchResult,
        VariationRenderResult,
        render_variation_batch,
    )

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

_RENDER_NAMES = frozenset(
    {
        "Color",
        "ExportFormat",
        "ExportResult",
        "Frame",
        "RenderOptions",
        "RenderSession",
        "RenderSessionMetadata",
        "render",
    }
)
_VARIATION_NAMES = frozenset(
    {"VariationBatchResult", "VariationRenderResult", "render_variation_batch"}
)


class _LazyPublicName:
    """同名 submodule と区別する遅延公開名 sentinel。"""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def _resolve_api_name(name: str) -> Any:
    """重い API group の公開値を実装 module から取得する。"""

    if name in _RENDER_NAMES:
        render_module = import_module("grafix.api.render")
        return getattr(render_module, name)
    if name in _VARIATION_NAMES:
        variation_module = import_module("grafix.api.variation_batch")
        return getattr(variation_module, name)
    if name == "export":
        export_module = import_module("grafix.api.export")
        return export_module.export
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _ApiFacadeModule(ModuleType):
    """同名実装 module より公開 callable を優先する API facade。"""

    def __getattribute__(self, name: str) -> Any:
        value = ModuleType.__getattribute__(self, name)
        if isinstance(value, _LazyPublicName):
            value = _resolve_api_name(value.name)
            ModuleType.__setattr__(self, name, value)
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"render", "export"} and isinstance(value, ModuleType):
            current = ModuleType.__getattribute__(self, "__dict__").get(name)
            if isinstance(current, _LazyPublicName) or callable(current):
                return
        ModuleType.__setattr__(self, name, value)


if not TYPE_CHECKING:
    render = _LazyPublicName("render")
    export = _LazyPublicName("export")


def __getattr__(name: str) -> Any:
    """重い API group を、最初に参照された時点で解決する。"""

    value = _resolve_api_name(name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """通常の module 名と公開 facade 名を返す。"""

    return sorted(set(globals()) | set(__all__))


if not TYPE_CHECKING:

    def run(*args: object, **kwargs: object) -> None:
        """GUI runtime を実行時まで読み込まずに公開 ``run`` へ委譲する。"""

        from .runner import run as runner_run

        runner_run(*args, **kwargs)


sys.modules[__name__].__class__ = _ApiFacadeModule
