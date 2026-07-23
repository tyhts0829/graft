from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PARAMETERS = ROOT / "src" / "grafix" / "core" / "parameters"
BENCHMARK = (
    ROOT
    / "src"
    / "grafix"
    / "devtools"
    / "benchmarks"
    / "parameter_hotpath_benchmark.py"
)

_FORBIDDEN_STORE_ATTRIBUTES = frozenset(
    {
        "_states",
        "_meta",
        "_explicit_by_key",
        "_labels",
        "_ordinals",
        "_effects",
        "_collapsed_headers",
        "_locked_keys",
        "_favorite_keys_data",
        "_variations",
        "_runtime",
        "_get_state_ref",
        "_get_explicit_ref",
        "_labels_ref",
        "_ordinals_ref",
        "_effects_ref",
        "_collapsed_headers_ref",
        "_locked_keys_ref",
        "_favorite_keys_ref",
        "_variations_ref",
        "_runtime_ref",
        "_touch",
        "_touch_favorites",
        "_begin_mutation_batch",
        "_end_mutation_batch",
        "_ensure_state",
        "_set_meta",
        "_set_explicit",
        "_replace_favorite_keys",
        "_load_persisted_parameters",
    }
)

_REMOVED_STORE_METHODS = _FORBIDDEN_STORE_ATTRIBUTES | {
    "_commit_mutation",
}
_REMOVED_STORE_CLASSES = {
    "_CollapsedHeaderSet",
    "_FavoriteKeySet",
    "_PendingStoreMutation",
}


def test_parameter_modules_do_not_bypass_store_ports() -> None:
    """Sibling module は raw live container/revision primitive を呼ばない。"""

    paths = [
        path
        for path in PARAMETERS.glob("*.py")
        if path.name != "store.py"
    ]
    paths.append(BENCHMARK)
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "store"
                and node.attr in _FORBIDDEN_STORE_ATTRIBUTES
            ):
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: store.{node.attr}"
                )

    assert violations == []


def test_legacy_store_mutation_surface_is_removed() -> None:
    """移行用 mutable view / touch / batch API を store 定義へ戻さない。"""

    path = PARAMETERS / "store.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert definitions.isdisjoint(_REMOVED_STORE_METHODS)
    assert definitions.isdisjoint(_REMOVED_STORE_CLASSES)
