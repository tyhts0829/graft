"""operation 定義 fingerprint の決定性と感度を検証する。"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import pytest

from grafix.core.definition_fingerprint import (
    DefinitionFingerprintError,
    EvaluationSpecFingerprint,
    ParameterSchemaFingerprint,
    attach_module_content_fingerprint,
    fingerprint_evaluation_spec,
    fingerprint_parameter_schema,
)
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters.meta import ParamMeta
from grafix.core.python_module_identity import canonical_authoring_module_name


def _compiled_function(source: str, *, filename: str) -> Callable[..., object]:
    namespace: dict[str, Any] = {"__name__": "fingerprint_fixture"}
    exec(compile(source, filename, "exec"), namespace)
    return namespace["evaluate"]


def _authored_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_name: str,
    dependencies: Literal["dataclass", "lru_cache", "combined"] = "combined",
    helper_delta: int = 1,
    fingerprint_name: str | None = None,
) -> types.ModuleType:
    """main alias と tracked dependency を持つ synthetic authoring module を作る。"""

    dataclass_source = (
        "from dataclasses import dataclass\n\n"
        "@dataclass(frozen=True)\n"
        "class Settings:\n"
        "    offset: int\n\n"
        "SETTINGS = Settings(3)\n\n"
        "def evaluate(value):\n"
        "    return value + SETTINGS.offset\n"
    )
    lru_cache_source = (
        "from functools import lru_cache\n\n"
        "@lru_cache(maxsize=4)\n"
        "def cached_step(value):\n"
        f"    return value + {helper_delta}\n\n"
        "def evaluate(value):\n"
        "    return cached_step(value)\n"
    )
    combined_source = (
        "from dataclasses import dataclass\n"
        "from enum import Enum\n"
        "from functools import lru_cache\n\n"
        "@dataclass(frozen=True)\n"
        "class Settings:\n"
        "    offset: int\n\n"
        "class Mode(Enum):\n"
        "    ACTIVE = 1\n\n"
        "SETTINGS = Settings(3)\n"
        "MODE = Mode.ACTIVE\n\n"
        "@lru_cache(maxsize=4)\n"
        "def cached_step(value):\n"
        f"    return value + {helper_delta}\n\n"
        "def evaluate(value):\n"
        "    return cached_step(value) + SETTINGS.offset + MODE.value\n"
    )
    source = {
        "dataclass": dataclass_source,
        "lru_cache": lru_cache_source,
        "combined": combined_source,
    }[dependencies]
    module = types.ModuleType(module_name)
    if fingerprint_name is not None:
        module.__grafix_fingerprint_name__ = fingerprint_name  # type: ignore[attr-defined]
    attach_module_content_fingerprint(module, source.encode("utf-8"))
    monkeypatch.setitem(sys.modules, module_name, module)
    exec(compile(source, f"<{module_name}>", "exec"), module.__dict__)
    return module


def _schema(
    *,
    description: str = "count",
    visible: Callable[[dict[str, Any]], bool] | None = None,
) -> ParameterOpSchema:
    return ParameterOpSchema(
        meta={
            "count": ParamMeta(
                kind="int",
                ui_min=0,
                ui_max=10,
                description=description,
            ),
            "enabled": ParamMeta(kind="bool"),
        },
        defaults={"count": 2, "enabled": True},
        param_order=("count", "enabled"),
        ui_visible={} if visible is None else {"count": visible},
    )


def test_same_callable_content_is_stable_across_object_filename_and_line() -> None:
    first = _compiled_function(
        "def evaluate(value=2):\n    return value + 1\n",
        filename="/checkout-a/project/module.py",
    )
    second = _compiled_function(
        "\n\n\n\ndef evaluate(value=2):\n    return value + 1\n",
        filename="/checkout-b/elsewhere/module.py",
    )

    first_fingerprint = fingerprint_evaluation_spec(first)
    second_fingerprint = fingerprint_evaluation_spec(second)

    assert isinstance(first_fingerprint, EvaluationSpecFingerprint)
    assert first is not second
    assert first_fingerprint == second_fingerprint


def test_closed_over_reloaded_callable_does_not_depend_on_temporary_module_name() -> None:
    first_namespace: dict[str, Any] = {"__name__": "candidate_a"}
    second_namespace: dict[str, Any] = {"__name__": "candidate_b"}
    source = "def evaluate(value):\n    return value + 1\n"
    exec(compile(source, "/first/checkout/op.py", "exec"), first_namespace)
    exec(compile(source, "/second/checkout/op.py", "exec"), second_namespace)

    def wrap(func: Callable[[int], int]) -> Callable[[int], int]:
        def wrapper(value: int) -> int:
            return func(value)

        return wrapper

    first = wrap(first_namespace["evaluate"])
    second = wrap(second_namespace["evaluate"])

    assert fingerprint_evaluation_spec(first) == fingerprint_evaluation_spec(second)


def test_spawn_main_alias_only_normalizes_to_parent_main_name() -> None:
    assert canonical_authoring_module_name("__main__") == "__main__"
    assert canonical_authoring_module_name("__mp_main__") == "__main__"
    assert canonical_authoring_module_name("project.__mp_main__") == "project.__mp_main__"
    assert canonical_authoring_module_name("project.sketch") == "project.sketch"


@pytest.mark.parametrize("dependencies", ["dataclass", "lru_cache", "combined"])
def test_spawn_main_alias_keeps_dataclass_enum_and_lru_cache_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
    dependencies: Literal["dataclass", "lru_cache", "combined"],
) -> None:
    parent = _authored_module(
        monkeypatch,
        module_name="__main__",
        dependencies=dependencies,
    )
    worker = _authored_module(
        monkeypatch,
        module_name="__mp_main__",
        dependencies=dependencies,
    )

    assert fingerprint_evaluation_spec(parent.evaluate) == fingerprint_evaluation_spec(
        worker.evaluate
    )


def test_regular_external_module_names_remain_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _authored_module(monkeypatch, module_name="regular_authoring_first")
    second = _authored_module(monkeypatch, module_name="regular_authoring_second")

    assert fingerprint_evaluation_spec(first.evaluate) != fingerprint_evaluation_spec(
        second.evaluate
    )


def test_spawn_main_alias_keeps_referenced_semantic_changes_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _authored_module(
        monkeypatch,
        module_name="__main__",
        helper_delta=1,
    )
    worker = _authored_module(
        monkeypatch,
        module_name="__mp_main__",
        helper_delta=2,
    )

    assert fingerprint_evaluation_spec(parent.evaluate) != fingerprint_evaluation_spec(
        worker.evaluate
    )


def test_explicit_module_fingerprint_name_wins_over_spawn_main_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = _authored_module(monkeypatch, module_name="__main__")
    explicitly_named_worker = _authored_module(
        monkeypatch,
        module_name="__mp_main__",
        fingerprint_name="project.explicit_entry",
    )

    assert fingerprint_evaluation_spec(
        parent.evaluate
    ) != fingerprint_evaluation_spec(explicitly_named_worker.evaluate)


def test_referenced_global_insertion_order_does_not_change_fingerprint() -> None:
    source = "def evaluate():\n    return alpha + beta\n"
    first_namespace: dict[str, Any] = {
        "__name__": "fingerprint_fixture",
        "alpha": 1,
        "beta": 2,
    }
    second_namespace: dict[str, Any] = {
        "__name__": "fingerprint_fixture",
        "beta": 2,
        "alpha": 1,
    }
    exec(compile(source, "first.py", "exec"), first_namespace)
    exec(compile(source, "second.py", "exec"), second_namespace)

    assert fingerprint_evaluation_spec(first_namespace["evaluate"]) == fingerprint_evaluation_spec(
        second_namespace["evaluate"]
    )


def test_defaults_kwdefaults_and_closure_are_part_of_callable_fingerprint() -> None:
    positional_a = _compiled_function(
        "def evaluate(value=1):\n    return value\n",
        filename="a.py",
    )
    positional_b = _compiled_function(
        "def evaluate(value=2):\n    return value\n",
        filename="b.py",
    )
    keyword_a = _compiled_function(
        "def evaluate(*, value=1):\n    return value\n",
        filename="a.py",
    )
    keyword_b = _compiled_function(
        "def evaluate(*, value=2):\n    return value\n",
        filename="b.py",
    )

    def close_over(value: int) -> Callable[[], int]:
        def evaluate() -> int:
            return value

        return evaluate

    assert fingerprint_evaluation_spec(positional_a) != fingerprint_evaluation_spec(positional_b)
    assert fingerprint_evaluation_spec(keyword_a) != fingerprint_evaluation_spec(keyword_b)
    assert fingerprint_evaluation_spec(close_over(1)) != fingerprint_evaluation_spec(close_over(2))


def test_referenced_helper_content_is_part_of_callable_fingerprint() -> None:
    source_template = """
