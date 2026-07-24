"""依存境界（core/export/interactive）の破りを検出するテスト。"""

from __future__ import annotations

import ast
from graphlib import CycleError, TopologicalSorter
from pathlib import Path


def _repo_root() -> Path:
    path = Path(__file__).resolve()
    for parent in path.parents:
        if (parent / "src").is_dir() and (parent / "tests").is_dir():
            return parent
    raise RuntimeError("repo root が見つからない")


def _iter_py_files(root: Path) -> list[Path]:
    return sorted([p for p in root.rglob("*.py") if p.is_file()])


def _module_name_for_path(*, path: Path, src_root: Path) -> tuple[str, bool]:
    rel = path.relative_to(src_root)
    parts = list(rel.parts)
    if not parts or not parts[-1].endswith(".py"):
        raise ValueError(f"python ファイルではない: {rel}")

    is_package = parts[-1] == "__init__.py"
    if is_package:
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")

    if not parts:
        raise ValueError(f"src 直下の __init__.py はモジュール名にできない: {rel}")
    return ".".join(parts), is_package


def _resolve_importfrom_targets(
    *,
    current_module: str,
    is_package: bool,
    node: ast.ImportFrom,
) -> set[str]:
    level = int(node.level or 0)
    if level == 0:
        if node.module is None:
            return set()
        base = str(node.module)
        targets = {base}
        for alias in node.names:
            if alias.name != "*":
                targets.add(f"{base}.{alias.name}")
        return targets

    current_package = current_module if is_package else current_module.rsplit(".", 1)[0]
    up = level - 1
    parts = current_package.split(".")
    if up > len(parts):
        raise ValueError(
            "相対 import の解決に失敗: "
            f"current_module={current_module!r}, is_package={is_package}, "
            f"level={level}, module={node.module!r}"
        )

    base_parts = parts[: len(parts) - up]
    if not base_parts:
        raise ValueError(
            "相対 import の解決に失敗: "
            f"current_module={current_module!r}, is_package={is_package}, "
            f"level={level}, module={node.module!r}"
        )

    base = ".".join(base_parts)
    if node.module is not None:
        base = f"{base}.{node.module}"

    targets = {base}
    for alias in node.names:
        if alias.name != "*":
            targets.add(f"{base}.{alias.name}")
    return targets


def _constant_importlib_module(node: ast.Call) -> str | None:
    """canonical ``importlib.import_module`` の定数 module 名だけを返す。"""

    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
    ):
        return None

    argument: ast.expr | None = node.args[0] if node.args else None
    if argument is None:
        argument = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "name"),
            None,
        )
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _import_modules_in_file(*, path: Path, src_root: Path) -> set[str]:
    current_module, is_package = _module_name_for_path(path=path, src_root=src_root)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(str(alias.name))
            continue
        if isinstance(node, ast.ImportFrom):
            targets = _resolve_importfrom_targets(
                current_module=current_module,
                is_package=is_package,
                node=node,
            )
            modules.update(targets)
            continue
        if isinstance(node, ast.Call):
            module = _constant_importlib_module(node)
            if module is not None:
                modules.add(module)

    return modules


def _is_forbidden_import(
    module: str,
    *,
    forbidden_prefixes: tuple[str, ...],
    forbidden_exact: tuple[str, ...],
) -> bool:
    return module in forbidden_exact or module.startswith(forbidden_prefixes)


def _assert_no_forbidden_imports(
    *,
    root: Path,
    forbidden_prefixes: tuple[str, ...],
    forbidden_exact: tuple[str, ...] = (),
) -> None:
    repo_root = _repo_root()
    src_root = repo_root / "src"
    violations: list[str] = []
    for path in _iter_py_files(root):
        rel = path.relative_to(repo_root)
        try:
            modules = _import_modules_in_file(path=path, src_root=src_root)
        except ValueError as e:
            violations.append(f"{rel}: {e}")
            continue

        bad = sorted(
            [
                module
                for module in modules
                if _is_forbidden_import(
                    module,
                    forbidden_prefixes=forbidden_prefixes,
                    forbidden_exact=forbidden_exact,
                )
            ]
        )
        if bad:
            violations.append(f"{rel}: {', '.join(bad)}")

    if violations:
        joined = "\n".join(violations)
        raise AssertionError(f"依存境界違反の import を検出:\n{joined}")


