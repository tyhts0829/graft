"""レビューで固定した局所的な単一責務契約を検査する。"""

from __future__ import annotations

import ast
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _function_names(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return tuple(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _class_method_names(path: Path, class_name: str) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return tuple(
        node.name
        for node in target.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_grid_diagnostic_adapter_has_one_owner() -> None:
    root = _repo_root() / "src" / "grafix" / "core"
    effects = root / "effects"
    former_owners = (
        effects / "metaball.py",
        effects / "growth.py",
        effects / "reaction_diffusion.py",
        effects / "isocontour.py",
    )

    assert all(
        "_grid_spec_from_bbox" not in _function_names(path) for path in former_owners
    )
    assert _function_names(root / "operation_diagnostics.py").count(
        "grid_spec_from_bbox_with_diagnostic"
    ) == 1


def test_grid_point_budget_uses_point_vocabulary() -> None:
    grid_source = (
        _repo_root()
        / "src"
        / "grafix"
        / "core"
        / "geometry_kernels"
        / "grid.py"
    ).read_text(encoding="utf-8")

    assert "DEFAULT_MAX_GRID_CELLS" not in grid_source
    assert "def cell_count" not in grid_source
    assert "max_cells" not in grid_source
    assert "DEFAULT_MAX_GRID_POINTS" in grid_source
    assert "def point_count" in grid_source
    assert "max_points" in grid_source


def test_trusted_draw_task_does_not_repeat_submit_validation() -> None:
    path = (
        _repo_root()
        / "src"
        / "grafix"
        / "interactive"
        / "runtime"
        / "mp_draw.py"
    )

    assert "__post_init__" not in _class_method_names(path, "_DrawTask")


def test_mp_draw_transition_state_owns_no_process_or_queue_capability() -> None:
    path = (
        _repo_root()
        / "src"
        / "grafix"
        / "interactive"
        / "runtime"
        / "mp_draw.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    state = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_MpDrawState"
    )
    method_names = {
        node.name for node in state.body if isinstance(node, ast.FunctionDef)
    }
    capability_calls = {
        node.func.attr
        for node in ast.walk(state)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "close" not in method_names
    assert not capability_calls & {
        "cancel_join_thread",
        "get_nowait",
        "join",
        "join_thread",
        "kill",
        "put",
        "put_nowait",
        "start",
        "terminate",
    }


def test_export_request_invariants_have_one_helper() -> None:
    path = (
        _repo_root()
        / "src"
        / "grafix"
        / "interactive"
        / "runtime"
        / "export_job_system.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definitions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_validate_export_request"
    ]
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_validate_export_request"
    ]

    assert len(definitions) == 1
    assert len(calls) == 2


def test_scene_item_static_and_runtime_containers_are_lists_or_tuples() -> None:
    path = _repo_root() / "src" / "grafix" / "core" / "scene.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    alias = next(
        node
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "SceneItem"
    )
    alias_source = ast.unparse(alias.value)

    assert "Sequence" not in source
    assert "list['SceneItem']" in alias_source
    assert "tuple['SceneItem', ...]" in alias_source
    assert "isinstance(item, (list, tuple))" in source


def test_public_operation_info_has_no_evaluator_capability() -> None:
    path = _repo_root() / "src" / "grafix" / "api" / "operation_info.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    operation_info = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "OperationInfo"
    )
    fields = {
        node.target.id
        for node in operation_info.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert fields == {
        "accepted_args",
        "accepts_var_kwargs",
        "defaults",
        "description",
        "doc",
        "kind",
        "meta",
        "n_inputs",
        "name",
        "provenance",
        "required_args",
        "source",
    }
    assert not fields & {"catalog", "declaration", "evaluation", "evaluator"}


def test_render_session_property_allowlist_excludes_child_owners() -> None:
    path = _repo_root() / "src" / "grafix" / "api" / "render.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    render_session = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RenderSession"
    )
    properties = {
        node.name
        for node in render_session.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(decorator, ast.Name) and decorator.id == "property"
            for decorator in node.decorator_list
        )
    }

    assert properties == {
        "config",
        "metadata",
        "options",
        "param_store",
        "runtime_limits",
    }


def test_scene_runner_fakes_are_not_injected_through_private_state() -> None:
    tests_root = _repo_root() / "tests"
    violations: list[str] = []
    for path in tests_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: tuple[ast.expr, ...]
            if isinstance(node, ast.Assign):
                targets = tuple(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
            elif isinstance(node, ast.AugAssign):
                targets = (node.target,)
            else:
                continue
            if any(
                isinstance(target, ast.Attribute) and target.attr == "_mp_draw"
                for target in targets
            ):
                violations.append(f"{path.relative_to(_repo_root())}:{node.lineno}")

    assert violations == []


def test_presented_frame_state_does_not_leak_back_into_dws() -> None:
    path = (
        _repo_root()
        / "src"
        / "grafix"
        / "interactive"
        / "runtime"
        / "draw_window_system.py"
    )
    forbidden = {
        "_last_export_provenance_token",
        "_last_export_snapshot",
        "_last_frame_t",
        "_last_realized_layers",
        "_presented_frame_id",
        "_presented_snapshot_revision",
        "_provenance_builder",
        "_provenance_frame_index",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert not attributes & forbidden
    assert "_presented_frame" in attributes


def test_gui_widget_state_has_no_process_global_reset_path() -> None:
    gui_root = _repo_root() / "src" / "grafix" / "interactive" / "parameter_gui"
    violations: list[str] = []
    for name in ("widgets.py", "table.py"):
        path = gui_root / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                violations.append(f"{name}:{node.lineno}: global")
            if isinstance(node, ast.Name) and node.id in {
                "_CHOICE_FILTER_BY_KEY",
                "_FONT_FILTER_BY_KEY",
                "_snippet_popup_focus_next",
                "_snippet_popup_text",
            }:
                violations.append(f"{name}:{node.lineno}: {node.id}")

    assert violations == []
