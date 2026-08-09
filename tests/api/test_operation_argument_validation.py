"""G/E operation の eager argument validation を検証する。"""

from __future__ import annotations

from contextlib import nullcontext
from enum import Enum

import numpy as np
import pytest

import grafix.api._op_validation as op_validation_module
import grafix.core.geometry as geometry_module
from grafix import E, G
from grafix.api._op_validation import validate_operation_kwargs
from grafix.core.operation_declaration import create_op_declaration
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.operation_authoring import effect
from grafix.core.parameters.context import parameter_context
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.operation_authoring import primitive
from grafix.core.realized_geometry import GeomTuple


@primitive(meta={"count": ParamMeta(kind="int")})
def central_validation_custom_primitive(*, count: int = 2) -> GeomTuple:
    _ = count
    return (
        np.empty((0, 3), dtype=np.float32),
        np.zeros((1,), dtype=np.int32),
    )


@primitive
def central_validation_required_primitive(*, seed: int) -> GeomTuple:
    _ = seed
    return (
        np.empty((0, 3), dtype=np.float32),
        np.zeros((1,), dtype=np.int32),
    )


@primitive
def central_validation_fixed_primitive(
    *,
    payload: object = (),
    **dynamic: object,
) -> GeomTuple:
    _ = payload, dynamic
    return (
        np.empty((0, 3), dtype=np.float32),
        np.zeros((1,), dtype=np.int32),
    )


@effect
def central_validation_fixed_effect(
    geometry: GeomTuple,
    *,
    payload: object = (),
) -> GeomTuple:
    _ = payload
    return geometry


def test_primitive_rejects_unknown_keyword_with_suggestion() -> None:
    with pytest.raises(TypeError, match=r"lenght.*length.*誤り"):
        G.line(lenght=2.0)


def test_effect_rejects_unknown_keyword_in_first_and_chained_step() -> None:
    with pytest.raises(TypeError, match=r"scal.*scale.*誤り"):
        E.scale(scal=(2.0, 2.0, 2.0))

    with pytest.raises(TypeError, match=r"rotaton.*rotation.*誤り"):
        E.scale().rotate(rotaton=(0.0, 0.0, 1.0))


def test_primitive_and_effect_reject_invalid_choice() -> None:
    with pytest.raises(ValueError, match=r"anchor.*center.*left.*right"):
        G.line(anchor="middle")
    with pytest.raises(ValueError, match=r"mode.*all.*by_line.*by_face"):
        E.scale(mode="separate")


def test_valid_choice_and_reserved_arguments_are_accepted() -> None:
    primitive = G.line(anchor="left", activate=False, key="line")
    effect = E.scale(mode="by_line", activate=False, key="scale")

    assert primitive.op == "line"
    assert effect.steps[0].parameter_op == "scale"


@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, None])
def test_primitive_and_effect_require_exact_bool_activate(invalid: object) -> None:
    with pytest.raises(TypeError, match="exact bool"):
        G.line(activate=invalid)
    with pytest.raises(TypeError, match="exact bool"):
        E.scale(activate=invalid)
    with pytest.raises(TypeError, match="exact bool"):
        E.scale().rotate(activate=invalid)


@pytest.mark.parametrize("invalid", ["false", 1])
def test_operation_selectors_require_exact_bool_activate(invalid: object) -> None:
    with pytest.raises(TypeError, match="exact bool"):
        G.select(
            target="line",
            params_by_target={"line": {"activate": invalid}},
        )
    with pytest.raises(TypeError, match="exact bool"):
        E.select(
            target="scale",
            params_by_target={"scale": {"activate": invalid}},
        )


def test_var_keyword_operation_keeps_dynamic_authoring_contract() -> None:
    def evaluator(**_params: object) -> object:
        return None

    spec = create_op_declaration(
        name="dynamic",
        kind="primitive",
        evaluator=evaluator,
        schema=ParameterOpSchema(
            meta={},
            defaults={},
            param_order=(),
            ui_visible={},
        ),
        n_inputs=0,
    )

    assert validate_operation_kwargs(
        op="dynamic",
        spec=spec,
        params={"custom": [1, 2]},
    ) == {"custom": (1, 2)}


class _CodeValueEnum(Enum):
    VALUE = "value"


@pytest.mark.parametrize(
    "invalid",
    [
        [1, 2],
        ((1, [2]),),
        {"value": 1},
        _CodeValueEnum.VALUE,
        np.array([1, 2], dtype=np.int64),
        np.int64(1),
    ],
)
@pytest.mark.parametrize(
    "factory",
    [
        lambda value: G.central_validation_fixed_primitive(payload=value),
        lambda value: E.central_validation_fixed_effect(payload=value),
    ],
)
def test_custom_fixed_signature_rejects_noncanonical_code_values(
    factory,
    invalid: object,
) -> None:
    with pytest.raises(TypeError, match="immutable"):
        factory(invalid)


def test_custom_fixed_signature_preserves_canonical_tuple_tree() -> None:
    payload = (None, True, 1, -0.0, "value", (2.5,))

    primitive = G.central_validation_fixed_primitive(payload=payload)
    effect_builder = E.central_validation_fixed_effect(payload=payload)

    expected = (None, True, 1, 0.0, "value", (2.5,))
    assert dict(primitive.args)["payload"] == expected
    assert dict(effect_builder.steps[0].args)["payload"] == expected


def test_custom_var_kwargs_freezes_only_dynamic_arguments() -> None:
    geometry = G.central_validation_fixed_primitive(
        payload=(1, 2),
        dynamic=[3, 4],
    )

    assert dict(geometry.args) == {
        "dynamic": (3, 4),
        "payload": (1, 2),
    }