def test_core_does_not_depend_on_api_export_or_interactive() -> None:
    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "grafix" / "core",
        forbidden_prefixes=(
            "grafix.api",
            "grafix.export",
            "grafix.file_io",
            "grafix.interactive",
            "grafix.parameter_storage",
            "subprocess",
            "tempfile",
            "pyglet",
            "moderngl",
            "imgui",
        ),
        forbidden_exact=("grafix",),
    )


def test_core_authoring_values_do_not_own_source_import_io() -> None:
    """core は recipe/snapshot 値を持つだけで source 発見・実行を行わない。"""

    root = _repo_root()
    src_root = root / "src"
    core = src_root / "grafix" / "core"
    forbidden_imports = {
        "grafix._snapshot_import",
        "grafix.authoring_loader",
    }
    forbidden_call_names = {"compile", "exec", "open"}
    authoring_io_method_names = {
        "glob",
        "read_bytes",
        "read_text",
    }
    source_tree_walk_methods = {"iterdir", "rglob"}
    violations: list[str] = []

    for path in _iter_py_files(core):
        rel = path.relative_to(root)
        imported = _import_modules_in_file(path=path, src_root=src_root)
        for module in sorted(
            module
            for module in imported
            if module in forbidden_imports
        ):
            violations.append(f"{rel}: import {module}")

        owns_authoring_values = "authoring" in path.stem or any(
            module
            in {
                "grafix.core.authoring_definitions",
                "grafix.core.authoring_recipe",
            }
            for module in imported
        )

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id in forbidden_call_names
                    and (node.func.id != "open" or owns_authoring_values)
                ):
                    violations.append(f"{rel}:{node.lineno}: {node.func.id}()")
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in source_tree_walk_methods
                ):
                    violations.append(f"{rel}:{node.lineno}: .{node.func.attr}()")
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "walk"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                ):
                    violations.append(f"{rel}:{node.lineno}: os.walk()")
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in authoring_io_method_names
                    and owns_authoring_values
                ):
                    violations.append(f"{rel}:{node.lineno}: .{node.func.attr}()")
                elif (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr
                    in {"append", "clear", "insert", "pop", "remove", "update"}
                    and isinstance(node.func.value, ast.Attribute)
                    and isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id == "sys"
                    and node.func.value.attr in {"meta_path", "modules"}
                ):
                    violations.append(
                        f"{rel}:{node.lineno}: mutate sys.{node.func.value.attr}"
                    )
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            targets: list[ast.expr]
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                targets = [node.target]
            for target in targets:
                owner = target.value if isinstance(target, ast.Subscript) else target
                if (
                    isinstance(owner, ast.Attribute)
                    and isinstance(owner.value, ast.Name)
                    and owner.value.id == "sys"
                    and owner.attr in {"meta_path", "modules"}
                ):
                    violations.append(
                        f"{rel}:{node.lineno}: mutate sys.{owner.attr}"
                    )

    assert not violations, "core authoring source I/O を検出:\n" + "\n".join(
        violations
    )


def test_legacy_core_authoring_loader_is_deleted_and_unreferenced() -> None:
    """filesystem/process owner を core へ戻す compatibility shim を禁止する。"""

    root = _repo_root()
    src_root = root / "src"
    legacy_name = ".".join(("grafix", "core", "authoring_loader"))
    assert not (src_root / "grafix" / "core" / "authoring_loader.py").exists()

    violations: list[str] = []
    for path in _iter_py_files(src_root / "grafix"):
        imported = _import_modules_in_file(path=path, src_root=src_root)
        if any(
            module == legacy_name or module.startswith(f"{legacy_name}.")
            for module in imported
        ):
            violations.append(str(path.relative_to(root)))

    assert not violations, "旧 core authoring loader import を検出:\n" + "\n".join(
        violations
    )


