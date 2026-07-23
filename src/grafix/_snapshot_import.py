"""確定済み Python source bytes を一時 package として import する。

この module は authoring candidate と sketch source reload が共有する、
``sys.meta_path`` / ``sys.modules`` 変更の狭い process-global primitive だけを持つ。
source の発見、catalog registration、generation の accept/rollback は caller の責務とする。
"""

from __future__ import annotations

import builtins
import contextlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from grafix.core.definition_fingerprint import attach_module_content_fingerprint

SnapshotImportGuard = Callable[[str, int], None]

_IMPORT_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class SnapshotModuleSource:
    """一 module として実行する確定済み source。"""

    path: Path
    content: bytes
    is_package: bool
    canonical_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise TypeError("path は Path です")
        if type(self.content) is not bytes:
            raise TypeError("content は exact bytes です")
        if type(self.is_package) is not bool:
            raise TypeError("is_package は exact bool です")
        if type(self.canonical_name) is not str or not self.canonical_name:
            raise TypeError("canonical_name は空でない str です")


@dataclass(frozen=True, slots=True)
class SnapshotImportModule:
    """import 時の unique 名と source の対応。"""

    name: str
    source: SnapshotModuleSource

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name:
            raise TypeError("name は空でない str です")
        if type(self.source) is not SnapshotModuleSource:
            raise TypeError("source は exact SnapshotModuleSource です")


@dataclass(frozen=True, slots=True)
class SnapshotImportPlan:
    """一時 namespace へ install する source の不変 plan。"""

    package_names: tuple[str, ...]
    modules: tuple[SnapshotImportModule, ...]
    canonical_package_name: str
    display_name: str

    def __post_init__(self) -> None:
        if type(self.package_names) is not tuple or any(
            type(name) is not str or not name for name in self.package_names
        ):
            raise TypeError("package_names は空でない str の tuple です")
        if len(set(self.package_names)) != len(self.package_names):
            raise ValueError("package_names が重複しています")
        if type(self.modules) is not tuple or any(
            type(module) is not SnapshotImportModule for module in self.modules
        ):
            raise TypeError("modules は SnapshotImportModule の tuple です")
        module_names = tuple(module.name for module in self.modules)
        if len(set(module_names)) != len(module_names):
            raise ValueError("snapshot module name が重複しています")
        if any(
            not any(
                module_name.startswith(f"{package_name}.")
                for package_name in self.package_names
            )
            for module_name in module_names
        ):
            raise ValueError("snapshot module は package namespace 配下に必要です")
        if type(self.canonical_package_name) is not str or not self.canonical_package_name:
            raise TypeError("canonical_package_name は空でない str です")
        if type(self.display_name) is not str or not self.display_name:
            raise TypeError("display_name は空でない str です")


class _SnapshotSourceLoader(importlib.abc.Loader):
    """``.pyc`` を介さず source bytes を実行する。"""

    def __init__(
        self,
        source: SnapshotModuleSource,
        *,
        import_guard: SnapshotImportGuard | None,
    ) -> None:
        self._source = source
        self._import_guard = import_guard

    def create_module(
        self,
        spec: importlib.machinery.ModuleSpec,
    ) -> types.ModuleType | None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        source = self._source
        import_guard = self._import_guard
        if import_guard is not None:
            original_import = builtins.__import__

            def guarded_import(
                name: str,
                globals: dict[str, object] | None = None,
                locals: dict[str, object] | None = None,
                fromlist: tuple[str, ...] = (),
                level: int = 0,
            ) -> object:
                import_guard(name, level)
                return original_import(name, globals, locals, fromlist, level)

            module.__dict__["__builtins__"] = {
                **vars(builtins),
                "__import__": guarded_import,
            }
        module.__dict__["__grafix_source_owner__"] = source.canonical_name
        attach_module_content_fingerprint(module, source.content)
        code = compile(source.content, str(source.path), "exec", dont_inherit=True)
        exec(code, module.__dict__)


