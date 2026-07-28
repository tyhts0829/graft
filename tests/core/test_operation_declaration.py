"""immutable operation declaration と参照型の契約を検証する。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from types import ModuleType
from typing import cast

import pytest

from grafix.core.authoring_definitions import RegistrationTarget, registration_scope
from grafix.core.definition_fingerprint import (
    EvaluationSpecFingerprint,
    ParameterSchemaFingerprint,
)
from grafix.core.operation_authoring import effect, primitive
from grafix.core.operation_declaration import (
    CachePolicy,
    EffectStepRef,
    EvaluationOpRef,
    OpKind,
    OpDeclaration,
    create_op_declaration,
    operation_declaration,
)
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple


def _schema(*, description: str = "amount") -> ParameterOpSchema:
    return ParameterOpSchema(
        meta={"amount": ParamMeta(kind="float", description=description)},
        defaults={"amount": 1.0},
        param_order=("amount",),
        ui_visible={},
    )


def _primitive(*, amount: float = 1.0) -> object:
    return amount


def _effect(geometry: object, *, amount: float = 1.0) -> object:
    _ = amount
    return geometry


def _operation_callable(
    module_name: str,
    *,
    kind: OpKind,
) -> Callable[..., GeomTuple]:
    if kind == "primitive":

        def canonical_source_owner_operation() -> GeomTuple:
            return cast(GeomTuple, ((), ()))

    else:

        def canonical_source_owner_operation(geometry: GeomTuple) -> GeomTuple:
            return geometry

    canonical_source_owner_operation.__module__ = module_name
    canonical_source_owner_operation.__qualname__ = (
        "canonical_source_owner_operation"
    )
    return canonical_source_owner_operation


def _authored_declaration(
    module_name: str,
    *,
    kind: OpKind = "primitive",
    cache_policy: CachePolicy = "content",
    version: str | None = None,
) -> OpDeclaration:
    target = RegistrationTarget()
    operation = _operation_callable(module_name, kind=kind)
    with registration_scope(target):
        decorator = primitive if kind == "primitive" else effect
        authored = decorator(operation, cache_policy=cache_policy, version=version)
    return operation_declaration(authored)


def test_factory_builds_frozen_declaration_and_typed_references() -> None:
    declaration = create_op_declaration(
        name="scale",
        kind="effect",
        evaluator=_effect,
        schema=_schema(),
        n_inputs=1,
    )

    assert isinstance(declaration.evaluation_fingerprint, EvaluationSpecFingerprint)
    assert isinstance(declaration.schema_fingerprint, ParameterSchemaFingerprint)
    assert declaration.ref == EvaluationOpRef(
        kind="effect",
        name="scale",
        fingerprint=declaration.evaluation_fingerprint,
    )
    assert declaration.effect_step_ref == EffectStepRef(
        operation=declaration.ref,
        schema_fingerprint=declaration.schema_fingerprint,
    )
    with pytest.raises(FrozenInstanceError):
        declaration.name = "changed"  # type: ignore[misc]


def test_schema_only_change_keeps_evaluation_fingerprint() -> None:
    first = create_op_declaration(
        name="dot",
        kind="primitive",
        evaluator=_primitive,
        schema=_schema(description="first"),
        n_inputs=0,
    )
    second = create_op_declaration(
        name="dot",
        kind="primitive",
        evaluator=_primitive,
        schema=_schema(description="second"),
        n_inputs=0,
    )

    assert first.evaluation_fingerprint == second.evaluation_fingerprint
    assert first.schema_fingerprint != second.schema_fingerprint


def test_evaluation_contract_options_change_only_the_target_declaration() -> None:
    first = create_op_declaration(
        name="scale",
        kind="effect",
        evaluator=_effect,
        schema=_schema(),
        n_inputs=1,
        evaluator_abi="1",
    )
    second = create_op_declaration(
        name="scale",
        kind="effect",
        evaluator=_effect,
        schema=_schema(),
        n_inputs=1,
        evaluator_abi="2",
    )

    assert first.evaluation_fingerprint != second.evaluation_fingerprint
    assert first.schema_fingerprint == second.schema_fingerprint


def test_external_dependency_hook_is_part_of_evaluation_fingerprint() -> None:
    def first_hook(*_args: object) -> str:
        return "first"

    def second_hook(*_args: object) -> str:
        return "second"

    without_hook = create_op_declaration(
        name="scale",
        kind="effect",
        evaluator=_effect,
        schema=_schema(),
        n_inputs=1,
    )
    first = create_op_declaration(
        name="scale",
        kind="effect",
        evaluator=_effect,
        schema=_schema(),
        n_inputs=1,
        external_dependency_hook=first_hook,
    )
    second = create_op_declaration(
        name="scale",
        kind="effect",
        evaluator=_effect,
        schema=_schema(),
        n_inputs=1,
        external_dependency_hook=second_hook,
    )

    assert len(
        {
            without_hook.evaluation_fingerprint,
            first.evaluation_fingerprint,
            second.evaluation_fingerprint,
        }
    ) == 3
    assert (
        without_hook.schema_fingerprint
        == first.schema_fingerprint
        == second.schema_fingerprint
    )


def test_effect_step_ref_rejects_primitive_reference() -> None:
    declaration = create_op_declaration(
        name="dot",
        kind="primitive",
        evaluator=_primitive,
        schema=_schema(),
        n_inputs=0,
    )

    with pytest.raises(ValueError, match="effect"):
        EffectStepRef(
            operation=declaration.ref,
            schema_fingerprint=declaration.schema_fingerprint,
        )


def test_none_cache_policy_requires_explicit_stable_version() -> None:
    with pytest.raises(ValueError, match="version"):
        create_op_declaration(
            name="dynamic",
            kind="primitive",
            evaluator=_primitive,
            schema=_schema(),
            n_inputs=0,
            cache_policy="none",
        )


@pytest.mark.parametrize("kind", ["primitive", "effect"])
@pytest.mark.parametrize("module_name", ["__main__", "__mp_main__"])
def test_direct_main_source_owner_is_canonical_but_provenance_is_raw(
    module_name: str,
    kind: OpKind,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, module_name, ModuleType(module_name))

    declaration = _authored_declaration(module_name, kind=kind)

    assert declaration.source_owner == "__main__"
    assert (
        declaration.provenance
        == f"{module_name}:canonical_source_owner_operation"
    )


@pytest.mark.parametrize("kind", ["primitive", "effect"])
def test_content_direct_main_identity_matches_spawn_alias(
    kind: OpKind,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for module_name in ("__main__", "__mp_main__"):
        monkeypatch.setitem(sys.modules, module_name, ModuleType(module_name))

    parent = _authored_declaration("__main__", kind=kind)
    worker = _authored_declaration("__mp_main__", kind=kind)

    assert parent.source_owner == worker.source_owner == "__main__"
    assert parent.evaluation_fingerprint == worker.evaluation_fingerprint


@pytest.mark.parametrize("kind", ["primitive", "effect"])
def test_dynamic_direct_main_identity_matches_spawn_alias(
    kind: OpKind,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for module_name in ("__main__", "__mp_main__"):
        monkeypatch.setitem(sys.modules, module_name, ModuleType(module_name))

    parent = _authored_declaration(
        "__main__",
        kind=kind,
        cache_policy="none",
        version="stable-v1",
    )
    worker = _authored_declaration(
        "__mp_main__",
        kind=kind,
        cache_policy="none",
        version="stable-v1",
    )

    assert parent.source_owner == worker.source_owner == "__main__"
    assert parent.evaluation_fingerprint == worker.evaluation_fingerprint
    assert parent.provenance == "__main__:canonical_source_owner_operation"
    assert worker.provenance == "__mp_main__:canonical_source_owner_operation"


def test_explicit_source_owner_is_not_canonicalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "explicit_source_owner_fixture"
    module = ModuleType(module_name)
    module.__grafix_source_owner__ = "__mp_main__"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module_name, module)

    declaration = _authored_declaration(module_name)

    assert declaration.source_owner == "__mp_main__"
    assert (
        declaration.provenance
        == "explicit_source_owner_fixture:canonical_source_owner_operation"
    )
