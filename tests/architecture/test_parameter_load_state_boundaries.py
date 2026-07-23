from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import grafix.parameter_storage as parameter_storage
from grafix.core.parameters.runtime import ParameterLoadState, ParamStoreRuntime
from grafix.core.parameters.store import ParamStore
from grafix.parameter_storage import ParamStoreLoadResult


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_parameter_storage_does_not_reach_param_store_private_state() -> None:
    """filesystem owner は ParamStore の公開 operation だけを使う。"""

    path = _repo_root() / "src" / "grafix" / "parameter_storage.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    private_store_capabilities = {
        "_collapsed_headers_ref",
        "_favorite_keys_ref",
        "_get_state_ref",
        "_locked_keys_ref",
        "_runtime_ref",
        "_variations_ref",
    }
    violations = [
        f"{path.name}:{node.lineno}: {node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in private_store_capabilities
    ]

    assert not violations, "parameter storage の private state access を検出:\n" + "\n".join(
        violations
    )


def test_legacy_parameter_load_contract_is_not_publicly_available() -> None:
    """load metadata の owner を result/state に一意化する。"""

    assert not hasattr(parameter_storage, "ParamStoreReadResult")
    assert not hasattr(parameter_storage, "ParamStoreReadStatus")

    store = ParamStore()
    assert not hasattr(store, "load_provenance")
    assert not hasattr(store, "load_diagnostics")
    assert not hasattr(store, "accept_loaded_state")

    runtime = ParamStoreRuntime()
    assert not hasattr(runtime, "load_provenance")
    assert not hasattr(runtime, "load_diagnostics")


def test_parameter_load_result_carries_immutable_state_outside_store() -> None:
    """field allowlist ではなく、store と load metadata の ownership を検査する。"""

    store = ParamStore()
    load_state = ParameterLoadState(provenance="session_recovery")
    result = ParamStoreLoadResult(
        store=store,
        status="loaded",
        load_state=load_state,
    )

    assert result.store is store
    assert result.load_state is load_state
    assert result.load_state.provenance == "session_recovery"
    assert not hasattr(store, "load_state")
    with pytest.raises(FrozenInstanceError):
        result.load_state = ParameterLoadState()  # type: ignore[misc]
