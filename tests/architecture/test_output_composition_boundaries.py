from __future__ import annotations

import ast
from pathlib import Path


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_export_modules_do_not_discover_runtime_config() -> None:
    export_root = Path("src/grafix/export")
    violations = [
        path
        for path in export_root.rglob("*.py")
        if "grafix.runtime_config_loader" in _imports(path)
    ]

    assert violations == []


def test_variation_batch_transaction_dependency_points_from_api_to_export() -> None:
    api_path = Path("src/grafix/api/variation_batch.py")
    export_path = Path("src/grafix/export/variation_batch.py")

    api_imports = _imports(api_path)
    export_imports = _imports(export_path)

    assert "grafix.export.variation_batch" in api_imports
    assert not {
        module
        for module in export_imports
        if module == "grafix.api" or module.startswith("grafix.api.")
    }


def test_variation_batch_api_has_no_publish_or_staging_capability() -> None:
    """API は render callback を組み立て、filesystem transaction は export に委譲する。"""

    path = Path("src/grafix/api/variation_batch.py")
    imports = _imports(path)
    forbidden_imports = {
        "html",
        "json",
        "os",
        "shutil",
        "tempfile",
        "grafix.file_io",
        "grafix.export.capture_publish",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_calls = {
        "atomic_write_text",
        "atomic_write_text_no_clobber",
        "fsync",
        "link",
        "mkdtemp",
        "publish_capture_generation",
        "rename",
        "replace",
        "rmtree",
    }
    capabilities = {
        node.func.id
        if isinstance(node.func, ast.Name)
        else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }

    assert not imports & forbidden_imports
    assert not capabilities & forbidden_calls


def test_interactive_leaf_modules_do_not_depend_on_export_services() -> None:
    leaf_roots = (
        Path("src/grafix/interactive/gl"),
        Path("src/grafix/interactive/midi"),
        Path("src/grafix/interactive/parameter_gui"),
    )
    violations = [
        (path, module)
        for root in leaf_roots
        for path in root.rglob("*.py")
        for module in _imports(path)
        if module == "grafix.export" or module.startswith("grafix.export.")
    ]

    assert violations == []
