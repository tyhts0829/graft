"""Grafix の公開 facade。

サブパッケージ（特に ``grafix.core``）だけを利用する場合に、GUI、export、
設定読み込みまで初期化しないよう、公開値は要求された時点で解決する。
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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


class _LazyPublicName:
    """同名 submodule の上書きから遅延公開名を区別する sentinel。"""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


def _resolve_public_name(name: str) -> Any:
    """公開名を対応する facade から取得する。"""

    if name == "cc":
        from grafix.cc import cc

        return cc
    import grafix.api as api

    return getattr(api, name)


class _GrafixFacadeModule(ModuleType):
    """同名 submodule より root の callable API を優先する module。"""

    def __getattribute__(self, name: str) -> Any:
        value = ModuleType.__getattribute__(self, name)
        if isinstance(value, _LazyPublicName):
            value = _resolve_public_name(value.name)
            ModuleType.__setattr__(self, name, value)
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        # import machinery は ``grafix.export`` / ``grafix.cc`` の load 後に
        # 同名の親属性を module で上書きする。従来の root callable surface と
        # lazy identity を両立するため、その二名だけは sys.modules を正本にする。
        if name in {"export", "cc"} and isinstance(value, ModuleType):
            current = ModuleType.__getattribute__(self, "__dict__").get(name)
            if isinstance(current, _LazyPublicName) or callable(current):
                return
        ModuleType.__setattr__(self, name, value)


# root 公開名と同名の submodule は PEP 562 の ``__getattr__`` より先に親属性へ
# install されるため、二名だけ sentinel と module assignment guard を使う。
if not TYPE_CHECKING:
    export = _LazyPublicName("export")
    cc = _LazyPublicName("cc")


def __getattr__(name: str) -> Any:
    """公開名を対応する facade から遅延解決する。"""

    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = _resolve_public_name(name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """通常の module 名と公開 facade 名を返す。"""

    return sorted(set(globals()) | set(__all__))


sys.modules[__name__].__class__ = _GrafixFacadeModule
