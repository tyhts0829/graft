"""primitive/effect authoring decorator の共有契約を検証する。"""

from __future__ import annotations

from enum import Enum
from inspect import signature

import numpy as np
import pytest

from grafix.core.authoring_definitions import RegistrationTarget, registration_scope
from grafix.core.operation_authoring import effect, primitive
from grafix.core.operation_declaration import operation_declaration
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple


def _empty_geometry() -> GeomTuple:
    return (
        np.empty((0, 3), dtype=np.float32),
        np.zeros((1,), dtype=np.int32),
    )


@pytest.mark.parametrize(
    "default",
    ([1.0, 2.0], {"value": 1}),
)
def test_primitive_rejects_mutable_code_owned_default(default: object) -> None:
    def invalid_default(*, value: object = default) -> GeomTuple:
        _ = value
        return _empty_geometry()

    with registration_scope(RegistrationTarget()):
        with pytest.raises(TypeError, match="immutable"):
            primitive(invalid_default)


def test_primitive_rejects_enum_code_owned_default() -> None:
    class MutableEnum(Enum):
        ITEM = []

    def invalid_default(*, value: object = MutableEnum.ITEM) -> GeomTuple:
        _ = value
        return _empty_geometry()

    with registration_scope(RegistrationTarget()):
        with pytest.raises(TypeError, match="immutable"):
            primitive(invalid_default)


def test_decorators_reject_wrapper_owned_callable_arguments() -> None:
    def reserved_primitive(*, activate: bool = True) -> GeomTuple:
        _ = activate
        return _empty_geometry()

    def reserved_effect(geometry: GeomTuple, *, key: str | None = None) -> GeomTuple:
        _ = key
        return geometry

    def reserved_geometry_input(shared: GeomTuple) -> GeomTuple:
        return shared

    with registration_scope(RegistrationTarget()):
        with pytest.raises(ValueError, match="wrapper 予約引数"):
            primitive(reserved_primitive)
        with pytest.raises(ValueError, match="wrapper 予約引数"):
            effect(reserved_effect)
        with pytest.raises(ValueError, match="wrapper 予約引数"):
            effect(reserved_geometry_input)


def test_primitive_requires_keyword_passable_operation_arguments() -> None:
    def positional_only(value: float = 1.0, /) -> GeomTuple:
        _ = value
        return _empty_geometry()

    def variadic(*values: float) -> GeomTuple:
        _ = values
        return _empty_geometry()

    with registration_scope(RegistrationTarget()):
        with pytest.raises(TypeError, match="keyword"):
            primitive(positional_only)
        with pytest.raises(TypeError, match="可変位置引数"):
            primitive(variadic)


def test_effect_requires_unambiguous_geometry_inputs() -> None:
    def insufficient(first: GeomTuple) -> GeomTuple:
        return first

    def keyword_only(*, geometry: GeomTuple) -> GeomTuple:
        return geometry

    def variadic_geometry(*geometries: GeomTuple) -> GeomTuple:
        return geometries[0]

    def default_geometry(geometry: GeomTuple = _empty_geometry()) -> GeomTuple:
        return geometry

    with registration_scope(RegistrationTarget()):
        with pytest.raises(TypeError, match="2 個"):
            effect(n_inputs=2)(insufficient)
        with pytest.raises(TypeError, match="位置引数"):
            effect(keyword_only)
        with pytest.raises(TypeError, match="位置引数"):
            effect(variadic_geometry)
        with pytest.raises(TypeError, match="default"):
            effect(default_geometry)


def test_effect_requires_keyword_passable_operation_arguments() -> None:
    def positional_only(
        geometry: GeomTuple,
        amount: float = 1.0,
        /,
    ) -> GeomTuple:
        _ = amount
        return geometry

    def variadic(geometry: GeomTuple, *amounts: float) -> GeomTuple:
        _ = amounts
        return geometry

    with registration_scope(RegistrationTarget()):
        with pytest.raises(TypeError, match="keyword"):
            effect(positional_only)
        with pytest.raises(TypeError, match="可変位置引数"):
            effect(variadic)


@pytest.mark.parametrize("n_inputs", [True, 1.0, "1"])
def test_effect_rejects_implicitly_convertible_input_counts(n_inputs: object) -> None:
    with pytest.raises(TypeError, match="n_inputs.*int"):
        effect(n_inputs=n_inputs)  # type: ignore[arg-type]


