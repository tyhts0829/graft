"""
Purpose:
    config authoring source の確定済み bytes を、別 process でも同じ candidate として再構築できる immutable recipe にする。
Use when:
    worker replay、source capture、config authoring の process 境界を変更・調査する場合。
Constraints:
    - captured bytes を source of truth とし、replay 時に live filesystem の内容へ置き換えない。
    - module source は相対 path の重複なし・決定的順序を維持する。
See:
    grafix.authoring_loader
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AuthoringModuleSource:
    """一 module の確定済み source bytes と論理 path。"""

    relative_path: Path
    content: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, Path) or self.relative_path.is_absolute():
            raise TypeError("relative_path は相対 Path です")
        if not self.relative_path.parts:
            raise ValueError("relative_path は空にできません")
        if type(self.content) is not bytes:
            raise TypeError("content は exact bytes です")

    @property
    def is_package(self) -> bool:
        """package initializer なら True を返す。"""

        return self.relative_path.name == "__init__.py"


@dataclass(frozen=True, slots=True)
class AuthoringSourceRoot:
    """config の一探索 root と、その時点の module bytes。"""

    path: Path
    modules: tuple[AuthoringModuleSource, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise TypeError("path は Path です")
        if type(self.modules) is not tuple or any(
            type(module) is not AuthoringModuleSource for module in self.modules
        ):
            raise TypeError("modules は AuthoringModuleSource の tuple です")
        relative_paths = tuple(module.relative_path for module in self.modules)
        if relative_paths != tuple(sorted(relative_paths, key=lambda item: item.parts)):
            raise ValueError("modules は relative path 順である必要があります")
        if len(set(relative_paths)) != len(relative_paths):
            raise ValueError("module relative path が重複しています")


@dataclass(frozen=True, slots=True)
class AuthoringDefinitionsRecipe:
    """worker が live disk を読まず config candidate を再構築する recipe。"""

    roots: tuple[AuthoringSourceRoot, ...] = ()

    def __post_init__(self) -> None:
        if type(self.roots) is not tuple or any(
            type(root) is not AuthoringSourceRoot for root in self.roots
        ):
            raise TypeError("roots は AuthoringSourceRoot の tuple です")


__all__ = [
    "AuthoringDefinitionsRecipe",
    "AuthoringModuleSource",
    "AuthoringSourceRoot",
]
