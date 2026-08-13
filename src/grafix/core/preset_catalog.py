"""
Purpose:
    preset callable/schema/identity の declaration と、session/generation ごとの immutable preset catalog を定義する。
Use when:
    ``P`` の lookup・parameter identity、preset decorator の declaration 付与、catalog binding を変更・調査する場合。
Constraints:
    - mutable builder は snapshot 作成前に限り、draw には immutable catalog だけを束縛する。
    - 同じ target 内の同名 preset は拒否し、operation のような overwrite を許可しない。
    - config-scoped preset を draw scope 外の default catalog へ暗黙に流入させない。
See:
    architecture.md §3
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType

from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.key import validate_parameter_identity
from grafix.core.scene import SceneItem

_PRESET_DECLARATION_ATTRIBUTE = "__grafix_preset_declaration__"
_PRESET_PREFIX = "preset."


@dataclass(frozen=True, slots=True)
class PresetIdentity:
    """``P(...)`` が preset invoker へ渡す呼び出し identity。"""

    name: str | None
    key: str | int | None
    instance_key: str | int | None
    shared: bool

    def __post_init__(self) -> None:
        if self.name is not None:
            identity_string(self.name, name="preset label")
        validate_parameter_identity(
            key=self.key,
            instance_key=self.instance_key,
            shared=self.shared,
        )


def preset_op(name: str) -> str:
    """callable 名を ParameterKey 用の canonical preset op にする。"""

    return _PRESET_PREFIX + identity_string(name, name="preset name")


@dataclass(frozen=True, slots=True)
class PresetDeclaration:
    """decorator が生成する preset 1 件の immutable declaration。"""

    name: str
    func: Callable[..., SceneItem]
    invoker: Callable[..., SceneItem]
    schema: ParameterOpSchema

    def __post_init__(self) -> None:
        name = identity_string(self.name, name="preset name")
        if not callable(self.func):
            raise TypeError("preset func は callable である必要があります")
        if not callable(self.invoker):
            raise TypeError("preset invoker は callable である必要があります")
        if type(self.schema) is not ParameterOpSchema:
            raise TypeError("preset schema は exact ParameterOpSchema である必要があります")

        object.__setattr__(self, "name", name)

    @property
    def display_op(self) -> str:
        """ParameterKey/GUI が使う canonical preset operation 名。"""

        return f"preset.{self.name}"


@dataclass(frozen=True, slots=True)
class PresetCatalog(Mapping[str, PresetDeclaration]):
    """一 session/generation 内で変化しない preset catalog snapshot。"""

    _declarations: Mapping[str, PresetDeclaration]

    def __post_init__(self) -> None:
        declarations: dict[str, PresetDeclaration] = {}
        for raw_name, declaration in self._declarations.items():
            name = identity_string(raw_name, name="preset catalog name")
            if type(declaration) is not PresetDeclaration:
                raise TypeError(
                    "preset catalog value は exact PresetDeclaration である必要があります"
                )
            if declaration.name != name:
                raise ValueError("preset catalog key と declaration.name が一致しません")
            if name in declarations:
                raise ValueError(f"preset {name!r} は既に登録されています")
            declarations[name] = declaration
        object.__setattr__(self, "_declarations", MappingProxyType(declarations))

    def __getitem__(self, name: str) -> PresetDeclaration:
        return self._declarations[identity_string(name, name="preset name")]

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._declarations))

    def __len__(self) -> int:
        return len(self._declarations)

    def __contains__(self, name: object) -> bool:
        return type(name) is str and name in self._declarations

    def declarations(self) -> tuple[PresetDeclaration, ...]:
        """declaration を deterministic な preset 名順で返す。"""

        return tuple(self._declarations[name] for name in sorted(self._declarations))


class PresetCatalogBuilder:
    """preset declaration を snapshot 前に組み立てる mutable builder。"""

    __slots__ = ("_declarations",)

    def __init__(self, seed: PresetCatalog | None = None) -> None:
        """空、または既存 snapshot を seed にした builder を作る。"""

        if seed is not None and type(seed) is not PresetCatalog:
            raise TypeError("seed は exact PresetCatalog または None です")
        self._declarations: dict[str, PresetDeclaration] = (
            {} if seed is None else dict(seed._declarations)
        )

    def register(self, declaration: PresetDeclaration) -> None:
        """preset を一件登録する。同じ builder 内の同名定義は拒否する。"""

        if type(declaration) is not PresetDeclaration:
            raise TypeError("declaration は exact PresetDeclaration である必要があります")
        name = declaration.name
        if name in self._declarations:
            raise ValueError(f"preset {name!r} は既に登録されています")
        self._declarations[name] = declaration

    def freeze(self) -> PresetCatalog:
        """現在の declaration を defensive copy した snapshot を返す。"""

        return PresetCatalog(self._declarations)


def attach_preset_declaration(
    func: Callable[..., SceneItem],
    declaration: PresetDeclaration,
) -> None:
    """decorated callable へ immutable preset declaration を付与する。"""

    if not callable(func):
        raise TypeError("func は callable である必要があります")
    if type(declaration) is not PresetDeclaration:
        raise TypeError("declaration は exact PresetDeclaration である必要があります")
    try:
        setattr(func, _PRESET_DECLARATION_ATTRIBUTE, declaration)
    except (AttributeError, TypeError) as exc:
        raise TypeError("preset callable に declaration を付与できません") from exc


def preset_declaration(func: Callable[..., SceneItem]) -> PresetDeclaration:
    """decorated callable に付与済みの preset declaration を返す。"""

    if not callable(func):
        raise TypeError("func は callable である必要があります")
    declaration = getattr(func, _PRESET_DECLARATION_ATTRIBUTE, None)
    if type(declaration) is not PresetDeclaration:
        raise LookupError("callable に preset declaration が付与されていません")
    return declaration


_BOUND_PRESET_CATALOG: ContextVar[PresetCatalog | None] = ContextVar(
    "grafix_bound_preset_catalog",
    default=None,
)


@contextmanager
def bind_preset_catalog(catalog: PresetCatalog) -> Iterator[PresetCatalog]:
    """現在の execution context へ immutable preset catalog を束縛する。"""

    if type(catalog) is not PresetCatalog:
        raise TypeError("catalog は exact PresetCatalog である必要があります")
    token = _BOUND_PRESET_CATALOG.set(catalog)
    try:
        yield catalog
    finally:
        _BOUND_PRESET_CATALOG.reset(token)


def current_preset_catalog() -> PresetCatalog:
    """bound snapshot、または default authoring preset snapshot を返す。"""

    from grafix.core.authoring_definitions import (
        current_registration_target,
        default_authoring_definitions,
    )

    target = current_registration_target()
    if target is not None:
        return target.snapshot().presets

    bound = _BOUND_PRESET_CATALOG.get()
    if bound is not None:
        return bound

    return default_authoring_definitions.snapshot().presets


__all__ = [
    "PresetCatalog",
    "PresetCatalogBuilder",
    "PresetDeclaration",
    "PresetIdentity",
    "attach_preset_declaration",
    "bind_preset_catalog",
    "current_preset_catalog",
    "preset_declaration",
    "preset_op",
]