def test_core_does_not_implement_publish_or_path_allocation_policy() -> None:
    """domain layer から filesystem mutation と capture path policy を排除する。"""

    root = _repo_root()
    violations: list[str] = []
    core_root = root / "src" / "grafix" / "core"
    forbidden_names = {
        "VersionedPathAllocator",
        "atomic_write_text",
        "_publish_capture_generation",
        "publish_capture_generation",
        "capture_manifest_path_for",
    }
    forbidden_os_calls = {
        "fsync",
        "link",
        "makedirs",
        "mkdir",
        "remove",
        "removedirs",
        "rename",
        "replace",
        "rmdir",
        "symlink",
        "unlink",
    }
    forbidden_mutating_methods = {
        "chmod",
        "hardlink_to",
        "mkdir",
        "rename",
        "rmdir",
        "symlink_to",
        "unlink",
        "write_bytes",
        "write_text",
    }
    for path in _iter_py_files(core_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: {node.id}"
                )
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            owner = node.func.value
            if (
                isinstance(owner, ast.Name)
                and owner.id == "os"
                and node.func.attr in forbidden_os_calls
            ):
                violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: os.{node.func.attr}"
                )
            if node.func.attr in forbidden_mutating_methods:
                violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: .{node.func.attr}()"
                )

    assert not violations, "core filesystem policy を検出:\n" + "\n".join(
        violations
    )


def test_legacy_core_parameter_persistence_module_is_deleted() -> None:
    root = _repo_root()
    assert not (
        root / "src" / "grafix" / "core" / "parameters" / "persistence.py"
    ).exists()


def test_runtime_config_core_is_pure_and_loader_imports_are_one_way() -> None:
    """core value/parser へ YAML・探索・loader convenience を戻さない。"""

    root = _repo_root()
    core_path = root / "src" / "grafix" / "core" / "runtime_config.py"
    loader_path = root / "src" / "grafix" / "runtime_config_loader.py"
    assert loader_path.is_file()

    tree = ast.parse(core_path.read_text(encoding="utf-8"))
    forbidden_modules = {"yaml", "traceback", "importlib.resources"}
    forbidden_calls = {
        "cwd",
        "home",
        "is_file",
        "read_bytes",
        "read_text",
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden_modules or alias.name == "os":
                    violations.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in forbidden_modules or module == "importlib":
                violations.append(f"{node.lineno}: from {module} import")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_calls
        ):
            violations.append(f"{node.lineno}: .{node.func.attr}()")
    assert not violations, "runtime_config core I/O を検出:\n" + "\n".join(violations)

    loader_names = {
        "load_runtime_config",
        "load_runtime_config_report",
        "output_root_dir",
        "runtime_config",
        "runtime_config_report",
        "runtime_config_with_fallback",
    }
    import_violations: list[str] = []
    for path in _iter_py_files(root / "src" / "grafix"):
        if path == loader_path:
            continue
        source_tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(source_tree):
            if not (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module == "grafix.core.runtime_config"
            ):
                continue
            bad = sorted(alias.name for alias in node.names if alias.name in loader_names)
            if bad:
                import_violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: {', '.join(bad)}"
                )
    assert not import_violations, "旧 loader import を検出:\n" + "\n".join(
        import_violations
    )


def test_low_level_evaluation_does_not_observe_full_runtime_config() -> None:
    """DAG/font slice は RuntimeConfig value や ambient accessor を参照しない。"""

    root = _repo_root()
    paths = (
        root / "src" / "grafix" / "core" / "evaluation_config.py",
        root / "src" / "grafix" / "core" / "evaluation_context.py",
        root / "src" / "grafix" / "core" / "font_resolver.py",
        root / "src" / "grafix" / "core" / "font_resources.py",
        root / "src" / "grafix" / "core" / "primitives" / "text.py",
    )
    forbidden = {"RuntimeConfig", "current_runtime_config"}
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden:
                violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: {node.id}"
                )
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in forbidden:
                        violations.append(
                            f"{path.relative_to(root)}:{node.lineno}: {alias.name}"
                        )
    assert not violations, "full RuntimeConfig 参照を検出:\n" + "\n".join(violations)