class _SnapshotSourceFinder(importlib.abc.MetaPathFinder):
    """plan の unique namespace 内だけを source snapshot から解決する。"""

    def __init__(
        self,
        plan: SnapshotImportPlan,
        *,
        import_guard: SnapshotImportGuard | None,
    ) -> None:
        self._modules = {module.name: module.source for module in plan.modules}
        namespaces: set[str] = set()
        for module in plan.modules:
            package_name = next(
                name
                for name in plan.package_names
                if module.name.startswith(f"{name}.")
            )
            package_depth = len(package_name.split("."))
            parts = module.name.split(".")
            namespaces.update(
                ".".join(parts[:depth])
                for depth in range(package_depth + 1, len(parts))
            )
        self._namespaces = namespaces - self._modules.keys()
        self._display_name = plan.display_name
        self._import_guard = import_guard

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del path, target
        source = self._modules.get(fullname)
        if source is not None:
            search_locations = (
                [f"<{self._display_name}:{fullname}>"]
                if source.is_package
                else None
            )
            return importlib.util.spec_from_file_location(
                fullname,
                source.path,
                loader=_SnapshotSourceLoader(
                    source,
                    import_guard=self._import_guard,
                ),
                submodule_search_locations=search_locations,
            )
        if fullname not in self._namespaces:
            return None
        spec = importlib.machinery.ModuleSpec(fullname, loader=None, is_package=True)
        spec.submodule_search_locations = [f"<{self._display_name}:{fullname}>"]
        return spec


def _install_namespace_package(
    name: str,
    *,
    canonical_name: str,
    display_name: str,
) -> None:
    package = types.ModuleType(name)
    package.__package__ = name
    package.__path__ = [f"<{display_name}:{name}>"]  # type: ignore[attr-defined]
    package.__file__ = None
    package.__grafix_fingerprint_name__ = canonical_name  # type: ignore[attr-defined]
    package.__grafix_source_owner__ = canonical_name  # type: ignore[attr-defined]
    spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = list(package.__path__)
    package.__spec__ = spec
    sys.modules[name] = package


def _remove_snapshot_modules(package_names: tuple[str, ...]) -> None:
    prefixes = tuple(f"{name}." for name in package_names)
    for module_name in tuple(sys.modules):
        if module_name in package_names or module_name.startswith(prefixes):
            sys.modules.pop(module_name, None)


@contextlib.contextmanager
def snapshot_import_context(
    plan: SnapshotImportPlan,
    *,
    retain_modules: bool,
    import_guard: SnapshotImportGuard | None = None,
) -> Iterator[None]:
    """plan を一 lock 下で install し、finder を必ず解除する。

    ``retain_modules=True`` の場合だけ正常終了後も loaded module を残す。
    ``BaseException`` 経路では常に candidate module を破棄する。
    """

    if type(plan) is not SnapshotImportPlan:
        raise TypeError("plan は exact SnapshotImportPlan です")
    if type(retain_modules) is not bool:
        raise TypeError("retain_modules は exact bool です")
    finder = _SnapshotSourceFinder(plan, import_guard=import_guard)
    with _IMPORT_LOCK:
        _remove_snapshot_modules(plan.package_names)
        finder_installed = False
        succeeded = False
        try:
            for package_name in plan.package_names:
                _install_namespace_package(
                    package_name,
                    canonical_name=plan.canonical_package_name,
                    display_name=plan.display_name,
                )
            sys.meta_path.insert(0, finder)
            finder_installed = True
            yield
            succeeded = True
        finally:
            if finder_installed and finder in sys.meta_path:
                sys.meta_path.remove(finder)
            if not succeeded or not retain_modules:
                _remove_snapshot_modules(plan.package_names)


def remove_snapshot_modules(package_names: tuple[str, ...]) -> None:
    """保持中の snapshot package を共通 import lock 下で破棄する。"""

    if type(package_names) is not tuple or any(
        type(name) is not str or not name for name in package_names
    ):
        raise TypeError("package_names は空でない str の tuple です")
    with _IMPORT_LOCK:
        _remove_snapshot_modules(package_names)


__all__ = [
    "SnapshotImportGuard",
    "SnapshotImportModule",
    "SnapshotImportPlan",
    "SnapshotModuleSource",
    "remove_snapshot_modules",
    "snapshot_import_context",
]
