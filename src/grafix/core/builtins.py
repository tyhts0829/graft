"""builtin operation manifest と immutable catalog bootstrap を提供する。"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import cast

from grafix.core.authoring_definitions import RegistrationTarget
from grafix.core.operation_catalog import OperationCatalog
from grafix.core.operation_declaration import (
    OpDeclaration,
    OpKind,
    operation_declaration,
)
from grafix.core.parameters.identity import identity_string
from grafix.core.value_validation import exact_string_choice


@dataclass(frozen=True, slots=True)
class BuiltinOperationManifestItem:
    """builtin declaration を module cache から回収する静的 locator。"""

    kind: OpKind
    name: str
    module: str
    attribute: str
    evaluator_abi: str

    def __post_init__(self) -> None:
        kind = cast(
            OpKind,
            exact_string_choice(
                self.kind,
                name="builtin operation kind",
                choices=("primitive", "effect"),
            ),
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "name",
            identity_string(self.name, name="builtin operation name"),
        )
        object.__setattr__(
            self,
            "module",
            identity_string(self.module, name="builtin operation module"),
        )
        object.__setattr__(
            self,
            "attribute",
            identity_string(self.attribute, name="builtin operation attribute"),
        )
        object.__setattr__(
            self,
            "evaluator_abi",
            identity_string(self.evaluator_abi, name="builtin evaluator ABI"),
        )


_PRIMITIVE_NAMES = (
    "arc",
    "asemic",
    "bezier",
    "circle",
    "ellipse",
    "grid",
    "line",
    "lissajous",
    "laplace_field_grid",
    "lsystem",
    "polygon",
    "polyline",
    "polyhedron",
    "rect",
    "sphere",
    "spiral",
    "spline",
    "text",
    "torus",
    "wave",
    "topographic_contours",
)

_EFFECT_NAMES = (
    "collapse",
    "scale",
    "rotate",
    "fill",
    "dash",
    "displace",
    "wobble",
    "affine",
    "subdivide",
    "quantize",
    "pixelate",
    "partition",
    "mirror",
    "mirror3d",
    "metaball",
    "isocontour",
    "translate",
    "extrude",
    "repeat",
    "buffer",
    "bold",
    "drop",
    "trim",
    "lowpass",
    "highpass",
    "clip",
    "twist",
    "weave",
    "growth",
    "relax",
    "reaction_diffusion",
    "warp",
    "resample",
    "simplify",
    "deduplicate",
    "boolean",
    "offset_curve",
)

_BUILTIN_OPERATION_MANIFEST = tuple(
    BuiltinOperationManifestItem(
        kind="primitive",
        name=name,
        module=f"grafix.core.primitives.{name}",
        attribute=name,
        evaluator_abi="1",
    )
    for name in _PRIMITIVE_NAMES
) + tuple(
    BuiltinOperationManifestItem(
        kind="effect",
        name=name,
        module=f"grafix.core.effects.{name}",
        attribute=name,
        evaluator_abi="1",
    )
    for name in _EFFECT_NAMES
)

_MANIFEST_BY_KEY = MappingProxyType(
    {(item.kind, item.name): item for item in _BUILTIN_OPERATION_MANIFEST}
)

# builtin 列挙 consumer は manifest と同じ一つのデータから投影する。
_BUILTIN_PRIMITIVE_MODULES = MappingProxyType(
    {
        item.name: item.module
        for item in _BUILTIN_OPERATION_MANIFEST
        if item.kind == "primitive"
    }
)
_BUILTIN_EFFECT_MODULES = MappingProxyType(
    {
        item.name: item.module
        for item in _BUILTIN_OPERATION_MANIFEST
        if item.kind == "effect"
    }
)

_BOOTSTRAP_LOCK = RLock()
_BUILTIN_OPERATION_CATALOG: OperationCatalog | None = None


def builtin_operation_manifest() -> tuple[BuiltinOperationManifestItem, ...]:
    """kind/name 順序が固定された builtin manifest を返す。"""

    return _BUILTIN_OPERATION_MANIFEST


def is_builtin_operation(
    *,
    kind: OpKind,
    name: str,
    module: str,
    attribute: str,
) -> bool:
    """callable identity が manifest の builtin locator と一致するか返す。"""

    item = _MANIFEST_BY_KEY.get((kind, name))
    return (
        item is not None
        and item.module == module
        and item.attribute == attribute
    )


def builtin_evaluator_abi(
    *,
    kind: OpKind,
    name: str,
    module: str,
    attribute: str,
) -> str | None:
    """exact builtin locator の manifest evaluator ABI を返す。"""

    item = _MANIFEST_BY_KEY.get((kind, name))
    if (
        item is None
        or item.module != module
        or item.attribute != attribute
    ):
        return None
    return item.evaluator_abi


def builtin_operation_declaration(kind: OpKind, name: str) -> OpDeclaration | None:
    """manifest entry を import し、callable に付与済みの declaration を回収する。"""

    item = _MANIFEST_BY_KEY.get((kind, name))
    if item is None:
        return None
    module = importlib.import_module(item.module)
    try:
        callable_object = getattr(module, item.attribute)
    except AttributeError as exc:
        raise RuntimeError(
            f"builtin {item.kind} callable が見つかりません: "
            f"{item.module}.{item.attribute}"
        ) from exc
    declaration = operation_declaration(callable_object)
    if declaration.kind != item.kind or declaration.name != item.name:
        raise RuntimeError(
            f"builtin manifest と declaration が一致しません: {item.kind} {item.name!r}"
        )
    return declaration


def builtin_operation_catalog() -> OperationCatalog:
    """全 builtin declaration を manifest から一度だけ回収した snapshot を返す。"""

    global _BUILTIN_OPERATION_CATALOG
    with _BOOTSTRAP_LOCK:
        cached = _BUILTIN_OPERATION_CATALOG
        if cached is not None:
            return cached
        target = RegistrationTarget()
        for item in _BUILTIN_OPERATION_MANIFEST:
            declaration = builtin_operation_declaration(item.kind, item.name)
            if declaration is None:  # manifest からの lookup なので到達しない。
                raise RuntimeError(
                    f"builtin declaration が見つかりません: {item.kind} {item.name!r}"
                )
            target.register(declaration)
        catalog = target.snapshot().operations
        _BUILTIN_OPERATION_CATALOG = catalog
        return catalog


def ensure_builtin_primitive_registered(name: str) -> bool:
    """builtin primitive declaration を import/cache 上で利用可能にする。"""

    name_s = identity_string(name, name="builtin primitive name")
    return builtin_operation_declaration("primitive", name_s) is not None


def ensure_builtin_effect_registered(name: str) -> bool:
    """builtin effect declaration を import/cache 上で利用可能にする。"""

    name_s = identity_string(name, name="builtin effect name")
    return builtin_operation_declaration("effect", name_s) is not None


def ensure_builtin_primitives_registered() -> None:
    """全 builtin primitive declaration を import/cache 上で利用可能にする。"""

    for name in _BUILTIN_PRIMITIVE_MODULES:
        assert ensure_builtin_primitive_registered(name)


def ensure_builtin_effects_registered() -> None:
    """全 builtin effect declaration を import/cache 上で利用可能にする。"""

    for name in _BUILTIN_EFFECT_MODULES:
        assert ensure_builtin_effect_registered(name)


def ensure_builtin_ops_registered() -> None:
    """全 builtin declaration の immutable catalog bootstrap を完了する。"""

    builtin_operation_catalog()


__all__ = [
    "BuiltinOperationManifestItem",
    "builtin_operation_catalog",
    "builtin_operation_declaration",
    "builtin_operation_manifest",
    "builtin_evaluator_abi",
    "ensure_builtin_effect_registered",
    "ensure_builtin_effects_registered",
    "ensure_builtin_ops_registered",
    "ensure_builtin_primitive_registered",
    "ensure_builtin_primitives_registered",
    "is_builtin_operation",
]
