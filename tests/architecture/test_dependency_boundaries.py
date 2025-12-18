"""依存境界（core/export/interactive）の破りを検出するテスト。"""

from __future__ import annotations

import ast
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

    return modules


def _assert_no_forbidden_imports(
    *,
    root: Path,
    forbidden_prefixes: tuple[str, ...],
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

        bad = sorted([m for m in modules if m.startswith(forbidden_prefixes)])
        if bad:
            violations.append(f"{rel}: {', '.join(bad)}")

    if violations:
        joined = "\n".join(violations)
        raise AssertionError(f"依存境界違反の import を検出:\n{joined}")


def test_core_does_not_depend_on_export_or_interactive() -> None:
    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "grafix" / "core",
        forbidden_prefixes=("grafix.export", "grafix.interactive", "pyglet", "moderngl", "imgui"),
    )


def test_export_does_not_depend_on_interactive() -> None:
    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "grafix" / "export",
        forbidden_prefixes=("grafix.interactive", "pyglet", "moderngl", "imgui"),
    )


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

    node = _parse_single_stmt("from grafix import export\n")
    assert isinstance(node, ast.ImportFrom)
    got = _resolve_importfrom_targets(
        current_module="grafix.core.pipeline",
        is_package=False,
        node=node,
    )
    assert "grafix.export" in got

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
