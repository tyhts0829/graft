"""
Purpose:
    parameter metadata、canonical UI value、MIDI assignmentが共有する値契約を一元化する。
Use when:
    parameter kind、UI range、choice、RGB、またはCC割当のschemaを変更する場合。
Constraints:
    - boolを数値へ扱うなどの暗黙coercionを避け、exact typeとfinite値を要求する。
    - kind、choices、range、value、CC shapeの整合を全consumerで共通に保つ。
    - RGBは0..255の三成分とし、style系operationへMIDI割当を許可しない。
"""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite
from numbers import Integral, Real
from typing import Literal, TypeAlias, cast

ParamKind: TypeAlias = Literal[
    "bool",
    "int",
    "float",
    "str",
    "font",
    "choice",
    "vec3",
    "rgb",
]
CcKey: TypeAlias = int | tuple[int | None, int | None, int | None] | None

PARAM_KINDS = frozenset({"bool", "int", "float", "str", "font", "choice", "vec3", "rgb"})
NUMERIC_PARAM_KINDS = frozenset({"int", "float", "vec3", "rgb"})
SCALAR_CC_PARAM_KINDS = frozenset({"float", "int", "choice"})
_CC_DISABLED_OPS = frozenset({"__style__", "__layer_style__"})


def validate_param_kind(kind: object) -> ParamKind:
    """サポート対象の parameter kind を返す。"""

    if not isinstance(kind, str):
        raise TypeError("parameter kind must be a string")
    if kind not in PARAM_KINDS:
        raise ValueError(f"unsupported parameter kind: {kind!r}")
    return cast(ParamKind, kind)


def validate_param_choices(
    kind: ParamKind,
    choices: Sequence[str] | None,
) -> tuple[str, ...] | None:
    """kind と整合する immutable choices を返す。"""

    if kind != "choice":
        if choices is not None:
            raise ValueError("choices is only valid for choice parameters")
        return None
    if (
        choices is None
        or isinstance(choices, (str, bytes))
        or not isinstance(choices, Sequence)
        or not choices
    ):
        raise ValueError("choice parameters require non-empty choices")
    if any(type(choice) is not str for choice in choices):
        raise TypeError("choice values must be exact strings")
    normalized = tuple(choices)
    if len(set(normalized)) != len(normalized):
        raise ValueError("choice values must be unique")
    return normalized


def validate_param_range(
    kind: ParamKind,
    ui_min: object | None,
    ui_max: object | None,
) -> tuple[int | float | None, int | float | None]:
    """kind と整合する canonical UI range を返す。"""

    if kind not in NUMERIC_PARAM_KINDS:
        if ui_min is not None or ui_max is not None:
            raise ValueError(f"{kind} parameters cannot define ui_min/ui_max")
        return None, None
    normalized: list[int | float | None] = []
    for field, value in (("ui_min", ui_min), ("ui_max", ui_max)):
        if value is None:
            normalized.append(None)
            continue
        if kind in {"int", "rgb"}:
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise TypeError(f"{field} must be an integer for {kind}")
            number: int | float = int(value)
            if kind == "rgb" and not 0 <= number <= 255:
                raise ValueError(f"{field} must be in 0..255 for rgb")
        else:
            if isinstance(value, bool) or not isinstance(value, Real):
                raise TypeError(f"{field} must be a finite real number")
            number = float(value)
            if not isfinite(number):
                raise ValueError(f"{field} must be a finite real number")
        normalized.append(number)
    normalized_min, normalized_max = normalized
    if normalized_min is not None and normalized_max is not None:
        if normalized_min >= normalized_max:
            raise ValueError("ui_min must be less than ui_max")
    return normalized_min, normalized_max


def validate_parameter_value(
    value: object,
    *,
    kind: ParamKind,
    choices: Sequence[str] | None,
) -> object:
    """widget/store が扱う canonical UI value を検証して返す。"""

    if kind == "bool":
        if type(value) is not bool:
            raise TypeError("bool parameter value must be an exact bool")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise TypeError("int parameter value must be an integer")
        return int(value)
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("float parameter value must be a real number")
        normalized = float(value)
        if not isfinite(normalized):
            raise ValueError("float parameter value must be finite")
        return normalized
    if kind in {"str", "font"}:
        if type(value) is not str:
            raise TypeError(f"{kind} parameter value must be an exact string")
        return value
    if kind == "choice":
        if type(value) is not str:
            raise TypeError("choice parameter value must be an exact string")
        allowed = validate_param_choices(kind, choices)
        assert allowed is not None
        if value not in allowed:
            raise ValueError(f"choice parameter value is unavailable: {value!r}")
        return value
    if kind == "vec3":
        if type(value) is not tuple or len(value) != 3:
            raise TypeError("vec3 parameter value must be a three-float tuple")
        if any(
            isinstance(component, bool) or not isinstance(component, Real) for component in value
        ):
            raise TypeError("vec3 components must be real numbers")
        normalized_vec = tuple(float(component) for component in value)
        if any(not isfinite(component) for component in normalized_vec):
            raise ValueError("vec3 components must be finite")
        return normalized_vec
    if kind == "rgb":
        if type(value) is not tuple or len(value) != 3:
            raise TypeError("rgb parameter value must be a three-int tuple")
        if any(
            isinstance(component, bool) or not isinstance(component, Integral)
            for component in value
        ):
            raise TypeError("rgb components must be integers")
        normalized_rgb = tuple(int(component) for component in value)
        if any(component < 0 or component > 255 for component in normalized_rgb):
            raise ValueError("rgb components must be in 0..255")
        return normalized_rgb
    raise AssertionError(f"unreachable parameter kind: {kind!r}")


def validate_cc_key(
    cc_key: object,
    *,
    kind: ParamKind,
    op: str,
) -> CcKey:
    """parameter kind/op に対応する canonical MIDI CC assignment を返す。"""

    canonical = validate_cc_key_shape(cc_key)
    if canonical is None:
        return None
    if op in _CC_DISABLED_OPS:
        raise ValueError(f"MIDI CC is not supported for {op}")
    if type(canonical) is int:
        if kind not in SCALAR_CC_PARAM_KINDS:
            raise ValueError(f"scalar MIDI CC is not supported for {kind}")
        return canonical
    if kind != "vec3":
        raise ValueError(f"component MIDI CC is not supported for {kind}")
    return canonical


def validate_cc_key_shape(cc_key: object) -> CcKey:
    """kind に依存しない MIDI CC の immutable shape と値域を検証する。"""

    if cc_key is None:
        return None
    if type(cc_key) is int:
        if not 0 <= cc_key <= 127:
            raise ValueError("MIDI CC must be in 0..127")
        return cc_key
    if type(cc_key) is not tuple or len(cc_key) != 3:
        raise TypeError("MIDI CC must be an int, a three-item tuple, or None")
    for component in cc_key:
        if component is None:
            continue
        if type(component) is not int:
            raise TypeError("MIDI CC components must be exact ints or None")
        if not 0 <= component <= 127:
            raise ValueError("MIDI CC components must be in 0..127")
    if cc_key == (None, None, None):
        raise ValueError("empty component MIDI CC must be represented by None")
    return cc_key


__all__ = [
    "CcKey",
    "NUMERIC_PARAM_KINDS",
    "PARAM_KINDS",
    "ParamKind",
    "SCALAR_CC_PARAM_KINDS",
    "validate_cc_key",
    "validate_cc_key_shape",
    "validate_param_choices",
    "validate_param_kind",
    "validate_param_range",
    "validate_parameter_value",
]
