"""
Purpose:
    immutable operation catalog から ``G.select``/``E.select`` 用の evaluator-free parameter schema と安定 identity を合成する。
Use when:
    selector 候補、arity 別 effect 選択、target 引数の namespace、GUI/help 表示を変更・調査する場合。
Constraints:
    - selector を架空の evaluator として evaluation catalog へ登録しない。
    - 候補は public operation と exact effect arity で絞り、target ごとの schema fingerprint を固定する。
    - encoded parameter key は target/argument 境界を曖昧にせず、search/help で内部 namespace を漏らさない。
See:
    grafix.core.operation_schema
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from difflib import get_close_matches
from threading import RLock
from types import MappingProxyType
from typing import Any, Literal, cast

from .operation_catalog import (
    OperationCatalog,
    OperationCatalogEntry,
    current_operation_catalog,
)
from .operation_declaration import OpKind
from .operation_schema import ParameterOpSchema, UiVisiblePred
from .parameters.identity import identity_string
from .parameters.meta import ParamMeta
from .value_validation import exact_integer, exact_string_choice

SelectorKind = Literal["primitive", "effect"]

PRIMITIVE_SELECTOR_OP = "_grafix_select_primitive"
_EFFECT_SELECTOR_PREFIX = "_grafix_select_effect_"
_TARGET_ARG = "target"
_TARGET_PARAM_PREFIX = "@"
_TARGET_META_DESCRIPTION = "この呼び出しで実行する登録済み operation を選択する。"


class _NoSelectableOperationsError(ValueError):
    """指定 kind/arity の selector 候補が 1 件も無いことを表す。"""


@dataclass(frozen=True, slots=True, order=True)
class SelectorCatalogFingerprint:
    """evaluation catalog identity から独立した selector schema fingerprint。"""

    digest: str

    def __post_init__(self) -> None:
        if type(self.digest) is not str or len(self.digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.digest
        ):
            raise ValueError("selector fingerprint は SHA-256 lowercase hex です")


@dataclass(frozen=True, slots=True)
class SelectorSpec:
    """selector discovery が返す evaluator を持たない immutable schema。"""

    op: str
    kind: SelectorKind
    n_inputs: int
    schema: ParameterOpSchema
    fingerprint: SelectorCatalogFingerprint
    target_schema_fingerprints: Mapping[str, str]

    def __post_init__(self) -> None:
        op = identity_string(self.op, name="selector op")
        kind = cast(
            SelectorKind,
            exact_string_choice(
                self.kind,
                name="selector kind",
                choices=("primitive", "effect"),
            ),
        )
        n_inputs = exact_integer(self.n_inputs, name="selector n_inputs", minimum=0)
        if kind == "primitive" and n_inputs != 0:
            raise ValueError("primitive selector の n_inputs は 0 です")
        if kind == "effect" and n_inputs < 1:
            raise ValueError("effect selector の n_inputs は 1 以上です")
        if type(self.schema) is not ParameterOpSchema:
            raise TypeError("schema は exact ParameterOpSchema です")
        if type(self.fingerprint) is not SelectorCatalogFingerprint:
            raise TypeError("fingerprint は exact SelectorCatalogFingerprint です")
        fingerprints: dict[str, str] = {}
        for raw_name, raw_digest in self.target_schema_fingerprints.items():
            name = identity_string(raw_name, name="selector target name")
            if type(raw_digest) is not str or len(raw_digest) != 64:
                raise ValueError("target schema fingerprint が不正です")
            fingerprints[name] = raw_digest
        object.__setattr__(self, "op", op)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "n_inputs", n_inputs)
        object.__setattr__(
            self,
            "target_schema_fingerprints",
            MappingProxyType(fingerprints),
        )


@dataclass(frozen=True, slots=True)
class _SelectorCacheEntry:
    fingerprint: SelectorCatalogFingerprint
    spec: SelectorSpec


_SELECTOR_CACHE: dict[tuple[SelectorKind, int], _SelectorCacheEntry] = {}
_SELECTOR_CACHE_LOCK = RLock()


def validate_effect_selector_n_inputs(n_inputs: object) -> int:
    """effect selector の arity を厳密な正整数として返す。"""

    return exact_integer(n_inputs, name="effect selector の n_inputs", minimum=1)


def effect_selector_op(n_inputs: int) -> str:
    """effect arity に対応する private selector op 名を返す。"""

    return f"{_EFFECT_SELECTOR_PREFIX}{validate_effect_selector_n_inputs(n_inputs)}"


def selector_kind(op: str) -> SelectorKind | None:
    """private selector op の種別を返す。通常 operation なら None。"""

    op_s = identity_string(op, name="selector op")
    if op_s == PRIMITIVE_SELECTOR_OP:
        return "primitive"
    if op_s.startswith(_EFFECT_SELECTOR_PREFIX):
        suffix = op_s.removeprefix(_EFFECT_SELECTOR_PREFIX)
        if suffix.isdigit() and int(suffix) >= 1:
            return "effect"
    return None


def selector_effect_n_inputs(op: str) -> int | None:
    """private effect selector op から arity を返す。"""

    op_s = identity_string(op, name="selector op")
    if not op_s.startswith(_EFFECT_SELECTOR_PREFIX):
        return None
    suffix = op_s.removeprefix(_EFFECT_SELECTOR_PREFIX)
    if not suffix.isdigit() or int(suffix) < 1:
        return None
    return int(suffix)


def selector_param_key(target: str, arg: str) -> str:
    """target/arg を衝突しない ParameterKey.arg へ符号化する。"""

    target_s = identity_string(target, name="selector target")
    arg_s = identity_string(arg, name="selector argument")
    return f"{_TARGET_PARAM_PREFIX}{len(target_s)}:{target_s}{arg_s}"


def decode_selector_param_key(arg: str) -> tuple[str, str] | None:
    """selector parameter key を ``(target, original_arg)`` へ戻す。"""

    text = identity_string(arg, name="selector argument")
    if not text.startswith(_TARGET_PARAM_PREFIX):
        return None
    colon = text.find(":", 1)
    if colon < 0:
        return None
    length_text = text[1:colon]
    if not length_text.isdigit():
        return None
    target_length = int(length_text)
    target_start = colon + 1
    target_end = target_start + target_length
    if target_end > len(text):
        return None
    target = text[target_start:target_end]
    original_arg = text[target_end:]
    if not target or not original_arg:
        return None
    return target, original_arg


def selector_search_terms(op: str, arg: str) -> tuple[str, ...]:
    """内部 namespace を漏らさない selector row の検索語を返す。"""

    if selector_kind(op) is None:
        return (identity_string(arg, name="selector argument"),)
    decoded = decode_selector_param_key(arg)
    if decoded is None:
        return (identity_string(arg, name="selector argument"),)
    return decoded


def selector_help_identity(op: str, arg: str) -> str | None:
    """selector row の公開 Help identity を返す。通常 operation なら None。"""

    kind = selector_kind(op)
    if kind is None:
        return None
    prefix = "G.select" if kind == "primitive" else "E.select"
    decoded = decode_selector_param_key(arg)
    if decoded is None:
        return f"{prefix}.{identity_string(arg, name='selector argument')}"
    target, original_arg = decoded
    return f"{prefix}.{target}.{original_arg}"


def _selector_ui_rule(
    *,
    target: str,
    arg: str,
    arg_keys: Mapping[str, str],
    target_rule: UiVisiblePred | None,
) -> UiVisiblePred:
    activate_key = arg_keys.get("activate")

    def visible(values: Mapping[str, Any]) -> bool:
        if values.get(_TARGET_ARG) != target:
            return False
        if arg != "activate" and activate_key is not None:
            if not bool(values.get(activate_key, True)):
                return False
        if target_rule is None:
            return True
        target_values = {
            original_arg: values.get(encoded_arg)
            for original_arg, encoded_arg in arg_keys.items()
        }
        return bool(target_rule(target_values))

    return visible


def _selector_entries(
    catalog: OperationCatalog,
    *,
    kind: SelectorKind,
    n_inputs: int,
) -> tuple[OperationCatalogEntry, ...]:
    return tuple(
        entry
        for entry in catalog.public_entries(kind=cast(OpKind, kind))
        if kind == "primitive" or entry.n_inputs == n_inputs
    )


def _selector_fingerprint(
    *,
    kind: SelectorKind,
    n_inputs: int,
    entries: tuple[OperationCatalogEntry, ...],
) -> SelectorCatalogFingerprint:
    digest = hashlib.sha256()
    digest.update(b"grafix-selector-catalog-v1\0")
    digest.update(kind.encode("ascii"))
    digest.update(b"\0")
    digest.update(str(n_inputs).encode("ascii"))
    for entry in entries:
        digest.update(b"\0")
        digest.update(entry.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.schema_fingerprint.digest.encode("ascii"))
    return SelectorCatalogFingerprint(digest.hexdigest())


def _build_selector_spec(
    *,
    kind: SelectorKind,
    n_inputs: int,
    entries: tuple[OperationCatalogEntry, ...],
    fingerprint: SelectorCatalogFingerprint,
) -> SelectorSpec:
    names = tuple(entry.name for entry in entries)
    if not names:
        arity = "" if kind == "primitive" else f"（n_inputs={n_inputs}）"
        raise _NoSelectableOperationsError(
            f"選択可能な {kind}{arity} が登録されていません"
        )

    meta: dict[str, ParamMeta] = {
        _TARGET_ARG: ParamMeta(
            kind="choice",
            choices=names,
            display_name="Operation",
            description=_TARGET_META_DESCRIPTION,
        )
    }
    defaults: dict[str, Any] = {_TARGET_ARG: names[0]}
    param_order: list[str] = [_TARGET_ARG]
    ui_visible: dict[str, UiVisiblePred] = {}

    for entry in entries:
        target = entry.name
        target_schema = entry.schema
        ordered_args = tuple(
            dict.fromkeys((*target_schema.param_order, *target_schema.meta.keys()))
        )
        arg_keys = {
            arg: selector_param_key(target, arg)
            for arg in ordered_args
            if arg in target_schema.meta
        }
        for arg in ordered_args:
            target_meta = target_schema.meta.get(arg)
            if target_meta is None:
                continue
            encoded_arg = arg_keys[arg]
            meta[encoded_arg] = replace(
                target_meta,
                display_name=target_meta.display_name or str(arg),
            )
            if arg in target_schema.defaults:
                defaults[encoded_arg] = target_schema.defaults[arg]
            param_order.append(encoded_arg)
            ui_visible[encoded_arg] = _selector_ui_rule(
                target=target,
                arg=arg,
                arg_keys=arg_keys,
                target_rule=target_schema.ui_visible.get(arg),
            )

    op = PRIMITIVE_SELECTOR_OP if kind == "primitive" else effect_selector_op(n_inputs)
    return SelectorSpec(
        op=op,
        kind=kind,
        n_inputs=n_inputs,
        schema=ParameterOpSchema(
            meta=meta,
            defaults=defaults,
            param_order=tuple(param_order),
            ui_visible=ui_visible,
        ),
        fingerprint=fingerprint,
        target_schema_fingerprints={
            entry.name: entry.schema_fingerprint.digest for entry in entries
        },
    )


def selector_spec(
    catalog: OperationCatalog,
    *,
    kind: SelectorKind,
    n_inputs: int,
) -> SelectorSpec:
    """immutable catalog から evaluator を持たない selector schema を返す。"""

    if type(catalog) is not OperationCatalog:
        raise TypeError("catalog は exact OperationCatalog です")
    canonical_kind = cast(
        SelectorKind,
        exact_string_choice(kind, name="selector kind", choices=("primitive", "effect")),
    )
    count = exact_integer(n_inputs, name="selector n_inputs", minimum=0)
    if canonical_kind == "primitive" and count != 0:
        raise ValueError("primitive selector の n_inputs は 0 です")
    if canonical_kind == "effect" and count < 1:
        raise ValueError("effect selector の n_inputs は 1 以上です")
    entries = _selector_entries(catalog, kind=canonical_kind, n_inputs=count)
    fingerprint = _selector_fingerprint(
        kind=canonical_kind,
        n_inputs=count,
        entries=entries,
    )
    cache_key = (canonical_kind, count)
    with _SELECTOR_CACHE_LOCK:
        cached = _SELECTOR_CACHE.get(cache_key)
        if cached is not None and cached.fingerprint == fingerprint:
            return cached.spec
        spec = _build_selector_spec(
            kind=canonical_kind,
            n_inputs=count,
            entries=entries,
            fingerprint=fingerprint,
        )
        _SELECTOR_CACHE[cache_key] = _SelectorCacheEntry(fingerprint, spec)
        return spec


def ensure_primitive_selector_spec() -> SelectorSpec:
    """current immutable catalog の primitive selector schema を返す。"""

    return selector_spec(current_operation_catalog(), kind="primitive", n_inputs=0)


def ensure_effect_selector_spec(n_inputs: int) -> SelectorSpec:
    """current immutable catalog の arity 別 effect selector schema を返す。"""

    count = validate_effect_selector_n_inputs(n_inputs)
    return selector_spec(current_operation_catalog(), kind="effect", n_inputs=count)


def ensure_selector_spec_registered(op: str) -> bool:
    """current catalog から selector schema を構築できるか返す（catalog は変更しない）。"""

    kind = selector_kind(op)
    try:
        if kind == "primitive":
            ensure_primitive_selector_spec()
            return True
        if kind == "effect":
            n_inputs = selector_effect_n_inputs(op)
            if n_inputs is None:
                return False
            ensure_effect_selector_spec(n_inputs)
            return True
    except _NoSelectableOperationsError:
        return False
    return False


def _target_error(
    *,
    kind: SelectorKind,
    target: str,
    choices: tuple[str, ...],
    n_inputs: int | None,
) -> ValueError:
    hint_match = get_close_matches(target, choices, n=1, cutoff=0.55)
    hint = "" if not hint_match else f"。{hint_match[0]!r} の誤りですか？"
    arity = "" if n_inputs is None else f"（n_inputs={n_inputs}）"
    available = ", ".join(repr(choice) for choice in choices) or "（なし）"
    return ValueError(
        f"選択可能な {kind}{arity} に {target!r} はありません{hint}。"
        f"利用可能な候補: {available}"
    )


def validate_selector_target(
    *,
    kind: SelectorKind,
    target: str,
    selector_spec: SelectorSpec,
    n_inputs: int | None,
) -> str:
    """selector schema の固定済み候補に target が含まれることを検証する。"""

    target_s = identity_string(target, name=f"{kind} selector target")
    choices = tuple(selector_spec.schema.meta[_TARGET_ARG].choices or ())
    if target_s.startswith("_") or target_s not in choices:
        raise _target_error(
            kind=kind,
            target=target_s,
            choices=choices,
            n_inputs=n_inputs,
        )
    return target_s


def validated_effect_selector(
    target: str,
    *,
    n_inputs: int,
    catalog: OperationCatalog | None = None,
) -> tuple[str, SelectorSpec]:
    """effect selector の base target と、それを検証した schema を返す。"""

    count = validate_effect_selector_n_inputs(n_inputs)
    selected_catalog = current_operation_catalog() if catalog is None else catalog
    try:
        spec = selector_spec(
            selected_catalog,
            kind="effect",
            n_inputs=count,
        )
    except _NoSelectableOperationsError:
        raise _target_error(
            kind="effect",
            target=identity_string(target, name="effect selector target"),
            choices=(),
            n_inputs=count,
        ) from None
    return (
        validate_selector_target(
            kind="effect",
            target=target,
            selector_spec=spec,
            n_inputs=count,
        ),
        spec,
    )


def validate_effect_selector_target(
    target: str,
    *,
    n_inputs: int,
    catalog: OperationCatalog | None = None,
) -> str:
    """effect selector の base target を指定 catalog の schema で検証する。"""

    validated_target, _ = validated_effect_selector(
        target,
        n_inputs=n_inputs,
        catalog=catalog,
    )
    return validated_target


__all__ = [
    "PRIMITIVE_SELECTOR_OP",
    "SelectorCatalogFingerprint",
    "SelectorKind",
    "SelectorSpec",
    "decode_selector_param_key",
    "effect_selector_op",
    "ensure_effect_selector_spec",
    "ensure_primitive_selector_spec",
    "ensure_selector_spec_registered",
    "selector_effect_n_inputs",
    "selector_help_identity",
    "selector_kind",
    "selector_param_key",
    "selector_search_terms",
    "selector_spec",
    "validate_effect_selector_n_inputs",
    "validate_effect_selector_target",
    "validate_selector_target",
    "validated_effect_selector",
]
