"""Benchmark package の一方向 composition graph を固定する。"""

from __future__ import annotations

import ast
from graphlib import CycleError, TopologicalSorter
from pathlib import Path

_PACKAGE = "grafix.devtools.benchmarks"
_PROVIDERS = {
    "effect_benchmark",
    "interactive_scenario_benchmark",
    "mp_draw_benchmark",
    "parameter_edit_benchmark",
    "parameter_hotpath_benchmark",
    "perf_hotpath_benchmark",
    "primitive_benchmark",
    "remaining_effect_benchmark",
    "renderer_benchmark",
    "system_benchmark",
}
_ALLOWED_PROVIDER_DEPENDENCIES = {
    "interactive_scenario_benchmark": {
        "parameter_hotpath_benchmark",
        "renderer_benchmark",
    },
    "parameter_edit_benchmark": {"parameter_hotpath_benchmark"},
}


def _benchmark_root() -> Path:
    return Path(__file__).resolve().parents[2] / "src" / "grafix" / "devtools" / "benchmarks"


def _module_imports(path: Path, known: set[str]) -> set[str]:
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            candidates = [node.module]
            candidates.extend(f"{node.module}.{alias.name}" for alias in node.names)
        else:
            continue
        for candidate in candidates:
            if not candidate.startswith(f"{_PACKAGE}."):
                continue
            local = candidate.removeprefix(f"{_PACKAGE}.").split(".", 1)[0]
            if local in known and local != path.stem:
                imports.add(local)
    return imports


def _import_graph() -> dict[str, set[str]]:
    root = _benchmark_root()
    paths = {path.stem: path for path in root.glob("*.py") if path.name != "__init__.py"}
    known = set(paths)
    return {name: _module_imports(path, known) for name, path in paths.items()}


def _private_provider_references(path: Path) -> set[str]:
    """Sibling provider の private symbol 参照を抽出する。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    provider_aliases: dict[str, str] = {}
    private_references: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                prefix = f"{_PACKAGE}."
                if not alias.name.startswith(prefix):
                    continue
                provider = alias.name.removeprefix(prefix).split(".", 1)[0]
                if provider in _PROVIDERS and alias.asname is not None:
                    provider_aliases[alias.asname] = provider
        elif isinstance(node, ast.ImportFrom) and node.module == _PACKAGE:
            for alias in node.names:
                if alias.name in _PROVIDERS:
                    provider_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            prefix = f"{_PACKAGE}."
            if not node.module.startswith(prefix):
                continue
            provider = node.module.removeprefix(prefix).split(".", 1)[0]
            if provider not in _PROVIDERS:
                continue
            private_references.update(
                f"{provider}.{alias.name}" for alias in node.names if alias.name.startswith("_")
            )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
            continue
        if isinstance(node.value, ast.Name) and node.value.id in provider_aliases:
            private_references.add(f"{provider_aliases[node.value.id]}.{node.attr}")
    return private_references


def test_benchmark_module_graph_is_acyclic() -> None:
    graph = _import_graph()
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:  # pragma: no cover - failure detail
        raise AssertionError(f"benchmark import cycle: {exc.args}") from exc


def test_benchmark_layers_keep_one_way_dependencies() -> None:
    graph = _import_graph()

    assert not graph["definition"] & ({"catalog", "executor", "runner"} | _PROVIDERS)
    assert not graph["metrics"] & ({"catalog", "executor", "runner"} | _PROVIDERS)
    assert not graph["executor"] & ({"catalog", "runner"} | _PROVIDERS)
    assert not graph["catalog"] & {"executor", "runner"}
    for provider in _PROVIDERS:
        assert not graph[provider] & {"catalog", "executor", "runner"}
        assert graph[provider] & _PROVIDERS <= _ALLOWED_PROVIDER_DEPENDENCIES.get(
            provider,
            set(),
        )


def test_provider_dependencies_use_only_public_symbols() -> None:
    root = _benchmark_root()

    violations = {
        provider: references
        for provider in sorted(_PROVIDERS)
        if (references := _private_provider_references(root / f"{provider}.py"))
    }

    assert violations == {}