def test_parameters_do_not_depend_on_operation_registries() -> None:
    """parameter domain は application から schema snapshot を受け取る。"""

    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "grafix" / "core" / "parameters",
        forbidden_prefixes=(
            "grafix.core.effect_registry",
            "grafix.core.primitive_registry",
            "grafix.core.op_registry",
            "grafix.core.preset_registry",
        ),
    )


def test_legacy_registry_modules_are_deleted_and_never_imported() -> None:
    """authoring/runtime は旧 process-global registry へ戻れない。"""

    root = _repo_root()
    legacy_modules = {
        "grafix.core.effect_registry",
        "grafix.core.op_registry",
        "grafix.core.preset_registry",
        "grafix.core.primitive_registry",
    }
    core = root / "src" / "grafix" / "core"
    assert all(
        not (core / f"{module.rsplit('.', 1)[1]}.py").exists()
        for module in legacy_modules
    )

    violations: list[str] = []
    for source_root in (root / "src" / "grafix", root / "tests"):
        for path in _iter_py_files(source_root):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported: set[str] = set()
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    if node.module is not None:
                        imported.add(node.module)
                elif isinstance(node, ast.Call):
                    module = _constant_importlib_module(node)
                    if module is not None:
                        imported.add(module)
                for module in sorted(imported & legacy_modules):
                    violations.append(
                        f"{path.relative_to(root)}:{node.lineno}: {module}"
                    )

    assert not violations, "legacy registry import を検出:\n" + "\n".join(violations)


