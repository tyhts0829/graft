"""sketch source を immutable authoring generation として隔離 load する。"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib
import inspect
import traceback
import types
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from grafix._snapshot_import import (
    SnapshotImportGuard,
    SnapshotImportModule,
    SnapshotImportPlan,
    SnapshotModuleSource,
    remove_snapshot_modules,
    snapshot_import_context,
)
from grafix._source_import_policy import validate_source_import_policy
from grafix.authoring_loader import (
    default_session_authoring_definitions,
    load_authoring_definitions_recipe,
    load_config_authoring_definitions,
)
from grafix.core.authoring_definitions import (
    AuthoringDefinitionsSnapshot,
    RegistrationTarget,
    registration_scope,
)
from grafix.core.authoring_recipe import AuthoringDefinitionsRecipe
from grafix.core.operation_catalog import OperationCatalog, bind_operation_catalog
from grafix.core.preset_catalog import PresetCatalog, bind_preset_catalog
from grafix.core.runtime_config import RuntimeConfig, bind_runtime_config
from grafix.core.scene import SceneItem
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
)

ReloadStatus = Literal["unchanged", "reloaded", "failed"]
SourceFingerprint = (
    tuple[tuple[str, int, int, int, int], ...]
    | tuple[Literal["missing"]]
)

_ENTRY_MODULE_NAME = "_entry"
_CANONICAL_SOURCE_PACKAGE = "_grafix_watch_source"


class _SourceDependencyNotFound(ModuleNotFoundError):
    """未作成 helper の生成候補 path を polling 層へ渡す。"""

    def __init__(self, module_name: str, candidate_paths: tuple[Path, ...]) -> None:
        super().__init__(
            f"source relative helper が見つかりません: {module_name!r}"
        )
        self.candidate_paths = candidate_paths


@dataclass(frozen=True, slots=True)
class _SourceModuleSnapshot:
    """一 generation で実行する Python source 一件の bytes snapshot。"""

    relative_path: str
    source_path: Path
    content: bytes

    def __post_init__(self) -> None:
        if type(self.relative_path) is not str or not self.relative_path:
            raise TypeError("relative_path は空でない str です")
        if not isinstance(self.source_path, Path):
            raise TypeError("source_path は Path です")
        if type(self.content) is not bytes:
            raise TypeError("content は bytes です")


@dataclass(frozen=True, slots=True)
class _SourcePackageSnapshot:
    """main と静的 relative import 依存だけを固定した worker-safe recipe。"""

    main_relative_path: str
    modules: tuple[_SourceModuleSnapshot, ...]
    local_roots: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.main_relative_path) is not str or not self.main_relative_path:
            raise TypeError("main_relative_path は空でない str です")
        if type(self.modules) is not tuple or any(
            type(module) is not _SourceModuleSnapshot for module in self.modules
        ):
            raise TypeError("modules は _SourceModuleSnapshot の tuple です")
        relative_paths = tuple(module.relative_path for module in self.modules)
        if relative_paths != tuple(sorted(relative_paths)):
            raise ValueError("source modules は relative path 順である必要があります")
        if len(set(relative_paths)) != len(relative_paths):
            raise ValueError("source module relative path が重複しています")
        if self.main_relative_path not in relative_paths:
            raise ValueError("main source が package snapshot にありません")
        if type(self.local_roots) is not tuple or any(
            type(name) is not str or not name.isidentifier()
            for name in self.local_roots
        ):
            raise TypeError("local_roots は identifier の tuple です")
        if self.local_roots != tuple(sorted(set(self.local_roots))):
            raise ValueError("local_roots は重複なしの名前順である必要があります")

    @property
    def main_source(self) -> _SourceModuleSnapshot:
        for module in self.modules:
            if module.relative_path == self.main_relative_path:
                return module
        raise RuntimeError("main source snapshot がありません")


def _source_paths_fingerprint(
    main_path: Path,
    paths: tuple[Path, ...],
) -> SourceFingerprint:
    """main と到達可能 helper だけの軽量 stat fingerprint を返す。"""

    if not main_path.is_file():
        return ("missing",)
    root = main_path.parent
    entries: list[tuple[str, int, int, int, int]] = []
    for source_path in sorted(
        set(paths),
        key=lambda item: item.relative_to(root).parts,
    ):
        relative_path = source_path.relative_to(root).as_posix()
        try:
            stat_result = source_path.stat()
        except FileNotFoundError:
            entries.append((relative_path, -1, -1, -1, -1))
            continue
        entries.append(
            (
                relative_path,
                int(stat_result.st_mtime_ns),
                int(stat_result.st_size),
                int(stat_result.st_ctime_ns),
                int(stat_result.st_ino),
            )
        )
    return tuple(entries)


def _local_source_roots(root: Path, *, main_path: Path) -> tuple[str, ...]:
    """absolute local import を拒否するための top-level 候補名だけを返す。"""

    names: set[str] = set()
    for candidate in root.iterdir():
        if candidate == main_path:
            continue
        if candidate.is_file() and candidate.suffix == ".py":
            name = candidate.stem
            if name != "__init__" and name.isidentifier():
                names.add(name)
        elif candidate.is_dir() and candidate.name.isidentifier():
            names.add(candidate.name)
    return tuple(sorted(names))


def _relative_module_source(root: Path, parts: tuple[str, ...]) -> Path | None:
    """relative module parts に対応する .py または package init を返す。"""

    if not parts or any(not part.isidentifier() for part in parts):
        return None
    base = root.joinpath(*parts)
    module_path = base.with_suffix(".py")
    package_path = base / "__init__.py"
    module_exists = module_path.is_file()
    package_exists = package_path.is_file()
    if module_exists and package_exists:
        raise ValueError(
            "source helper の module/package 解決が曖昧です: "
            f"{module_path}, {package_path}"
        )
    if package_exists:
        return package_path
    if module_exists:
        return module_path
    return None


def _module_package_parts(
    relative_path: str,
    *,
    main_relative_path: str,
) -> tuple[str, ...]:
    """source module の relative import 基準 package parts を返す。"""

    if relative_path == main_relative_path:
        return ()
    relative = Path(relative_path)
    if relative.name == "__init__.py":
        return relative.parent.parts
    return relative.with_suffix("").parts[:-1]


def _collect_reachable_source_modules(
    path: Path,
    *,
    main_source_bytes: bytes | None,
) -> tuple[_SourceModuleSnapshot, ...]:
    """静的 relative import を辿り、到達可能 source だけを読み込む。"""

    root = path.parent
    main_relative_path = path.relative_to(root).as_posix()
    pending = [main_relative_path]
    collected: dict[str, _SourceModuleSnapshot] = {}

    def enqueue_source(source_path: Path) -> None:
        relative = source_path.relative_to(root).as_posix()
        if relative not in collected and relative not in pending:
            pending.append(relative)

    def enqueue_module(parts: tuple[str, ...], *, required: bool) -> bool:
        source_path = _relative_module_source(root, parts)
        if source_path is None:
            namespace_path = root.joinpath(*parts)
            if namespace_path.is_dir():
                return True
            if required:
                name = ".".join(parts)
                candidate_base = root.joinpath(*parts)
                raise _SourceDependencyNotFound(
                    name,
                    (
                        candidate_base.with_suffix(".py"),
                        candidate_base / "__init__.py",
                    ),
                )
            return False
        for depth in range(1, len(parts)):
            package_init = root.joinpath(*parts[:depth], "__init__.py")
            if package_init.is_file():
                enqueue_source(package_init)
        enqueue_source(source_path)
        return source_path.name == "__init__.py"

    local_roots = frozenset(_local_source_roots(root, main_path=path))
    while pending:
        relative_path = pending.pop()
        if relative_path in collected:
            continue
        source_path = root / relative_path
        content = (
            main_source_bytes
            if relative_path == main_relative_path and main_source_bytes is not None
            else source_path.read_bytes()
        )
        tree = validate_source_import_policy(content, path=source_path)
        collected[relative_path] = _SourceModuleSnapshot(
            relative_path=relative_path,
            source_path=source_path,
            content=content,
        )
        package_parts = _module_package_parts(
            relative_path,
            main_relative_path=main_relative_path,
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                local_name = next(
                    (
                        alias.name.partition(".")[0]
                        for alias in node.names
                        if alias.name.partition(".")[0] in local_roots
                    ),
                    None,
                )
                if local_name is not None:
                    raise ImportError(
                        "source generation 内の helper は relative import を"
                        f"使用してください: from .{local_name} import ..."
                    )
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level == 0:
                root_name = "" if node.module is None else node.module.partition(".")[0]
                if root_name in local_roots:
                    raise ImportError(
                        "source generation 内の helper は relative import を"
                        f"使用してください: from .{root_name} import ..."
                    )
                continue

            ascend = node.level - 1
            if ascend > len(package_parts):
                raise ImportError("source relative import が package root を越えています")
            anchor = package_parts if ascend == 0 else package_parts[:-ascend]
            module_parts = () if node.module is None else tuple(node.module.split("."))
            target_parts = (*anchor, *module_parts)
            target_is_package = False
            if module_parts:
                target_is_package = enqueue_module(target_parts, required=True)
            for alias in node.names:
                if alias.name == "*":
                    continue
                child_parts = (*target_parts, *alias.name.split("."))
                enqueue_module(
                    child_parts,
                    required=not module_parts,
                )
                if not target_is_package and module_parts:
                    # ``from .module import symbol`` の symbol は通常 submodule でない。
                    break

    return tuple(collected[name] for name in sorted(collected))


def _snapshot_source_package(
    path: Path,
    *,
    main_source_bytes: bytes | None = None,
) -> _SourcePackageSnapshot:
    """到達可能 source を同一 stat fingerprint 間の bytes として固定する。"""

    root = path.parent
    main_relative_path = path.relative_to(root).as_posix()
    for _attempt in range(3):
        discovered = _collect_reachable_source_modules(
            path,
            main_source_bytes=main_source_bytes,
        )
        before = _source_paths_fingerprint(
            path,
            tuple(module.source_path for module in discovered),
        )
        modules = _collect_reachable_source_modules(
            path,
            main_source_bytes=main_source_bytes,
        )
        after = _source_paths_fingerprint(
            path,
            tuple(module.source_path for module in modules),
        )
        if (
            before == after
            and tuple(module.relative_path for module in discovered)
            == tuple(module.relative_path for module in modules)
        ):
            return _SourcePackageSnapshot(
                main_relative_path=main_relative_path,
                modules=modules,
                local_roots=_local_source_roots(root, main_path=path),
            )
    raise RuntimeError("sketch source tree が読み込み中に変更されました")


def _source_module_name(package_name: str, relative_path: str, *, main: bool) -> str:
    """snapshot 内 path を generation 固有 module 名へ変換する。"""

    if main:
        return f"{package_name}.{_ENTRY_MODULE_NAME}"
    relative = Path(relative_path)
    module_path = relative.parent if relative.name == "__init__.py" else relative.with_suffix("")
    parts = module_path.parts
    if not parts:
        return package_name
    if any(not part.isidentifier() for part in parts):
        raise ValueError(
            "source helper path は Python identifier で構成する必要があります: "
            f"{relative_path}"
        )
    return ".".join((package_name, *parts))


def _source_import_guard(local_roots: tuple[str, ...]) -> SnapshotImportGuard:
    roots = frozenset(local_roots)

    def reject_local_absolute_import(name: str, level: int) -> None:
        if level == 0 and name.partition(".")[0] in roots:
            raise ImportError(
                "source generation 内の helper は relative import を使用してください: "
                f"from .{name.partition('.')[0]} import ..."
            )

    return reject_local_absolute_import


def _source_import_plan(
    package_name: str,
    snapshot: _SourcePackageSnapshot,
) -> SnapshotImportPlan:
    modules: list[SnapshotImportModule] = []
    for source in snapshot.modules:
        is_main = source.relative_path == snapshot.main_relative_path
        module_name = _source_module_name(
            package_name,
            source.relative_path,
            main=is_main,
        )
        if module_name == package_name:
            continue
        modules.append(
            SnapshotImportModule(
                name=module_name,
                source=SnapshotModuleSource(
                    path=source.source_path,
                    content=source.content,
                    is_package=(
                        not is_main and Path(source.relative_path).name == "__init__.py"
                    ),
                    canonical_name=(
                        _CANONICAL_SOURCE_PACKAGE
                        + module_name.removeprefix(package_name)
                    ),
                ),
            )
        )
    return SnapshotImportPlan(
        package_names=(package_name,),
        modules=tuple(modules),
        canonical_package_name=_CANONICAL_SOURCE_PACKAGE,
        display_name="grafix-source",
    )


def _preflight_source_package(snapshot: _SourcePackageSnapshot) -> None:
    """復元された worker recipe を含む全 source を実行前に検証する。"""

    for source in snapshot.modules:
        validate_source_import_policy(source.content, path=source.source_path)


def _remove_source_modules(package_name: str | None) -> None:
    if package_name is None:
        return
    remove_snapshot_modules((package_name,))


def _authoring_baseline(config: RuntimeConfig | None) -> AuthoringDefinitionsSnapshot:
    """worker でも再構築できる session baseline を返す。"""

    if config is None:
        return default_session_authoring_definitions()
    if type(config) is not RuntimeConfig:
        raise TypeError("config は exact RuntimeConfig または None です")
    return load_config_authoring_definitions(config)


@dataclass(frozen=True, slots=True)
class SourceReloadResult:
    """1 回の stat/reload 判定結果。"""

    status: ReloadStatus
    generation: int
    draw: Callable[[float], SceneItem]
    definitions: AuthoringDefinitionsSnapshot | None = None
    summary: str | None = None
    details: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "status",
            exact_string_choice(
                self.status,
                name="status",
                choices=("unchanged", "reloaded", "failed"),
            ),
        )
        object.__setattr__(
            self,
            "generation",
            exact_integer(self.generation, name="generation", minimum=-1),
        )
        if not callable(self.draw):
            raise TypeError("draw は callable である必要があります")
        if self.definitions is not None and type(self.definitions) is not AuthoringDefinitionsSnapshot:
            raise TypeError("definitions は exact AuthoringDefinitionsSnapshot または None です")
        for name in ("summary", "details", "source"):
            value = getattr(self, name)
            if value is not None:
                exact_string(value, name=name)


@dataclass(frozen=True, slots=True)
class _RollbackState:
    """worker swap 確定まで保持する直前 generation。"""

    committed_generation: int
    previous_generation: int
    previous_draw: Callable[[float], SceneItem]
    previous_module_name: str | None
    previous_definitions: AuthoringDefinitionsSnapshot


def _validate_draw(module: types.ModuleType, *, attribute: str) -> Callable[[float], SceneItem]:
    try:
        candidate = getattr(module, attribute)
    except AttributeError as exc:
        raise AttributeError(f"sourceに{attribute!r} callableがありません") from exc
    if not callable(candidate):
        raise TypeError(f"source attribute {attribute!r} はcallableである必要があります")
    if inspect.iscoroutinefunction(candidate):
        raise TypeError("drawは同期callableである必要があります")
    try:
        inspect.signature(candidate).bind(0.0)
    except TypeError as exc:
        raise TypeError("drawは時刻tを1つ受け取れるsignatureである必要があります") from exc
    return candidate


def _execute_source_generation(
    *,
    source_package: _SourcePackageSnapshot,
    module_name: str,
    draw_attribute: str,
    baseline: AuthoringDefinitionsSnapshot,
    config: RuntimeConfig | None,
) -> tuple[
    types.ModuleType,
    Callable[[float], SceneItem],
    AuthoringDefinitionsSnapshot,
]:
    """source bytes を candidate target だけへ登録して実行する。"""

    if type(source_package) is not _SourcePackageSnapshot:
        raise TypeError("source_package は exact _SourcePackageSnapshot です")
    _preflight_source_package(source_package)
    target = RegistrationTarget(
        operations=baseline.operations,
        presets=baseline.presets,
    )
    plan = _source_import_plan(module_name, source_package)
    entry_module_name = f"{module_name}.{_ENTRY_MODULE_NAME}"
    with snapshot_import_context(
        plan,
        retain_modules=True,
        import_guard=_source_import_guard(source_package.local_roots),
    ):
        config_scope = (
            contextlib.nullcontext()
            if config is None
            else bind_runtime_config(config)
        )
        with (
            registration_scope(target),
            config_scope,
        ):
            module = importlib.import_module(entry_module_name)
            loaded_draw = _validate_draw(module, attribute=draw_attribute)
        definitions = target.snapshot(recipe=baseline.recipe)
    return module, loaded_draw, definitions


class ReloadedDraw:
    """検証済み source bytes と immutable definition generation の draw。"""

    def __init__(
        self,
        *,
        path: Path,
        source_bytes: bytes,
        module_name: str,
        draw_attribute: str,
        loaded_draw: Callable[[float], SceneItem] | None = None,
        definitions: AuthoringDefinitionsSnapshot | None = None,
        baseline: AuthoringDefinitionsSnapshot | None = None,
        config: RuntimeConfig | None = None,
        source_package: _SourcePackageSnapshot | None = None,
    ) -> None:
        if not isinstance(path, Path):
            raise TypeError("path は Path である必要があります")
        if type(source_bytes) is not bytes:
            raise TypeError("source_bytes は bytes である必要があります")
        module_name = exact_string(module_name, name="module_name")
        draw_attribute = exact_string(draw_attribute, name="draw_attribute")
        if not module_name:
            raise ValueError("module_name は空にできません")
        if not draw_attribute:
            raise ValueError("draw_attribute は空にできません")
        if loaded_draw is not None and not callable(loaded_draw):
            raise TypeError("loaded_draw は callable または None である必要があります")
        if definitions is not None and type(definitions) is not AuthoringDefinitionsSnapshot:
            raise TypeError("definitions は exact AuthoringDefinitionsSnapshot または None です")
        if baseline is not None and type(baseline) is not AuthoringDefinitionsSnapshot:
            raise TypeError("baseline は exact AuthoringDefinitionsSnapshot または None です")
        if config is not None and type(config) is not RuntimeConfig:
            raise TypeError("config は exact RuntimeConfig または None です")
        if source_package is not None and type(source_package) is not _SourcePackageSnapshot:
            raise TypeError(
                "source_package は exact _SourcePackageSnapshot または None です"
            )
        self._path = path
        self._source_bytes = source_bytes
        self._module_name = module_name
        self._draw_attribute = draw_attribute
        self._loaded_draw = loaded_draw
        self._definitions = definitions
        self._baseline = baseline
        self._recipe = (
            baseline.recipe
            if baseline is not None
            else None if definitions is None else definitions.recipe
        )
        self._config = config
        self._source_package = source_package

    def _load_in_current_process(self) -> None:
        baseline = self._baseline
        if baseline is None:
            recipe = self._recipe
            if recipe is None:
                raise RuntimeError(
                    "worker で authoring baseline を再構築する exact recipe がありません"
                )
            baseline = load_authoring_definitions_recipe(recipe)
        source_package = self._source_package
        if source_package is None:
            source_package = _snapshot_source_package(
                self._path,
                main_source_bytes=self._source_bytes,
            )
        _module, draw, definitions = _execute_source_generation(
            source_package=source_package,
            module_name=f"{self._module_name}_worker",
            draw_attribute=self._draw_attribute,
            baseline=baseline,
            config=self._config,
        )
        self._loaded_draw = draw
        self._definitions = definitions
        self._baseline = baseline
        self._source_package = source_package

    def __call__(self, t: float) -> SceneItem:
        render_t = finite_real(t, name="t")
        draw = self._loaded_draw
        definitions = self._definitions
        if draw is not None and definitions is None:
            return draw(render_t)
        if draw is None or definitions is None:
            self._load_in_current_process()
            draw = self._loaded_draw
            definitions = self._definitions
        assert draw is not None
        assert definitions is not None
        with (
            bind_operation_catalog(definitions.operations),
            bind_preset_catalog(definitions.presets),
        ):
            return draw(render_t)

    @property
    def __grafix_source_path__(self) -> Path:
        return self._path

    @property
    def __grafix_source_bytes__(self) -> bytes:
        return self._source_bytes

    @property
    def __grafix_operation_catalog__(self) -> OperationCatalog:
        definitions = self._definitions
        if definitions is None:
            raise RuntimeError("worker 側 source generation はまだ load されていません")
        return definitions.operations

    @property
    def __grafix_preset_catalog__(self) -> PresetCatalog:
        definitions = self._definitions
        if definitions is None:
            raise RuntimeError("worker 側 source generation はまだ load されていません")
        return definitions.presets

    @property
    def __grafix_authoring_definitions__(self) -> AuthoringDefinitionsSnapshot:
        definitions = self._definitions
        if definitions is None:
            raise RuntimeError("worker 側 source generation はまだ load されていません")
        return definitions

    def __getstate__(
        self,
    ) -> tuple[
        Path,
        bytes,
        str,
        str,
        RuntimeConfig | None,
        _SourcePackageSnapshot | None,
        AuthoringDefinitionsRecipe,
    ]:
        """callable/catalog を除く source と baseline recipe を spawn へ渡す。"""

        recipe = self._recipe
        if recipe is None:
            raise TypeError(
                "ReloadedDraw を spawn へ渡すには exact authoring recipe が必要です"
            )
        return (
            self._path,
            self._source_bytes,
            self._module_name,
            self._draw_attribute,
            self._config,
            self._source_package,
            recipe,
        )

    def __setstate__(
        self,
        state: tuple[
            Path,
            bytes,
            str,
            str,
            RuntimeConfig | None,
            _SourcePackageSnapshot | None,
            AuthoringDefinitionsRecipe,
        ],
    ) -> None:
        (
            path,
            source_bytes,
            module_name,
            draw_attribute,
            config,
            source_package,
            recipe,
        ) = state
        ReloadedDraw.__init__(
            self,
            path=path,
            source_bytes=source_bytes,
            module_name=module_name,
            draw_attribute=draw_attribute,
            config=config,
            source_package=source_package,
        )
        self._recipe = recipe


def _unavailable_draw(_t: float) -> SceneItem:
    raise RuntimeError("sketch sourceはまだ正常にloadされていません")


class SourceReloadController:
    """mtime polling で immutable sketch generation を transactional に交換する。"""

    def __init__(
        self,
        path: str | Path,
        *,
        draw_attribute: str = "draw",
        baseline: AuthoringDefinitionsSnapshot | None = None,
        config: RuntimeConfig | None = None,
    ) -> None:
        if isinstance(path, Path):
            path_input = path
        elif type(path) is str:
            path_text = exact_string(path, name="path")
            if not path_text:
                raise ValueError("path は空にできません")
            path_input = Path(path_text)
        else:
            raise TypeError("path は str または Path である必要があります")
        source_path = path_input.expanduser().resolve(strict=False)
        if not source_path.is_file():
            raise FileNotFoundError(f"sketch sourceが見つかりません: {source_path}")
        attribute = exact_string(draw_attribute, name="draw_attribute")
        if not attribute:
            raise ValueError("draw_attributeは空にできません")
        if attribute != attribute.strip():
            raise ValueError("draw_attributeの前後に空白は使用できません")
        if baseline is not None and type(baseline) is not AuthoringDefinitionsSnapshot:
            raise TypeError("baseline は exact AuthoringDefinitionsSnapshot または None です")
        if config is not None and type(config) is not RuntimeConfig:
            raise TypeError("config は exact RuntimeConfig または None です")

        self._path = source_path
        self._draw_attribute = attribute
        self._config = config
        self._namespace_token = hashlib.sha256(
            f"{source_path}\0{id(self)}".encode("utf-8")
        ).hexdigest()[:12]
        self._baseline = _authoring_baseline(config) if baseline is None else baseline
        self._definitions = self._baseline
        self._generation = -1
        self._attempt = 0
        self._module_name: str | None = None
        self._draw: Callable[[float], SceneItem] = _unavailable_draw
        self._rollback_state: _RollbackState | None = None
        self._closed = False
        self._source_paths: tuple[Path, ...] = (source_path,)
        self._last_fingerprint = self._fingerprint()
        result = self._reload(retain_rollback=False)
        if result.status != "reloaded":
            raise RuntimeError(result.summary or "initial sketch load failed")

    def __enter__(self) -> SourceReloadController:
        if self._closed:
            raise RuntimeError("close済みのSourceReloadControllerは再利用できません")
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def draw(self) -> Callable[[float], SceneItem]:
        return self._draw

    @property
    def definitions(self) -> AuthoringDefinitionsSnapshot:
        return self._definitions

    @property
    def baseline(self) -> AuthoringDefinitionsSnapshot:
        """全 source generation の seed に固定した authoring snapshot。"""

        return self._baseline

    @property
    def operation_catalog(self) -> OperationCatalog:
        return self._definitions.operations

    @property
    def preset_catalog(self) -> PresetCatalog:
        return self._definitions.presets

    def _fingerprint(self) -> SourceFingerprint:
        return _source_paths_fingerprint(self._path, self._source_paths)

    def poll(
        self,
        *,
        force: bool = False,
        retain_rollback: bool = False,
    ) -> SourceReloadResult:
        force = exact_bool(force, name="force")
        retain_rollback = exact_bool(retain_rollback, name="retain_rollback")
        if self._closed:
            raise RuntimeError("SourceReloadControllerはclose済みです")
        pending = self._rollback_state
        if pending is not None:
            raise RuntimeError(
                "前回の reload generation が未確定です。"
                "accept_generation() または rollback_generation() を先に呼んでください: "
                f"generation={pending.committed_generation}"
            )
        fingerprint = self._fingerprint()
        if not force and fingerprint == self._last_fingerprint:
            return SourceReloadResult(
                status="unchanged",
                generation=self._generation,
                draw=self._draw,
                definitions=self._definitions,
            )
        self._last_fingerprint = fingerprint
        return self._reload(retain_rollback=retain_rollback)

    def _reload(self, *, retain_rollback: bool) -> SourceReloadResult:
        self._attempt += 1
        previous_module = self._module_name
        previous_draw = self._draw
        previous_generation = self._generation
        previous_definitions = self._definitions
        module_name = f"_grafix_watch_{self._namespace_token}_{self._attempt}"
        source_package: _SourcePackageSnapshot | None = None
        try:
            source_package = _snapshot_source_package(self._path)
            source_bytes = source_package.main_source.content
            _module, loaded_draw, definitions = _execute_source_generation(
                source_package=source_package,
                module_name=module_name,
                draw_attribute=self._draw_attribute,
                baseline=self._baseline,
                config=self._config,
            )
            draw = ReloadedDraw(
                path=self._path,
                source_bytes=source_bytes,
                module_name=module_name,
                draw_attribute=self._draw_attribute,
                loaded_draw=loaded_draw,
                definitions=definitions,
                baseline=self._baseline,
                config=self._config,
                source_package=source_package,
            )
        except BaseException as exc:
            _remove_source_modules(module_name)
            # candidate generation の一時 module は終了操作でも必ず破棄するが、
            # KeyboardInterrupt / SystemExit などの process-control 例外を
            # 通常の reload failure へ変換してはならない。
            if not isinstance(exc, Exception):
                raise
            raw_error_path = getattr(exc, "filename", None)
            if source_package is not None:
                self._source_paths = tuple(
                    dict.fromkeys(
                        (
                            *self._source_paths,
                            *(module.source_path for module in source_package.modules),
                        )
                    )
                )
            else:
                if isinstance(exc, _SourceDependencyNotFound):
                    self._source_paths = tuple(
                        dict.fromkeys(
                            (*self._source_paths, *exc.candidate_paths)
                        )
                    )
                if type(raw_error_path) is str:
                    error_path = Path(raw_error_path).resolve(strict=False)
                    try:
                        error_path.relative_to(self._path.parent)
                    except ValueError:
                        pass
                    else:
                        if error_path.suffix == ".py":
                            self._source_paths = tuple(
                                dict.fromkeys((*self._source_paths, error_path))
                            )
            self._last_fingerprint = self._fingerprint()
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            source = str(self._path)
            raw_error_lineno = getattr(exc, "lineno", None)
            if type(raw_error_path) is str and type(raw_error_lineno) is int:
                source = f"{raw_error_path}:{raw_error_lineno}"
            tb = exc.__traceback__
            while tb is not None:
                try:
                    same_path = Path(tb.tb_frame.f_code.co_filename).resolve(strict=False) == self._path
                except (OSError, RuntimeError, ValueError):
                    same_path = False
                if same_path:
                    source = f"{self._path}:{tb.tb_lineno}"
                tb = tb.tb_next
            summary = f"{type(exc).__name__}: {exc}"
            if previous_generation < 0:
                raise RuntimeError(summary) from exc
            return SourceReloadResult(
                status="failed",
                generation=self._generation,
                draw=self._draw,
                definitions=self._definitions,
                summary=summary,
                details=details,
                source=source,
            )

        assert source_package is not None
        self._module_name = module_name
        self._draw = draw
        self._definitions = definitions
        self._source_paths = tuple(module.source_path for module in source_package.modules)
        self._last_fingerprint = self._fingerprint()
        self._generation += 1
        if retain_rollback and previous_generation >= 0:
            self._rollback_state = _RollbackState(
                committed_generation=self._generation,
                previous_generation=previous_generation,
                previous_draw=previous_draw,
                previous_module_name=previous_module,
                previous_definitions=previous_definitions,
            )
        elif previous_module is not None and previous_module != module_name:
            _remove_source_modules(previous_module)
        return SourceReloadResult(
            status="reloaded",
            generation=self._generation,
            draw=draw,
            definitions=definitions,
            source=str(self._path),
        )

    def accept_generation(self, generation: int) -> None:
        expected = exact_integer(generation, name="generation", minimum=0)
        state = self._rollback_state
        if state is None:
            raise ValueError(
                "accept可能なreload generationではありません: "
                f"current={self._generation}, got={expected}"
            )
        if expected != state.committed_generation or expected != self._generation:
            raise ValueError(
                f"reload generationが一致しません: current={self._generation}, got={expected}"
            )
        previous_module = state.previous_module_name
        if previous_module is not None and previous_module != self._module_name:
            _remove_source_modules(previous_module)
        self._rollback_state = None

    def rollback_generation(self, generation: int) -> Callable[[float], SceneItem]:
        expected = exact_integer(generation, name="generation", minimum=0)
        state = self._rollback_state
        if (
            state is None
            or expected != state.committed_generation
            or expected != self._generation
        ):
            raise ValueError(
                f"rollback可能なreload generationではありません: "
                f"current={self._generation}, got={expected}"
            )
        current_module = self._module_name
        if current_module is not None and current_module != state.previous_module_name:
            _remove_source_modules(current_module)
        self._module_name = state.previous_module_name
        self._draw = state.previous_draw
        self._definitions = state.previous_definitions
        self._generation = state.previous_generation
        self._rollback_state = None
        return self._draw

    def close(self) -> None:
        """一時 module を解放する。process-global registry は変更しない。"""

        if self._closed:
            return
        self._closed = True
        if self._rollback_state is not None:
            self.accept_generation(self._generation)
        module_name = self._module_name
        self._module_name = None
        _remove_source_modules(module_name)


_CURRENT_SOURCE_RELOAD: ContextVar[SourceReloadController | None] = ContextVar(
    "grafix_current_source_reload",
    default=None,
)


@contextlib.contextmanager
def source_reload_context(
    controller: SourceReloadController,
) -> Iterator[SourceReloadController]:
    if not isinstance(controller, SourceReloadController):
        raise TypeError("controllerはSourceReloadControllerである必要があります")
    token = _CURRENT_SOURCE_RELOAD.set(controller)
    try:
        yield controller
    finally:
        _CURRENT_SOURCE_RELOAD.reset(token)


def current_source_reload() -> SourceReloadController | None:
    return _CURRENT_SOURCE_RELOAD.get()


__all__ = [
    "ReloadStatus",
    "ReloadedDraw",
    "SourceReloadController",
    "SourceReloadResult",
    "current_source_reload",
    "source_reload_context",
]