@pytest.mark.parametrize(
    "factory",
    [
        lambda: G.polyline(points=[(0.0, 0.0), (1.0, 1.0)]),
        lambda: G.polyline(points=((0.0, 0.0), [1.0, 1.0])),
        lambda: G.spline(points=[(0.0, 0.0), (1.0, 1.0)]),
        lambda: G.bezier(p0=[0.0, 0.0]),
        lambda: G.select(
            target="polyline",
            params_by_target={"polyline": {"points": [(0.0, 0.0)]}},
        ),
    ],
)
def test_builtin_fixed_signature_rejects_mutable_code_values(factory) -> None:
    with pytest.raises(TypeError, match="immutable"):
        factory()


def test_builtin_fixed_signature_preserves_exact_tuple_values() -> None:
    polyline = G.polyline(points=((0.0, 0.0), (1.0, 1.0)))
    spline = G.spline(points=((0.0, 0.0), (1.0, 1.0)))
    bezier = G.bezier(p0=(0.0, 0.0))

    assert dict(polyline.args)["points"] == ((0.0, 0.0), (1.0, 1.0))
    assert dict(spline.args)["points"] == ((0.0, 0.0), (1.0, 1.0))
    assert dict(bezier.args)["p0"] == (0.0, 0.0)


def test_required_operation_argument_is_rejected_by_builder() -> None:
    with pytest.raises(TypeError, match=r"required_primitive.*seed"):
        G.central_validation_required_primitive()


def test_effect_builder_is_hashable_and_has_no_nested_mutable_params() -> None:
    first = E.scale(scale=(2.0, 3.0, 4.0), key="immutable-step")
    second = E.scale(scale=(2.0, 3.0, 4.0), key="immutable-step")

    assert first == second
    assert hash(first) == hash(second)
    assert first.steps[0].args == (("scale", (2.0, 3.0, 4.0)),)
    assert isinstance(first.steps[0].args, tuple)


def test_g_polyline_avoids_permissive_normalization_for_fixed_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def counted_normalize(params):
        nonlocal calls
        calls += 1
        raise AssertionError("固定 signature 引数を permissive normalize してはならない")

    def unexpected_public_factory_normalize(_params):
        raise AssertionError("G は Geometry.create で再正規化してはならない")

    monkeypatch.setattr(op_validation_module, "normalize_args", counted_normalize)
    monkeypatch.setattr(
        geometry_module,
        "normalize_args",
        unexpected_public_factory_normalize,
    )

    geometry = G.polyline(
        points=tuple((float(index), float(index % 7)) for index in range(2_000)),
        closed=True,
        key="single-normalization",
    )

    assert geometry.op == "polyline"
    assert calls == 0


@pytest.mark.parametrize(
    "factory",
    [
        lambda: E.dash(dash_length=[2.0, 1.0]),
        lambda: E.dash(gap_length=[1.0, 2.0]),
        lambda: E.dash(offset=[0.0, 1.0]),
        lambda: E.dash(offset_jitter=[0.0, 1.0]),
        lambda: E.fill(angle_sets=[1, 2]),
        lambda: E.fill(angle=[0.0, 90.0]),
        lambda: E.fill(density=[10.0, 20.0]),
        lambda: E.fill(min_spacing=[0.1, 0.2]),
        lambda: E.fill(spacing_gradient=[0.0, 0.5]),
        lambda: E.fill(remove_boundary=[True, False]),
    ],
)
def test_dash_and_fill_reject_groupwise_sequence_parameters(factory) -> None:
    with pytest.raises(TypeError):
        factory()


@pytest.mark.parametrize("recording", [False, True])
@pytest.mark.parametrize(
    "factory",
    [
        lambda: G.circle(segments="4"),
        lambda: G.circle(segments=3.9),
        lambda: G.circle(segments=True),
        lambda: G.circle(radius="2.0"),
        lambda: G.circle(radius=float("inf")),
        lambda: G.circle(center="123"),
        lambda: G.circle(center=[1.0, 2.0, 3.0]),
        lambda: G.circle(center=(1.0, float("nan"), 3.0)),
        lambda: E.translate(delta=(1.0, 2.0, 3.0, 4.0)),
        lambda: E.translate(delta=[1.0, 2.0, 3.0]),
        lambda: G.central_validation_custom_primitive(count="4"),
    ],
)
def test_meta_values_are_rejected_equally_inside_and_outside_recording_context(
    recording: bool,
    factory,
) -> None:
    context = parameter_context(ParamStore()) if recording else nullcontext()
    with context, pytest.raises((TypeError, ValueError)):
        factory()


@pytest.mark.parametrize("recording", [False, True])
def test_meta_values_are_canonical_in_every_recording_context(recording: bool) -> None:
    context = parameter_context(ParamStore()) if recording else nullcontext()
    with context:
        geometry = G.circle(
            radius=np.float32(2.5),
            segments=np.int64(8),
            center=(np.float32(1.0), np.int64(2), 3.0),
        )

    args = dict(geometry.args)
    assert type(args["radius"]) is float
    assert type(args["segments"]) is int
    assert args["center"] == (1.0, 2.0, 3.0)
    assert all(type(value) is float for value in args["center"])


def test_selector_validates_custom_operation_before_lowering() -> None:
    with pytest.raises(TypeError, match="count"):
        G.select(
            target="central_validation_custom_primitive",
            params_by_target={
                "central_validation_custom_primitive": {"count": 1.5},
            },
        )
