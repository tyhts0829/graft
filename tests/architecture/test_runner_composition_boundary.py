"""公開 runner と interactive application lifetime の境界を固定する。"""

from __future__ import annotations

import ast
from pathlib import Path


def _runner_tree() -> ast.Module:
    root = Path(__file__).resolve().parents[2]
    path = root / "src" / "grafix" / "api" / "runner.py"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_public_run_delegates_interactive_lifetime_to_one_private_owner() -> None:
    tree = _runner_tree()
    public_run = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run"
    )
    application_owners = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and "Application" in node.name
    ]

    assert [owner.name for owner in application_owners] == [
        "_InteractiveApplication"
    ]

    public_names = {
        node.id for node in ast.walk(public_run) if isinstance(node, ast.Name)
    }
    lifecycle_names = {
        "DrawWindowSystem",
        "MultiWindowLoop",
        "ParameterSession",
        "WorkspaceWindowController",
        "authoring_definitions_for_draw",
        "create_midi_session",
    }
    assert not public_names & lifecycle_names
    assert "RenderOptions" in public_names
    assert "runtime_config_with_fallback" in public_names
    assert "_InteractiveApplication" in public_names
    assert not any(
        isinstance(node, (ast.Try, ast.With, ast.AsyncWith))
        for node in ast.walk(public_run)
    )

    owner_names = {
        node.id
        for node in ast.walk(application_owners[0])
        if isinstance(node, ast.Name)
    }
    assert lifecycle_names <= owner_names
