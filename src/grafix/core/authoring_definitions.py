"""
Purpose:
    operation/preset decorator の登録先と、両者を同時点で固定する immutable authoring snapshot を定義する。
Use when:
    custom authoring の登録経路、candidate isolation、session が採用する定義 snapshot を変更・調査する場合。
Constraints:
    - process-level default store は authoring convenience に限り、evaluation state や resource を保持させない。
    - scoped registration は candidate target だけを変更し、session には mutable builder ではなく snapshot を渡す。
See:
    architecture.md §3
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import TypeAlias

from grafix.core.authoring_recipe import AuthoringDefinitionsRecipe
from grafix.core.operation_catalog import OperationCatalog, OperationCatalogBuilder
from grafix.core.operation_declaration import OpDeclaration
from grafix.core.preset_catalog import (
    PresetCatalog,
    PresetCatalogBuilder,
    PresetDeclaration,
)
from grafix.core.value_validation import exact_bool

AuthoringDeclaration: TypeAlias = OpDeclaration | PresetDeclaration


@dataclass(frozen=True, slots=True)
class AuthoringDefinitionsSnapshot:
    """operation/preset を同じ時点で固定した immutable authoring snapshot。"""

    operations: OperationCatalog
    presets: PresetCatalog
    recipe: AuthoringDefinitionsRecipe | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if type(self.operations) is not OperationCatalog:
            raise TypeError("operations は exact OperationCatalog である必要があります")
        if type(self.presets) is not PresetCatalog:
            raise TypeError("presets は exact PresetCatalog である必要があります")
        if self.recipe is not None and type(self.recipe) is not AuthoringDefinitionsRecipe:
            raise TypeError(
                "recipe は exact AuthoringDefinitionsRecipe または None です"
            )


class RegistrationTarget:
    """decorator declaration を candidate builder へ集約する登録先。"""

    __slots__ = ("_operations", "_presets")

    def __init__(
        self,
        *,
        operations: OperationCatalog | None = None,
        presets: PresetCatalog | None = None,
    ) -> None:
        """空、または既存 snapshot を seed にした登録先を作る。"""

        self._operations = OperationCatalogBuilder(operations)
        self._presets = PresetCatalogBuilder(presets)

    def register(
        self,
        declaration: AuthoringDeclaration,
        *,
        overwrite: bool = False,
    ) -> None:
        """operation/preset declaration を型に応じた builder へ登録する。

        Parameters
        ----------
        declaration : OpDeclaration | PresetDeclaration
            decorator が作った immutable declaration。
        overwrite : bool, default=False
            operation の同名 entry だけを置換する。preset の置換は許可しない。
        """

        overwrite_b = exact_bool(overwrite, name="overwrite")
        if type(declaration) is OpDeclaration:
            self._operations.register(declaration, overwrite=overwrite_b)
            return
        if type(declaration) is PresetDeclaration:
            if overwrite_b:
                raise ValueError("preset declaration は overwrite できません")
            self._presets.register(declaration)
            return
        raise TypeError("declaration は exact OpDeclaration または PresetDeclaration です")

    def snapshot(
        self,
        *,
        recipe: AuthoringDefinitionsRecipe | None = None,
    ) -> AuthoringDefinitionsSnapshot:
        """現在の candidate を defensive copy した immutable snapshot にする。"""

        return AuthoringDefinitionsSnapshot(
            operations=self._operations.freeze(),
            presets=self._presets.freeze(),
            recipe=recipe,
        )


class DefaultAuthoringDefinitions(RegistrationTarget):
    """通常 module import 用の thread-safe な process-level 定義 store。

    Notes
    -----
    この store は authoring convenience に限る。session/evaluation は
    :meth:`snapshot` の戻り値だけを所有し、store や lock を保持しない。
    """

    __slots__ = ("_lock",)

    def __init__(self) -> None:
        super().__init__()
        self._lock = RLock()

    def register(
        self,
        declaration: AuthoringDeclaration,
        *,
        overwrite: bool = False,
    ) -> None:
        """短い lock 内で declaration を atomic に登録する。"""

        with self._lock:
            super().register(declaration, overwrite=overwrite)

    def snapshot(
        self,
        *,
        recipe: AuthoringDefinitionsRecipe | None = None,
    ) -> AuthoringDefinitionsSnapshot:
        """短い lock 内で operation/preset を同時に snapshot する。"""

        with self._lock:
            return super().snapshot(recipe=recipe)


_CURRENT_REGISTRATION_TARGET: ContextVar[RegistrationTarget | None] = ContextVar(
    "grafix_current_registration_target",
    default=None,
)


def current_registration_target() -> RegistrationTarget | None:
    """現在の execution context に束縛された登録先を返す。"""

    return _CURRENT_REGISTRATION_TARGET.get()


def register_authoring_declaration(
    declaration: AuthoringDeclaration,
    *,
    overwrite: bool = False,
) -> None:
    """declaration を scoped target、なければ default store へ一経路で登録する。"""

    target = current_registration_target()
    if target is None:
        target = default_authoring_definitions
    target.register(declaration, overwrite=overwrite)


@contextmanager
def registration_scope(target: RegistrationTarget) -> Iterator[RegistrationTarget]:
    """decorator registration を ``target`` だけへ向ける context scope。

    Parameters
    ----------
    target : RegistrationTarget
        source/config candidate が所有する登録先。

    Yields
    ------
    RegistrationTarget
        束縛した同一 target。
    """

    if not isinstance(target, RegistrationTarget):
        raise TypeError("target は RegistrationTarget である必要があります")
    token = _CURRENT_REGISTRATION_TARGET.set(target)
    try:
        yield target
    finally:
        _CURRENT_REGISTRATION_TARGET.reset(token)


default_authoring_definitions = DefaultAuthoringDefinitions()
"""通常 module scope の公開 decorator が使用する唯一の default 定義 store。"""


__all__ = [
    "AuthoringDeclaration",
    "AuthoringDefinitionsSnapshot",
    "DefaultAuthoringDefinitions",
    "RegistrationTarget",
    "current_registration_target",
    "default_authoring_definitions",
    "register_authoring_declaration",
    "registration_scope",
]
