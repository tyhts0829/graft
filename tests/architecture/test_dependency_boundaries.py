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


def _import_modules_in_file(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(str(alias.name))
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level and int(node.level) > 0:
                continue
            if node.module is None:
                continue
            modules.add(str(node.module))

    return modules


def _assert_no_forbidden_imports(
    *,
    root: Path,
    forbidden_prefixes: tuple[str, ...],
) -> None:
    violations: list[str] = []
    for path in _iter_py_files(root):
        modules = _import_modules_in_file(path)
        bad = sorted([m for m in modules if m.startswith(forbidden_prefixes)])
        if bad:
            rel = path.relative_to(_repo_root())
            violations.append(f"{rel}: {', '.join(bad)}")

    if violations:
        joined = "\n".join(violations)
        raise AssertionError(f"依存境界違反の import を検出:\n{joined}")


def test_core_does_not_depend_on_export_or_interactive() -> None:
    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "core",
        forbidden_prefixes=("src.export", "src.interactive", "pyglet", "moderngl", "imgui"),
    )


def test_export_does_not_depend_on_interactive() -> None:
    root = _repo_root()
    _assert_no_forbidden_imports(
        root=root / "src" / "export",
        forbidden_prefixes=("src.interactive", "pyglet", "moderngl", "imgui"),
    )
