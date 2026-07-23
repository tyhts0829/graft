"""公開 capability と、query/command の依存境界を検査する。

helper 名、定義数、call 数、private attribute 名は固定しない。振る舞いの契約は
各 unit/runtime test が担い、この module には実装を別名へ移しても残る capability
境界だけを置く。
"""

from __future__ import annotations

import ast
from pathlib import Path

from grafix.api.operation_info import OperationInfo


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_public_operation_info_has_no_evaluator_capability() -> None:
    """公開 description DTO から executable owner へ到達させない。"""

    path = _repo_root() / "src" / "grafix" / "api" / "operation_info.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    operation_info = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "OperationInfo"
    )
    declared_members = {
        node.target.id
        for node in operation_info.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    declared_members.update(
        node.name
        for node in operation_info.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    info = OperationInfo(
        name="probe",
        kind="primitive",
        n_inputs=0,
        accepted_args=(),
        required_args=(),
        defaults={},
        meta={},
        description="",
        doc="",
        source=None,
        provenance="probe.operation",
        accepts_var_kwargs=False,
    )
    forbidden = {
        "catalog",
        "declaration",
        "evaluate",
        "evaluation",
        "evaluator",
        "operation",
        "realize",
        "resolve",
    }

    # description 用の派生 field は追加できる。実行 capability だけを禁止する。
    assert not declared_members & forbidden
    assert not set(dir(info)) & forbidden


def test_mp_draw_state_values_do_not_own_process_or_queue_capabilities() -> None:
    """submit/close owner 以外の state/message class を純粋な値・遷移に保つ。"""

    runtime_root = (
        _repo_root() / "src" / "grafix" / "interactive" / "runtime"
    )
    paths = (
        runtime_root / "_mp_draw_protocol.py",
        runtime_root / "_mp_draw_state.py",
    )
    forbidden_calls = {
        "cancel_join_thread",
        "close",
        "get_nowait",
        "join",
        "join_thread",
        "kill",
        "put",
        "put_nowait",
        "start",
        "terminate",
    }
    violations: list[str] = []
    forbidden_import_prefixes = ("multiprocessing", "queue", "threading")
    for path in paths:
        imports = _imported_modules(path)
        for module in imports:
            if module.startswith(forbidden_import_prefixes):
                violations.append(f"{path.name}: import {module}")

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for class_node in (
            node for node in tree.body if isinstance(node, ast.ClassDef)
        ):
            for node in ast.walk(class_node):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in forbidden_calls
                ):
                    violations.append(
                        f"{path.name}:{class_node.name}:{node.lineno}: "
                        f"{node.func.attr}()"
                    )

    assert not violations, "state/message class の I/O capability を検出:\n" + "\n".join(
        violations
    )


def test_parameter_table_query_does_not_depend_on_store_mutation_commands() -> None:
    """table view/cache は snapshot query だけを所有し、commit は別 module に置く。"""

    path = (
        _repo_root()
        / "src"
        / "grafix"
        / "interactive"
        / "parameter_gui"
        / "table_view.py"
    )
    imports = _imported_modules(path)
    mutation_modules = {
        "grafix.core.parameters.edit_commands",
        "grafix.core.parameters.effect_order_ops",
        "grafix.core.parameters.midi_ops",
        "grafix.core.parameters.ui_ops",
        "grafix.core.parameters.variations",
    }

    assert not imports & mutation_modules


def test_legacy_parameter_gui_store_bridge_is_deleted() -> None:
    """query/command 分離後の service-locator shim を復活させない。"""

    path = (
        _repo_root()
        / "src"
        / "grafix"
        / "interactive"
        / "parameter_gui"
        / "store_bridge.py"
    )

    assert not path.exists()