def test_authoring_has_one_registration_path_and_no_legacy_global_store() -> None:
    """旧 live registry API と新たな module-global builder を復活させない。"""

    root = _repo_root()
    source_root = root / "src" / "grafix"
    legacy_symbols = {
        "BuiltinOpCatalog",
        "OpCatalogEntry",
        "OpRegistry",
        "OpSpec",
        "PresetRegistry",
        "PresetSpec",
        "RegistryRevision",
        "_AUTOLOAD_KEY",
        "_registry_revision",
        "builtin_effect_catalog",
        "builtin_primitive_catalog",
        "current_registry_revision",
        "effect_registry",
        "preset_registry",
        "primitive_registry",
        "registry_revision",
        "replace_all",
    }
    mutable_builder_factories = {
        "DefaultAuthoringDefinitions",
        "OperationCatalogBuilder",
        "PresetCatalogBuilder",
        "RegistrationTarget",
    }
    allowed_global_builder = (
        Path("src/grafix/core/authoring_definitions.py"),
        "default_authoring_definitions",
        "DefaultAuthoringDefinitions",
    )
    expected_decorator_definitions = {
        (Path("src/grafix/api/preset.py"), "preset"),
        (Path("src/grafix/core/operation_authoring.py"), "effect"),
        (Path("src/grafix/core/operation_authoring.py"), "primitive"),
    }

    violations: list[str] = []
    decorator_definitions: set[tuple[Path, str]] = set()
    for path in _iter_py_files(source_root):
        rel = path.relative_to(root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in {"primitive", "effect", "preset"}:
                    decorator_definitions.add((rel, node.name))
            if isinstance(node, ast.Name) and node.id in legacy_symbols:
                violations.append(f"{rel}:{node.lineno}: {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in legacy_symbols:
                violations.append(f"{rel}:{node.lineno}: {node.attr}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imported_name = alias.name.rsplit(".", 1)[-1]
                    if imported_name in legacy_symbols:
                        violations.append(
                            f"{rel}:{node.lineno}: import {imported_name}"
                        )

        # Catalog builder/registration target を module global に置くと、session
        # snapshot ではなく live process state へ戻る。唯一の例外は公開
        # decorator convenience 用 DefaultAuthoringDefinitions そのものだけ。
        for statement in tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = statement.value
            if not isinstance(value, ast.Call):
                continue
            called = value.func
            factory = (
                called.id
                if isinstance(called, ast.Name)
                else called.attr
                if isinstance(called, ast.Attribute)
                else None
            )
            if factory not in mutable_builder_factories:
                continue
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            for target in targets:
                target_name = target.id if isinstance(target, ast.Name) else ast.unparse(target)
                if (rel, target_name, factory) != allowed_global_builder:
                    violations.append(
                        f"{rel}:{statement.lineno}: global {target_name} = {factory}(...)"
                    )

    assert decorator_definitions == expected_decorator_definitions
    assert not violations, "legacy/global registration state を検出:\n" + "\n".join(
        violations
    )


def test_export_does_not_depend_on_api_or_interactive() -> None:
    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "grafix" / "export",
        forbidden_prefixes=(
            "grafix.api",
            "grafix.interactive",
            "pyglet",
            "moderngl",
            "imgui",
        ),
        forbidden_exact=("grafix",),
    )


def test_interactive_does_not_depend_on_api() -> None:
    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "grafix" / "interactive",
        forbidden_prefixes=("grafix.api",),
        forbidden_exact=("grafix",),
    )


def test_interactive_leaf_packages_do_not_depend_on_composition_or_config_discovery() -> None:
    """GL/MIDI/GUI leaf を runtime composition と config 探索へ逆依存させない。"""

    root = _repo_root()
    for package in ("gl", "midi", "parameter_gui"):
        _assert_no_forbidden_imports(
            root=root / "src" / "grafix" / "interactive" / package,
            forbidden_prefixes=(
                "grafix.interactive.runtime",
                "grafix.runtime_config_loader",
            ),
        )


def test_runtime_does_not_reach_through_renderer_context() -> None:
    """framebuffer operation は DrawRenderer の明示 API だけを通す。"""

    root = _repo_root()
    violations: list[str] = []
    runtime_root = root / "src" / "grafix" / "interactive" / "runtime"
    for path in _iter_py_files(runtime_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "ctx":
                continue
            owner = node.value
            if isinstance(owner, ast.Name) and "renderer" in owner.id:
                violations.append(f"{path.relative_to(root)}:{node.lineno}")
            elif isinstance(owner, ast.Attribute) and "renderer" in owner.attr:
                violations.append(f"{path.relative_to(root)}:{node.lineno}")

    assert not violations, "renderer context への直接到達を検出:\n" + "\n".join(
        violations
    )


def test_coordinators_delegate_mutation_and_platform_capabilities() -> None:
    """DWS、GUI、runner を順序と配線だけの coordinator に保つ。"""

    root = _repo_root()
    src_root = root / "src"
    targets = {
        "dws": src_root
        / "grafix"
        / "interactive"
        / "runtime"
        / "draw_window_system.py",
        "gui": src_root
        / "grafix"
        / "interactive"
        / "parameter_gui"
        / "gui.py",
        "runner": src_root / "grafix" / "api" / "runner.py",
    }
    violations: list[str] = []
    dws_tree = ast.parse(targets["dws"].read_text(encoding="utf-8"))
    forbidden_publish_calls = {
        "fsync",
        "link",
        "mkdir",
        "rename",
        "replace",
        "rmdir",
        "symlink_to",
        "unlink",
        "write_bytes",
        "write_text",
    }
    for node in ast.walk(dws_tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_publish_calls
        ):
            violations.append(
                f"{targets['dws'].relative_to(root)}:{node.lineno}: "
                f"{node.func.attr}()"
            )

    dws_imports = _import_modules_in_file(path=targets["dws"], src_root=src_root)
    for module in sorted(dws_imports & {"grafix.file_io", "shutil", "tempfile"}):
        violations.append(f"{targets['dws'].relative_to(root)}: {module}")

    runner_imports = _import_modules_in_file(
        path=targets["runner"],
        src_root=src_root,
    )
    for module in sorted(runner_imports):
        if module in {"AppKit", "Cocoa", "Foundation"}:
            violations.append(f"{targets['runner'].relative_to(root)}: {module}")

    gui_imports = _import_modules_in_file(
        path=targets["gui"],
        src_root=src_root,
    )
    for module in sorted(gui_imports):
        if module in {
            "grafix.core.parameters.variations",
            "grafix.core.parameters.ui_ops",
            "grafix.core.parameters.midi_ops",
            "grafix.core.parameters.effect_order_ops",
        }:
            violations.append(f"{targets['gui'].relative_to(root)}: {module}")

    assert not violations, "coordinator への責務逆流を検出:\n" + "\n".join(
        violations
    )


def test_api_and_interactive_do_not_access_param_store_private_state() -> None:
    """composition 外から ParamStore の live container へ到達させない。"""

    root = _repo_root()
    forbidden_names = {
        "_runtime_ref",
        "_variations_ref",
        "_collapsed_headers_ref",
        "_locked_keys_ref",
        "_favorite_keys_ref",
        "_get_state_ref",
        "_snapshot_cache",
        "_touch",
    }
    forbidden_prefixes = ("_observe_history_",)
    violations: list[str] = []

    def is_store_expression(node: ast.expr) -> bool:
        if isinstance(node, ast.Name):
            return node.id == "store" or node.id.endswith("_store")
        if isinstance(node, ast.Attribute):
            return node.attr in {"store", "_store"}
        return False

    for source_root in (
        root / "src" / "grafix" / "api",
        root / "src" / "grafix" / "interactive",
    ):
        for path in _iter_py_files(source_root):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "vars" and node.args:
                        argument = node.args[0]
                        if is_store_expression(argument):
                            violations.append(
                                f"{path.relative_to(root)}:{node.lineno}: vars(store)"
                            )
                if not isinstance(node, ast.Attribute):
                    continue
                name = node.attr
                if name in forbidden_names or name.startswith(forbidden_prefixes):
                    violations.append(
                        f"{path.relative_to(root)}:{node.lineno}: {name}"
                    )

    assert not violations, "ParamStore private state access を検出:\n" + "\n".join(
        violations
    )


def test_effect_modules_do_not_import_sibling_effects() -> None:
    root = _repo_root()
    src_root = root / "src"
    effects_root = src_root / "grafix" / "core" / "effects"
    violations: list[str] = []

    for path in sorted(effects_root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        current = f"grafix.core.effects.{path.stem}"
        imported = _import_modules_in_file(path=path, src_root=src_root)
        siblings = sorted(
            module
            for module in imported
            if (
                module == "grafix.core.effects"
                or module.startswith("grafix.core.effects.")
            )
            and module != current
            and not module.startswith(f"{current}.")
        )
        if siblings:
            violations.append(f"{path.relative_to(root)}: {', '.join(siblings)}")

    assert not violations, "effect 間の直接 import を検出:\n" + "\n".join(violations)


def test_geometry_kernel_import_graph_is_acyclic_and_does_not_depend_on_effects(
) -> None:
    root = _repo_root()
    src_root = root / "src"
    kernels_root = src_root / "grafix" / "core" / "geometry_kernels"
    kernel_modules = {
        f"grafix.core.geometry_kernels.{path.stem}"
        for path in kernels_root.glob("*.py")
        if path.name != "__init__.py"
    }
    graph: dict[str, set[str]] = {}
    effect_imports: list[str] = []

    for path in sorted(kernels_root.glob("*.py")):
        current, _is_package = _module_name_for_path(path=path, src_root=src_root)
        imported = _import_modules_in_file(path=path, src_root=src_root)
        graph[current] = {
            candidate
            for candidate in kernel_modules
            if any(
                module == candidate or module.startswith(f"{candidate}.")
                for module in imported
            )
        }
        bad = sorted(
            module
            for module in imported
            if module == "grafix.core.effects"
            or module.startswith("grafix.core.effects.")
        )
        effect_imports.extend(f"{path.relative_to(root)}: {module}" for module in bad)

    assert not effect_imports, "kernel から effect への import を検出:\n" + "\n".join(
        effect_imports
    )
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise AssertionError(f"geometry kernel の import cycle を検出: {exc}") from exc


def _parse_single_stmt(source: str) -> ast.stmt:
    tree = ast.parse(source)
    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.stmt)
    return tree.body[0]


def test__resolve_importfrom_targets_handles_relative_imports() -> None:
    node = _parse_single_stmt("from ..export import svg\n")
    assert isinstance(node, ast.ImportFrom)
    got = _resolve_importfrom_targets(
        current_module="grafix.core.pipeline",
        is_package=False,
        node=node,
    )
    assert "grafix.export" in got
    assert "grafix.export.svg" in got

    node = _parse_single_stmt("from .. import interactive\n")
    assert isinstance(node, ast.ImportFrom)
    got = _resolve_importfrom_targets(
        current_module="grafix.core.pipeline",
        is_package=False,
        node=node,
    )
    assert "grafix.interactive" in got

    node = _parse_single_stmt("from grafix import save\n")
    assert isinstance(node, ast.ImportFrom)
    got = _resolve_importfrom_targets(
        current_module="grafix.core.pipeline",
        is_package=False,
        node=node,
    )
    assert "grafix" in got
    assert "grafix.save" in got

    node = _parse_single_stmt("from . import context\n")
    assert isinstance(node, ast.ImportFrom)
    got = _resolve_importfrom_targets(
        current_module="grafix.core.parameters.resolver",
        is_package=False,
        node=node,
    )
    assert "grafix.core.parameters.context" in got

    node = _parse_single_stmt("from ..export import *\n")
    assert isinstance(node, ast.ImportFrom)
    got = _resolve_importfrom_targets(
        current_module="grafix.core.pipeline",
        is_package=False,
        node=node,
    )
    assert got == {"grafix.export"}

    node = _parse_single_stmt("from grafix import *\n")
    assert isinstance(node, ast.ImportFrom)
    got = _resolve_importfrom_targets(
        current_module="grafix.core.pipeline",
        is_package=False,
        node=node,
    )
    assert got == {"grafix"}


def test__import_modules_in_file_detects_constant_dynamic_and_root_imports(
    tmp_path: Path,
) -> None:
    src_root = tmp_path / "src"
    path = src_root / "grafix" / "interactive" / "sample.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "import importlib",
                'importlib.import_module("grafix.api.render")',
                'importlib.import_module(name="grafix.api.preset")',
                "from grafix import G, api, interactive, run, save as save_frame",
                "",
            ]
        ),
        encoding="utf-8",
    )

    modules = _import_modules_in_file(path=path, src_root=src_root)

    assert {
        "grafix",
        "grafix.G",
        "grafix.api",
        "grafix.save",
        "grafix.interactive",
        "grafix.run",
        "grafix.api.render",
        "grafix.api.preset",
    } <= modules


def test__is_forbidden_import_handles_root_exact_without_blocking_core() -> None:
    kwargs = {
        "forbidden_prefixes": ("grafix.api",),
        "forbidden_exact": ("grafix",),
    }

    assert _is_forbidden_import("grafix", **kwargs)
    assert _is_forbidden_import("grafix.api.render", **kwargs)
    assert not _is_forbidden_import("grafix.core.geometry", **kwargs)


def test__resolve_importfrom_targets_rejects_unresolvable_relative_imports() -> None:
    node = _parse_single_stmt("from ...export import svg\n")
    assert isinstance(node, ast.ImportFrom)
    try:
        _resolve_importfrom_targets(
            current_module="grafix.core",
            is_package=True,
            node=node,
        )
    except ValueError:
        return
    raise AssertionError("解決不能な相対 import は ValueError にする")
