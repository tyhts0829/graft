"""
Purpose:
    公開G/E引数を、operation schemaに対するcanonical DAG引数へ検証する。
Use when:
    operation引数の型、未知key、required/default、またはerror policyを変更する場合。
Constraints:
    - validationをGeometry作成前に完了し、暗黙coercionで不正な公開入力を隠さない。
    - evaluatorではなく、declarationに固定されたschemaとarityを正本にする。
"""

from __future__ import annotations

from collections.abc import Mapping
from difflib import get_close_matches
from typing import Any, Protocol

from grafix.core.geometry import normalize_args
from grafix.core.operation_declaration import OpKind
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters.validation import validate_parameter_value
from grafix.core.value_validation import canonical_immutable_value


class OperationParameterSpec(Protocol):
    """operation kwargs validation に必要な immutable contract。"""

    @property
    def kind(self) -> OpKind: ...

    @property
    def schema(self) -> ParameterOpSchema: ...

    @property
    def accepted_args(self) -> tuple[str, ...]: ...

    @property
    def required_args(self) -> tuple[str, ...]: ...

    @property
    def accepts_var_kwargs(self) -> bool: ...


def _suggest(name: str, candidates: tuple[str, ...]) -> str:
    matches = get_close_matches(name, candidates, n=1, cutoff=0.55)
    if not matches:
        return ""
    return f"。{matches[0]!r} の誤りですか？"


def validate_operation_kwargs(
    *,
    op: str,
    spec: OperationParameterSpec,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """明示指定された operation 引数を検証し、canonical 値で返す。

    ``activate`` のような wrapper 所有引数も受け入れるため、元 callable の
    signature だけでなく registry の defaults/meta も正規の引数集合に含める。
    default は declaration 構築時に同じ parameter validator で正規化済みなので、
    ここでは明示引数だけを扱う。
    """

    if not isinstance(params, Mapping):
        raise TypeError("operation 引数は mapping である必要があります")
    if any(type(name) is not str for name in params):
        raise TypeError("operation 引数名は exact str である必要があります")

    schema = spec.schema
    allowed = tuple(
        dict.fromkeys(
            (*spec.accepted_args, *schema.defaults.keys(), *schema.meta.keys())
        )
    )
    unknown = (
        ()
        if spec.accepts_var_kwargs
        else tuple(sorted(set(params) - set(allowed)))
    )
    if unknown:
        rendered = ", ".join(
            f"{name!r}{_suggest(name, allowed)}" for name in unknown
        )
        raise TypeError(f"{spec.kind} {op!r} に不明な引数があります: {rendered}")

    missing = tuple(name for name in spec.required_args if name not in params)
    if missing:
        rendered = ", ".join(repr(name) for name in missing)
        raise TypeError(f"{spec.kind} {op!r} に必要な引数がありません: {rendered}")

    canonical: dict[str, Any] = {}
    dynamic: dict[str, Any] = {}
    fixed_args = frozenset(spec.accepted_args)
    for name, value in params.items():
        meta = schema.meta.get(name)
        if meta is None:
            if name in fixed_args:
                canonical[name] = canonical_immutable_value(
                    value,
                    name=f"{spec.kind} {op!r} の {name!r}",
                )
            else:
                # ``**kwargs`` だけが受け取る動的引数は authoring 入力なので、
                # Geometry に保存する前に mutable alias を切り離す。
                dynamic[name] = value
            continue
        try:
            canonical[name] = validate_parameter_value(
                value,
                kind=meta.kind,
                choices=meta.choices,
            )
        except (TypeError, ValueError) as exc:
            if (
                isinstance(exc, ValueError)
                and meta.kind == "choice"
                and meta.choices is not None
            ):
                choices = tuple(meta.choices)
                hint = _suggest(str(value), tuple(str(choice) for choice in choices))
                raise ValueError(
                    f"{spec.kind} {op!r} の {name!r} は {choices!r} から選択してください"
                    f": {value!r}{hint}"
                ) from exc
            message = f"{spec.kind} {op!r} の {name!r} が不正です: {exc}"
            raise type(exc)(message) from exc

    if dynamic:
        canonical.update(normalize_args(dynamic))
    return canonical


__all__ = ["OperationParameterSpec", "validate_operation_kwargs"]