def helper(value):
    return value + {delta}

def evaluate(value):
    return helper(value)
"""
    first = _compiled_function(source_template.format(delta=1), filename="first.py")
    second = _compiled_function(source_template.format(delta=2), filename="second.py")

    assert fingerprint_evaluation_spec(first) != fingerprint_evaluation_spec(second)


def _snapshot_backed_helper(
    *,
    module_name: str,
    canonical_name: str,
    source_path: Path,
    content: bytes,
) -> types.ModuleType:
    module = types.ModuleType(module_name)
    module.__file__ = str(source_path)
    module.__grafix_fingerprint_name__ = canonical_name  # type: ignore[attr-defined]
    attach_module_content_fingerprint(module, content)
    exec(compile(content, str(source_path), "exec"), module.__dict__)
    return module


def _function_referencing_helper(
    helper: types.ModuleType,
    *,
    module_name: str,
    filename: str,
) -> Callable[[int], int]:
    namespace: dict[str, Any] = {
        "__name__": module_name,
        "helper_value": helper.helper_value,
    }
    exec(
        compile(
            "def evaluate(value):\n    return helper_value(value)\n",
            filename,
            "exec",
        ),
        namespace,
    )
    return namespace["evaluate"]


def test_snapshot_module_digest_wins_over_later_live_file_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"def helper_value(value):\n    return value + 1\n"
    first_path = tmp_path / "checkout-a" / "helper.py"
    second_path = tmp_path / "checkout-b" / "helper.py"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_bytes(content)
    second_path.write_bytes(content)
    first_helper = _snapshot_backed_helper(
        module_name="snapshot_helper_a",
        canonical_name="project.helper",
        source_path=first_path,
        content=content,
    )
    second_helper = _snapshot_backed_helper(
        module_name="snapshot_helper_b",
        canonical_name="project.helper",
        source_path=second_path,
        content=content,
    )
    monkeypatch.setitem(sys.modules, first_helper.__name__, first_helper)
    monkeypatch.setitem(sys.modules, second_helper.__name__, second_helper)
    first = _function_referencing_helper(
        first_helper,
        module_name="snapshot_main_a",
        filename="/checkout-a/main.py",
    )
    second = _function_referencing_helper(
        second_helper,
        module_name="snapshot_main_b",
        filename="/checkout-b/main.py",
    )

    before_mutation = fingerprint_evaluation_spec(first)
    first_path.write_text(
        "def helper_value(value):\n    return value + 100\n",
        encoding="utf-8",
    )

    assert fingerprint_evaluation_spec(first) == before_mutation
    assert fingerprint_evaluation_spec(second) == before_mutation


def test_malformed_snapshot_module_digest_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "helper.py"
    content = b"def helper_value(value):\n    return value\n"
    source_path.write_bytes(content)
    helper = types.ModuleType("malformed_snapshot_helper")
    helper.__file__ = str(source_path)
    helper.__grafix_content_fingerprint__ = "not-a-typed-fingerprint"  # type: ignore[attr-defined]
    exec(compile(content, str(source_path), "exec"), helper.__dict__)
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    evaluate = _function_referencing_helper(
        helper,
        module_name="malformed_snapshot_main",
        filename="main.py",
    )

    with pytest.raises(DefinitionFingerprintError, match="ModuleContentFingerprint"):
        fingerprint_evaluation_spec(evaluate)


def test_decorator_options_are_order_independent_but_semantically_visible() -> None:
    evaluator = _compiled_function(
        "def evaluate(value):\n    return value\n",
        filename="operation.py",
    )

    first = fingerprint_evaluation_spec(
        evaluator,
        decorator_options={"n_inputs": 1, "cache_policy": "content"},
    )
    reordered = fingerprint_evaluation_spec(
        evaluator,
        decorator_options={"cache_policy": "content", "n_inputs": 1},
    )
    changed = fingerprint_evaluation_spec(
        evaluator,
        decorator_options={"n_inputs": 2, "cache_policy": "content"},
    )

    assert first == reordered
    assert first != changed


def test_parameter_schema_fingerprint_is_typed_order_independent_and_sensitive() -> None:
    schema = _schema()
    reordered = ParameterOpSchema(
        meta=dict(reversed(tuple(schema.meta.items()))),
        defaults=dict(reversed(tuple(schema.defaults.items()))),
        param_order=schema.param_order,
        ui_visible=dict(reversed(tuple(schema.ui_visible.items()))),
    )
    changed = _schema(description="number of samples")

    fingerprint = fingerprint_parameter_schema(schema)

    assert isinstance(fingerprint, ParameterSchemaFingerprint)
    assert fingerprint == fingerprint_parameter_schema(reordered)
    assert fingerprint != fingerprint_parameter_schema(changed)


def test_ui_visible_callable_content_is_part_of_schema_fingerprint() -> None:
    def visible_after(limit: int) -> Callable[[dict[str, Any]], bool]:
        def visible(values: dict[str, Any]) -> bool:
            return values["count"] > limit

        return visible

    first = _schema(visible=visible_after(1))
    second = _schema(visible=visible_after(2))

    assert fingerprint_parameter_schema(first) != fingerprint_parameter_schema(second)


def test_uncanonicalizable_referenced_content_is_an_explicit_error() -> None:
    opaque = object()

    def evaluate() -> object:
        return opaque

    with pytest.raises(DefinitionFingerprintError, match="opaque"):
        fingerprint_evaluation_spec(evaluate)