def test_public_decorator_defaults_are_explicit() -> None:
    assert signature(primitive).parameters["overwrite"].default is False
    assert signature(effect).parameters["overwrite"].default is False
    assert signature(primitive).parameters["cache_policy"].default == "content"
    assert signature(effect).parameters["cache_policy"].default == "content"


def test_public_decorators_return_the_original_callable() -> None:
    def original_primitive(*, size: float = 1.0) -> GeomTuple:
        _ = size
        return _empty_geometry()

    def original_effect(
        geometry: GeomTuple,
        *,
        amount: float = 1.0,
    ) -> GeomTuple:
        _ = amount
        return geometry

    with registration_scope(RegistrationTarget()):
        decorated_primitive = primitive(original_primitive)
        decorated_effect = effect(meta={"amount": ParamMeta(kind="float")})(
            original_effect
        )

    assert decorated_primitive is original_primitive
    assert decorated_effect is original_effect
    assert operation_declaration(decorated_primitive) is not None
    assert operation_declaration(decorated_effect) is not None


@pytest.mark.parametrize("overwrite", [1, 0, "false", None])
def test_public_decorators_require_exact_bool_overwrite(overwrite: object) -> None:
    with pytest.raises(TypeError, match="overwrite"):
        primitive(overwrite=overwrite)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="overwrite"):
        effect(overwrite=overwrite)  # type: ignore[arg-type]


def test_duplicate_is_atomic_and_explicit_overwrite_replaces_whole_schema() -> None:
    target = RegistrationTarget()
    with registration_scope(target):

        @primitive(
            meta={"x": ParamMeta(kind="float")},
            ui_visible={"x": lambda _values: True},
        )
        def replace_target(*, x: float = 1.0) -> GeomTuple:
            _ = x
            return _empty_geometry()

    first = target.snapshot().operations.resolve(
        "primitive", "replace_target"
    ).declaration

    def replacement(*, y: float = 2.0) -> GeomTuple:
        _ = y
        return _empty_geometry()

    replacement.__name__ = "replace_target"
    with registration_scope(target):
        with pytest.raises(ValueError, match="既に登録"):
            primitive(replacement)
    assert target.snapshot().operations.resolve(
        "primitive", "replace_target"
    ).declaration is first

    with registration_scope(target):
        primitive(overwrite=True)(replacement)
    second = target.snapshot().operations.resolve(
        "primitive", "replace_target"
    ).declaration
    assert second is operation_declaration(replacement)
    assert dict(second.schema.meta) == {}
    assert dict(second.schema.defaults) == {}
    assert second.schema.param_order == ()
    assert dict(second.schema.ui_visible) == {}


def test_none_cache_policy_is_fixed_in_declaration_with_explicit_version() -> None:
    target = RegistrationTarget()
    with registration_scope(target):

        @primitive(cache_policy="none", version="live-v1")
        def live_shape() -> GeomTuple:
            return _empty_geometry()

    declaration = target.snapshot().operations.resolve(
        "primitive", "live_shape"
    ).declaration
    assert declaration.cache_policy == "none"
    assert declaration.version == "live-v1"


def test_versioned_none_cache_policy_accepts_explicit_dynamic_dependency() -> None:
    opaque = object()
    target = RegistrationTarget()
    with registration_scope(target):

        @primitive(cache_policy="none", version="dynamic-v1")
        def dynamic_shape() -> GeomTuple:
            _ = opaque
            return _empty_geometry()

    declaration = target.snapshot().operations.resolve(
        "primitive",
        "dynamic_shape",
    ).declaration
    assert declaration.cache_policy == "none"
    assert declaration.version == "dynamic-v1"


def test_meta_argument_must_exist_and_have_a_default() -> None:
    def missing_argument(*, value: float = 1.0) -> GeomTuple:
        _ = value
        return _empty_geometry()

    def missing_default(*, value: float) -> GeomTuple:
        _ = value
        return _empty_geometry()

    with registration_scope(RegistrationTarget()):
        with pytest.raises(ValueError, match="シグネチャに存在しない"):
            primitive(meta={"other": ParamMeta(kind="float")})(missing_argument)
        with pytest.raises(ValueError, match="default 必須"):
            primitive(meta={"value": ParamMeta(kind="float")})(missing_default)
