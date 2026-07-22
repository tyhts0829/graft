"""primitive/effect の公開 decorator と authoring adapter を定義する。"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ParamSpec, TypeAlias, TypeVar, cast, overload

from grafix.core.authoring_definitions import register_authoring_declaration
from grafix.core.builtins import builtin_evaluator_abi
from grafix.core.operation_declaration import (
    CachePolicy,
    ExternalDependencyHook,
    OpKind,
    attach_operation_declaration,
    create_op_declaration,
)
from grafix.core.operation_schema import ParameterOpSchema, UiVisiblePred
from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.meta_spec import meta_dict_from_user
from grafix.core.parameters.validation import validate_parameter_value
from grafix.core.realized_geometry import (
    GeomTuple,
    RealizedGeometry,
    concat_realized_geometries,
    realized_geometry_from_tuple,
)
from grafix.core.value_validation import (
    canonical_immutable_value,
    exact_bool,
    exact_integer,
)

PrimitiveEvaluator: TypeAlias = Callable[
    [tuple[tuple[str, Any], ...]],
    RealizedGeometry,
]
EffectEvaluator: TypeAlias = Callable[
    [Sequence[RealizedGeometry], tuple[tuple[str, Any], ...]],
    RealizedGeometry,
]

_P = ParamSpec("_P")
_GeomTupleT = TypeVar("_GeomTupleT", bound=GeomTuple)

_WRAPPER_ARGUMENTS = frozenset({"activate", "instance_key", "key", "shared"})
_ACTIVATE_META = {
    "primitive": ParamMeta(
        kind="bool",
        description="このプリミティブによる形状生成を有効にする。",
    ),
    "effect": ParamMeta(
        kind="bool",
        description="このエフェクトによる形状変換を有効にする。",
    ),
}


def _builtin_evaluation_contract() -> None:
    """manifest ABI から builtin fingerprint を作るための stable marker。"""


def _operation_parameters(
    *,
    kind: OpKind,
    func: Callable[..., object],
    n_inputs: int,
) -> tuple[inspect.Parameter, ...]:
    """decorator が受理できる callable signature を検証する。"""

    try:
        parameters = tuple(inspect.signature(func).parameters.values())
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{kind} evaluator signature を取得できません") from exc

    if kind == "effect":
        if len(parameters) < n_inputs:
            raise TypeError(
                f"effect '{func.__name__}' は Geometry 入力を {n_inputs} 個"
                "位置引数として宣言する必要があります"
            )
        for parameter in parameters[:n_inputs]:
            if parameter.kind not in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }:
                raise TypeError(
                    f"effect '{func.__name__}' の Geometry 入力 {parameter.name!r} は"
                    "位置引数である必要があります"
                )
            if parameter.default is not inspect.Parameter.empty:
                raise TypeError(
                    f"effect '{func.__name__}' の Geometry 入力 {parameter.name!r} に"
                    "default は指定できません"
                )
        operation_parameters = parameters[n_inputs:]
    else:
        operation_parameters = parameters

    for parameter in operation_parameters:
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise TypeError(
                f"{kind} '{func.__name__}' の operation 引数 {parameter.name!r} は"
                "keyword で受け取れる必要があります"
            )
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise TypeError(
                f"{kind} '{func.__name__}' に可変位置引数は使用できません"
            )

    reserved = sorted(
        parameter.name
        for parameter in parameters
        if parameter.name in _WRAPPER_ARGUMENTS
    )
    if reserved:
        raise ValueError(
            f"{kind} '{func.__name__}' の wrapper 予約引数は使用できません: "
            f"{reserved!r}"
        )
    return operation_parameters


def _schema_from_callable(
    *,
    kind: OpKind,
    func: Callable[..., object],
    n_inputs: int,
    meta: Mapping[str, ParamMeta] | None,
    ui_visible: Mapping[str, UiVisiblePred] | None,
) -> ParameterOpSchema:
    """signature と metadata を一度照合して immutable schema を作る。"""

    parameters = _operation_parameters(kind=kind, func=func, n_inputs=n_inputs)
    if meta is None:
        for parameter in parameters:
            if (
                parameter.default is inspect.Parameter.empty
                or parameter.kind
                in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
            ):
                continue
            canonical_immutable_value(
                parameter.default,
                name=f"{kind} '{func.__name__}' の {parameter.name!r} default",
            )
        return ParameterOpSchema(
            meta={},
            defaults={},
            param_order=(),
            ui_visible={} if ui_visible is None else ui_visible,
        )

    by_name = {parameter.name: parameter for parameter in parameters}
    defaults: dict[str, Any] = {}
    for argument, argument_meta in meta.items():
        matched_parameter = by_name.get(argument)
        if matched_parameter is None:
            raise ValueError(
                f"{kind} '{func.__name__}' の meta 引数がシグネチャに存在しない: "
                f"{argument!r}"
            )
        if matched_parameter.default is inspect.Parameter.empty:
            raise ValueError(
                f"{kind} '{func.__name__}' の meta 引数は default 必須: {argument!r}"
            )
        defaults[argument] = validate_parameter_value(
            matched_parameter.default,
            kind=argument_meta.kind,
            choices=argument_meta.choices,
        )

    for parameter in parameters:
        if (
            parameter.name in meta
            or parameter.default is inspect.Parameter.empty
            or parameter.kind
            in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        ):
            continue
        canonical_immutable_value(
            parameter.default,
            name=f"{kind} '{func.__name__}' の {parameter.name!r} default",
        )

    parameter_order = tuple(
        parameter.name for parameter in parameters if parameter.name in meta
    )
    return ParameterOpSchema(
        meta={"activate": _ACTIVATE_META[kind], **meta},
        defaults={"activate": True, **defaults},
        param_order=("activate", *parameter_order),
        ui_visible={} if ui_visible is None else ui_visible,
    )


def _normalized_meta(
    *,
    kind: OpKind,
    meta: Mapping[str, ParamMeta | Mapping[str, object]] | None,
) -> dict[str, ParamMeta] | None:
    """user metadata を正規化し、wrapper 予約引数を拒否する。"""

    normalized = None if meta is None else meta_dict_from_user(meta)
    if normalized is None:
        return None
    reserved = sorted(_WRAPPER_ARGUMENTS & set(normalized))
    if reserved:
        names = ", ".join(reserved)
        raise ValueError(f"{kind} の予約引数は meta に含められない: {names}")
    return normalized


def _source_owner(func: Callable[..., object], *, kind: OpKind) -> str:
    """reload module が明示した ownership、または module 名を返す。"""

    module = identity_string(func.__module__, name=f"{kind} module")
    module_object = sys.modules.get(module)
    return identity_string(
        getattr(module_object, "__grafix_source_owner__", module),
        name=f"{kind} source owner",
    )


@overload
def primitive(
    func: Callable[_P, _GeomTupleT],
    *,
    overwrite: bool = False,
    cache_policy: CachePolicy = "content",
    version: str | None = None,
    external_dependency_hook: ExternalDependencyHook | None = None,
    meta: Mapping[str, ParamMeta | Mapping[str, object]] | None = None,
    ui_visible: Mapping[str, UiVisiblePred] | None = None,
) -> Callable[_P, _GeomTupleT]: ...


@overload
def primitive(
    func: None = None,
    *,
    overwrite: bool = False,
    cache_policy: CachePolicy = "content",
    version: str | None = None,
    external_dependency_hook: ExternalDependencyHook | None = None,
    meta: Mapping[str, ParamMeta | Mapping[str, object]] | None = None,
    ui_visible: Mapping[str, UiVisiblePred] | None = None,
) -> Callable[[Callable[_P, _GeomTupleT]], Callable[_P, _GeomTupleT]]: ...


def primitive(
    func: Callable[..., GeomTuple] | None = None,
    *,
    overwrite: bool = False,
    cache_policy: CachePolicy = "content",
    version: str | None = None,
    external_dependency_hook: ExternalDependencyHook | None = None,
    meta: Mapping[str, ParamMeta | Mapping[str, object]] | None = None,
    ui_visible: Mapping[str, UiVisiblePred] | None = None,
) -> (
    Callable[..., GeomTuple]
    | Callable[[Callable[..., GeomTuple]], Callable[..., GeomTuple]]
):
    """関数を primitive として宣言する公開 decorator。

    Parameters
    ----------
    func : Callable[..., GeomTuple] or None, optional
        デコレート対象。戻り値は canonical な ``(coords, offsets)`` とする。
    overwrite : bool, default=False
        現在の authoring target にある同名宣言を置換するか。
    cache_policy : {"content", "none"}, default="content"
        pure/deterministic な operation は ``"content"`` を使う。外部状態へ
        依存して結果 cache を使えない場合だけ ``"none"`` を指定する。
    version : str or None, optional
        動的 operation の明示 version。``cache_policy="none"`` では必須。
    external_dependency_hook : Callable or None, optional
        cache lookup ごとに外部 asset の fingerprint と評価 lease を解決する hook。
    meta : Mapping or None, optional
        Parameter GUI に公開する引数の metadata。None は GUI 非公開を表す。
    ui_visible : Mapping or None, optional
        現在値から各公開引数の GUI 表示可否を決める predicate。

    Returns
    -------
    Callable
        declaration を付与した元の callable、または decorator。
    """

    overwrite_b = exact_bool(overwrite, name="overwrite")
    normalized_meta = _normalized_meta(kind="primitive", meta=meta)

    def decorator(f: Callable[..., GeomTuple]) -> Callable[..., GeomTuple]:
        module = identity_string(f.__module__, name="primitive module")
        builtin_abi = builtin_evaluator_abi(
            kind="primitive",
            name=f.__name__,
            module=module,
            attribute=f.__name__,
        )
        if normalized_meta is None and builtin_abi is not None:
            raise ValueError(
                f"組み込み primitive は meta 必須: {f.__module__}.{f.__name__}"
            )
        schema = _schema_from_callable(
            kind="primitive",
            func=f,
            n_inputs=0,
            meta=normalized_meta,
            ui_visible=ui_visible,
        )

        def evaluate(args: tuple[tuple[str, Any], ...]) -> RealizedGeometry:
            params = dict(args)
            if normalized_meta is not None and params.pop("activate") is False:
                return concat_realized_geometries()
            return realized_geometry_from_tuple(
                f(**params),
                context=f"@primitive {f.__module__}.{f.__name__}",
            )

        declaration = create_op_declaration(
            name=f.__name__,
            kind="primitive",
            evaluator=cast(PrimitiveEvaluator, evaluate),
            schema=schema,
            n_inputs=0,
            cache_policy=cache_policy,
            evaluator_abi=(
                "grafix-primitive-adapter-v1"
                if builtin_abi is None
                else f"grafix-builtin-primitive-{builtin_abi}"
            ),
            version=version,
            external_dependency_hook=external_dependency_hook,
            decorator_options={
                "adapter": "primitive-v1",
                "builtin_locator": (
                    None if builtin_abi is None else (module, f.__name__, builtin_abi)
                ),
            },
            source_owner=_source_owner(f, kind="primitive"),
            signature_source=f,
            fingerprint_source=(
                None if builtin_abi is None else _builtin_evaluation_contract
            ),
        )
        if builtin_abi is None:
            register_authoring_declaration(declaration, overwrite=overwrite_b)
        attach_operation_declaration(f, declaration)
        return f

    if func is None:
        return decorator
    return decorator(func)


@overload
def effect(
    func: Callable[_P, _GeomTupleT],
    *,
    overwrite: bool = False,
    cache_policy: CachePolicy = "content",
    version: str | None = None,
    n_inputs: int = 1,
    meta: Mapping[str, ParamMeta | Mapping[str, object]] | None = None,
    ui_visible: Mapping[str, UiVisiblePred] | None = None,
) -> Callable[_P, _GeomTupleT]: ...


@overload
def effect(
    func: None = None,
    *,
    overwrite: bool = False,
    cache_policy: CachePolicy = "content",
    version: str | None = None,
    n_inputs: int = 1,
    meta: Mapping[str, ParamMeta | Mapping[str, object]] | None = None,
    ui_visible: Mapping[str, UiVisiblePred] | None = None,
) -> Callable[[Callable[_P, _GeomTupleT]], Callable[_P, _GeomTupleT]]: ...


def effect(
    func: Callable[..., GeomTuple] | None = None,
    *,
    overwrite: bool = False,
    cache_policy: CachePolicy = "content",
    version: str | None = None,
    n_inputs: int = 1,
    meta: Mapping[str, ParamMeta | Mapping[str, object]] | None = None,
    ui_visible: Mapping[str, UiVisiblePred] | None = None,
) -> (
    Callable[..., GeomTuple]
    | Callable[[Callable[..., GeomTuple]], Callable[..., GeomTuple]]
):
    """関数を effect として宣言する公開 decorator。

    Parameters
    ----------
    func : Callable[..., GeomTuple] or None, optional
        デコレート対象。先頭の ``n_inputs`` 個で Geometry tuple を受け取る。
    overwrite : bool, default=False
        現在の authoring target にある同名宣言を置換するか。
    cache_policy : {"content", "none"}, default="content"
        pure/deterministic な operation は ``"content"`` を使う。
    version : str or None, optional
        動的 operation の明示 version。``cache_policy="none"`` では必須。
    n_inputs : int, default=1
        effect が必要とする Geometry 入力数。
    meta : Mapping or None, optional
        Parameter GUI に公開する引数の metadata。None は GUI 非公開を表す。
    ui_visible : Mapping or None, optional
        現在値から各公開引数の GUI 表示可否を決める predicate。

    Returns
    -------
    Callable
        declaration を付与した元の callable、または decorator。
    """

    overwrite_b = exact_bool(overwrite, name="overwrite")
    n_inputs_i = exact_integer(n_inputs, name="n_inputs", minimum=1)
    normalized_meta = _normalized_meta(kind="effect", meta=meta)

    def decorator(f: Callable[..., GeomTuple]) -> Callable[..., GeomTuple]:
        module = identity_string(f.__module__, name="effect module")
        builtin_abi = builtin_evaluator_abi(
            kind="effect",
            name=f.__name__,
            module=module,
            attribute=f.__name__,
        )
        if normalized_meta is None and builtin_abi is not None:
            raise ValueError(
                f"組み込み effect は meta 必須: {f.__module__}.{f.__name__}"
            )
        schema = _schema_from_callable(
            kind="effect",
            func=f,
            n_inputs=n_inputs_i,
            meta=normalized_meta,
            ui_visible=ui_visible,
        )

        def evaluate(
            inputs: Sequence[RealizedGeometry],
            args: tuple[tuple[str, Any], ...],
        ) -> RealizedGeometry:
            if len(inputs) != n_inputs_i:
                raise TypeError(
                    f"effect '{f.__name__}' は入力 Geometry を {n_inputs_i} 個必要とします"
                    f"（受け取った数: {len(inputs)}）"
                )
            params = dict(args)
            if normalized_meta is not None and params.pop("activate") is False:
                if len(inputs) == 1:
                    return inputs[0]
                return concat_realized_geometries(*inputs)

            inputs_as_tuples = tuple((geometry.coords, geometry.offsets) for geometry in inputs)
            out = f(*inputs_as_tuples, **params)
            if type(out) is tuple and len(out) == 2:
                out_coords, out_offsets = out
                for geometry in inputs:
                    if out_coords is geometry.coords and out_offsets is geometry.offsets:
                        return geometry
                    if out_offsets is geometry.offsets:
                        realized = geometry._with_coords(out_coords)
                        if realized is not None:
                            return realized
            return realized_geometry_from_tuple(
                out,
                context=f"@effect {f.__module__}.{f.__name__}",
            )

        declaration = create_op_declaration(
            name=f.__name__,
            kind="effect",
            evaluator=cast(EffectEvaluator, evaluate),
            schema=schema,
            n_inputs=n_inputs_i,
            cache_policy=cache_policy,
            evaluator_abi=(
                "grafix-effect-adapter-v1"
                if builtin_abi is None
                else f"grafix-builtin-effect-{builtin_abi}"
            ),
            version=version,
            decorator_options={
                "adapter": "effect-v1",
                "builtin_locator": (
                    None if builtin_abi is None else (module, f.__name__, builtin_abi)
                ),
            },
            source_owner=_source_owner(f, kind="effect"),
            signature_source=f,
            fingerprint_source=(
                None if builtin_abi is None else _builtin_evaluation_contract
            ),
        )
        if builtin_abi is None:
            register_authoring_declaration(declaration, overwrite=overwrite_b)
        attach_operation_declaration(f, declaration)
        return f

    if func is None:
        return decorator
    return decorator(func)


__all__ = [
    "EffectEvaluator",
    "PrimitiveEvaluator",
    "effect",
    "primitive",
]
