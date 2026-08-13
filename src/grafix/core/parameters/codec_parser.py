"""
Purpose:
    現行ParamStore payloadを一度だけ検証し、detachedなcanonical復元表現へ変換する。
Use when:
    永続schemaの厳密性、部分破損の診断、またはdecode入力契約を変更する場合。
Constraints:
    - schema versionとtop-level契約の不整合は拒否し、future schemaを推測しない。
    - writer定義外のfieldや暗黙の型coercionを許さず、entry局所の問題は診断へ残す。
    - parse中にliveなParamStoreや入力payloadを変更しない。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Generic, TypeVar

from .adjustment_snapshot import (
    ParameterAdjustment,
    ParameterAdjustmentSnapshot,
)
from .collapsed_header import (
    CollapsedHeaderKey,
    decode_collapsed_header_key,
)
from .effects import (
    EffectOrder,
    EffectStepKey,
    EffectStepTopology,
    EffectTopologySignature,
)
from .key import ParameterKey
from .labels import MAX_LABEL_LENGTH
from .meta import ParamMeta
from .meta_spec import PARAM_META_SPEC_KEYS, meta_from_record
from .state import ParamState, ParamStateSnapshot
from .validation import CcKey, validate_cc_key
from .variations import Variation

PARAM_STORE_SCHEMA_VERSION = 4
_PARAM_STORE_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "states",
        "meta",
        "labels",
        "ordinals",
        "effect_steps",
        "chain_ordinals",
        "explicit",
        "ui",
        "variations",
    }
)
_PARAMETER_KEY_FIELDS = frozenset({"op", "site_id", "arg"})
_STATE_FIELDS = _PARAMETER_KEY_FIELDS | {
    "override",
    "ui_value",
    "cc_key",
}
_META_REQUIRED_FIELDS = _PARAMETER_KEY_FIELDS | {
    "kind",
    "ui_min",
    "ui_max",
    "choices",
}
_META_OPTIONAL_FIELDS = PARAM_META_SPEC_KEYS - {
    "kind",
    "ui_min",
    "ui_max",
    "choices",
}
_EXPLICIT_FIELDS = _PARAMETER_KEY_FIELDS | {"explicit"}
_LABEL_FIELDS = frozenset({"op", "site_id", "label"})
_EFFECT_STEP_FIELDS = frozenset({"op", "site_id", "chain_id", "step_index", "n_inputs"})
_UI_FIELDS = frozenset(
    {
        "collapsed_headers",
        "effect_order_overrides",
        "locked_parameters",
        "favorite_parameters",
    }
)
_EFFECT_ORDER_FIELDS = frozenset({"chain_id", "steps"})
_EFFECT_ORDER_STEP_FIELDS = frozenset({"op", "site_id"})
_VARIATION_FIELDS = frozenset(
    {
        "name",
        "created_at",
        "note",
        "seed",
        "t",
        "thumbnail_path",
        "parameter_snapshot",
    }
)
_VARIATION_SNAPSHOT_FIELDS = frozenset(
    {
        "states",
        "meta",
        "collapsed_headers",
        "effect_order_state",
    }
)
_VARIATION_EFFECT_ORDER_FIELDS = frozenset({"chain_id", "topology", "steps"})
_VARIATION_TOPOLOGY_STEP_FIELDS = frozenset({"op", "site_id", "n_inputs"})


class ParamStoreSchemaError(ValueError):
    """ParamStore payload の schema version 表現が無効な場合の例外。"""


class UnsupportedParamStoreSchemaError(ParamStoreSchemaError):
    """現行以外の ParamStore schema を読もうとした場合の例外。"""

    def __init__(self, found_version: int | None) -> None:
        self.found_version = None if found_version is None else int(found_version)
        self.supported_version = PARAM_STORE_SCHEMA_VERSION
        found = "missing" if found_version is None else str(found_version)
        super().__init__(
            f"unsupported ParamStore schema_version {found}; expected {self.supported_version}"
        )


@dataclass(frozen=True, slots=True)
class ParamStoreDecodeIssue:
    """部分復元のために破棄・修復した 1 entry の診断。"""

    section: str
    index: int | None
    reason: str

    def describe(self) -> str:
        """log/診断表示用の安定文字列を返す。"""

        location = self.section if self.index is None else f"{self.section}[{self.index}]"
        return f"{location}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ParsedState:
    """検証・正規化済みの state entry。"""

    value: ParamStateSnapshot


@dataclass(frozen=True, slots=True)
class ParsedParamStore:
    """一回の parse で得た canonical 値と全 issue。"""

    states: Mapping[ParameterKey, ParsedState]
    meta: Mapping[ParameterKey, ParamMeta]
    explicit_by_key: Mapping[ParameterKey, bool]
    labels: Mapping[tuple[str, str], str]
    ordinals: Mapping[str, Mapping[str, int]]
    topologies: Mapping[str, tuple[EffectStepTopology, ...]]
    chain_ordinals: Mapping[str, int]
    effect_order_overrides: Mapping[str, EffectOrder]
    collapsed_headers: frozenset[CollapsedHeaderKey]
    locked_parameters: frozenset[ParameterKey]
    favorite_parameters: frozenset[ParameterKey]
    variations: tuple[Variation, ...]
    issues: tuple[ParamStoreDecodeIssue, ...]

    def __post_init__(self) -> None:
        """parse 結果が所有する mapping を読み取り専用に固定する。"""

        for field_name in (
            "states",
            "meta",
            "explicit_by_key",
            "labels",
            "topologies",
            "chain_ordinals",
            "effect_order_overrides",
        ):
            value = getattr(self, field_name)
            object.__setattr__(
                self,
                field_name,
                MappingProxyType(dict(value)),
            )
        object.__setattr__(
            self,
            "ordinals",
            MappingProxyType(
                {op: MappingProxyType(dict(by_site)) for op, by_site in self.ordinals.items()}
            ),
        )


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class _ParsedSection(Generic[T]):
    """一 section の canonical 値と、その生成中に見つけた issue。"""

    value: T
    issues: tuple[ParamStoreDecodeIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class _ParsedUi:
    collapsed_headers: frozenset[CollapsedHeaderKey]
    effect_order_overrides: dict[str, EffectOrder]
    locked_parameters: frozenset[ParameterKey]
    favorite_parameters: frozenset[ParameterKey]


def param_store_schema_version(obj: object) -> int:
    """payload が現行 schema なら version を返し、それ以外は拒否する。"""

    if not isinstance(obj, dict):
        raise TypeError("ParamStore payload must be a dict")
    if "schema_version" not in obj:
        raise UnsupportedParamStoreSchemaError(None)
    raw_version = obj["schema_version"]
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise ParamStoreSchemaError("schema_version must be an int")
    if raw_version != PARAM_STORE_SCHEMA_VERSION:
        raise UnsupportedParamStoreSchemaError(raw_version)
    return raw_version


def parse_param_store_payload(obj: object) -> ParsedParamStore:
    """現行 schema を一 pass/section で canonical intermediate へ変換する。"""

    param_store_schema_version(obj)
    assert isinstance(obj, dict)
    _validate_top_level_keys(obj)

    meta = _parse_meta(obj["meta"])
    states = _parse_states(obj["states"], meta.value)
    state_keys = frozenset(states.value)
    canonical_meta = {key: value for key, value in meta.value.items() if key in state_keys}
    meta_orphan_issues = tuple(
        ParamStoreDecodeIssue("meta", index, "matching state is missing")
        for index, key in enumerate(meta.value)
        if key not in state_keys
    )
    explicit = _parse_explicit(obj["explicit"], state_keys)
    labels = _parse_labels(obj["labels"])
    required_groups = frozenset((key.op, key.site_id) for key in state_keys)
    ordinals = _parse_ordinals(
        obj["ordinals"],
        required_groups=required_groups,
    )
    topologies = _parse_effect_topologies(obj["effect_steps"])
    chain_ordinals = _parse_chain_ordinals(
        obj["chain_ordinals"],
        topologies=topologies.value,
    )
    ui = _parse_ui(
        obj["ui"],
        state_keys=state_keys,
        topologies=topologies.value,
    )
    variations = _parse_variations(obj["variations"])

    issues = (
        *meta.issues,
        *states.issues,
        *meta_orphan_issues,
        *explicit.issues,
        *labels.issues,
        *ordinals.issues,
        *topologies.issues,
        *chain_ordinals.issues,
        *ui.issues,
        *variations.issues,
    )
    return ParsedParamStore(
        states=states.value,
        meta=canonical_meta,
        explicit_by_key=explicit.value,
        labels=labels.value,
        ordinals=ordinals.value,
        topologies=topologies.value,
        chain_ordinals=chain_ordinals.value,
        effect_order_overrides=ui.value.effect_order_overrides,
        collapsed_headers=ui.value.collapsed_headers,
        locked_parameters=ui.value.locked_parameters,
        favorite_parameters=ui.value.favorite_parameters,
        variations=variations.value,
        issues=tuple(issues),
    )


def _validate_top_level_keys(obj: dict[object, object]) -> None:
    """現行 schema の top-level field が過不足なく存在することを検証する。"""

    if any(not isinstance(key, str) for key in obj):
        raise ParamStoreSchemaError("ParamStore top-level keys must be strings")
    keys = frozenset(key for key in obj if isinstance(key, str))
    missing = sorted(_PARAM_STORE_TOP_LEVEL_KEYS - keys)
    unknown = sorted(keys - _PARAM_STORE_TOP_LEVEL_KEYS)
    if not missing and not unknown:
        return

    details: list[str] = []
    if missing:
        details.append(f"missing={missing!r}")
    if unknown:
        details.append(f"unknown={unknown!r}")
    raise ParamStoreSchemaError("invalid ParamStore top-level fields: " + ", ".join(details))


def _require_record_fields(
    item: Mapping[object, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    """writer が定義する record field 以外を許さず、必須 field を要求する。"""

    if any(not isinstance(key, str) for key in item):
        raise TypeError("field names must be strings")
    keys = frozenset(key for key in item if isinstance(key, str))
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if not missing and not unknown:
        return

    details: list[str] = []
    if missing:
        details.append("missing fields: " + ", ".join(missing))
    if unknown:
        details.append("unknown fields: " + ", ".join(unknown))
    raise ValueError("; ".join(details))


def _parse_key(item: Mapping[str, object]) -> ParameterKey:
    values = tuple(item[field] for field in ("op", "site_id", "arg"))
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("op, site_id, and arg must be non-empty strings")
    op, site_id, arg = values
    assert isinstance(op, str)
    assert isinstance(site_id, str)
    assert isinstance(arg, str)
    return ParameterKey(op=op, site_id=site_id, arg=arg)


def _entry_issue(
    section: str,
    index: int,
    error: Exception,
) -> ParamStoreDecodeIssue:
    reason = str(error)
    if isinstance(error, KeyError) and error.args:
        reason = str(error.args[0])
    return ParamStoreDecodeIssue(section, index, reason or "invalid entry")


def _list_entries(
    value: object,
    section: str,
) -> _ParsedSection[tuple[object, ...]]:
    if not isinstance(value, list):
        return _ParsedSection(
            (),
            (ParamStoreDecodeIssue(section, None, "expected a list"),),
        )
    return _ParsedSection(tuple(value))


def _validate_meta_record(item: Mapping[object, object]) -> None:
    """writer 由来 metadata record の field と非 coercive 型を検証する。"""

    _require_record_fields(
        item,
        required=_META_REQUIRED_FIELDS,
        optional=_META_OPTIONAL_FIELDS,
    )
    kind = item["kind"]
    if not isinstance(kind, str) or not kind.strip():
        raise TypeError("kind must be a non-empty string")
    choices = item["choices"]
    if choices is not None and (
        not isinstance(choices, list) or any(not isinstance(choice, str) for choice in choices)
    ):
        raise TypeError("choices must be a list of strings or null")


def _meta_from_payload(item: Mapping[str, object]) -> ParamMeta:
    try:
        return meta_from_record(item)
    except OverflowError as exc:
        raise ValueError("metadata numeric value is out of range") from exc


def _parse_meta(
    value: object,
    *,
    section: str = "meta",
) -> _ParsedSection[dict[ParameterKey, ParamMeta]]:
    entries = _list_entries(value, section)
    out: dict[ParameterKey, ParamMeta] = {}
    issues = list(entries.issues)
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(ParamStoreDecodeIssue(section, index, "expected an object"))
            continue
        try:
            _validate_meta_record(item)
            key = _parse_key(item)
            meta = _meta_from_payload(item)
            if key in out:
                raise ValueError("duplicate parameter key")
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_entry_issue(section, index, exc))
            continue
        out[key] = meta
    return _ParsedSection(out, tuple(issues))


def _parse_cc_key(value: object) -> tuple[CcKey, str | None]:
    if value is None:
        return None, None
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 127:
            return int(value), None
        return None, "cc_key must be in 0..127"
    if not isinstance(value, list) or len(value) != 3:
        return None, "cc_key must be an int, a three-item list, or null"
    components: list[int | None] = []
    for component in value:
        if component is None:
            components.append(None)
        elif (
            isinstance(component, int) and not isinstance(component, bool) and 0 <= component <= 127
        ):
            components.append(int(component))
        else:
            return None, "cc_key components must be ints in 0..127 or null"
    cc_tuple = (components[0], components[1], components[2])
    if cc_tuple == (None, None, None):
        return None, "cc_key list must contain at least one CC number"
    return cc_tuple, None


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _parse_state_value(value: object, meta: ParamMeta) -> object:
    """writer が生成する kind 別 JSON 型だけを canonical 値へ変換する。"""

    kind = meta.kind
    if kind == "bool":
        if not isinstance(value, bool):
            raise TypeError("ui_value must be a bool for bool parameters")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("ui_value must be an int for int parameters")
        return int(value)
    if kind == "float":
        return _finite_number(value, field="ui_value")
    if kind in {"str", "font"}:
        if not isinstance(value, str):
            raise TypeError(f"ui_value must be a string for {kind} parameters")
        return value
    if kind == "choice":
        if not isinstance(value, str):
            raise TypeError("ui_value must be a string for choice parameters")
        if meta.choices is not None and value not in meta.choices:
            raise ValueError("ui_value must be one of the declared choices")
        return value
    if kind in {"vec3", "rgb"}:
        if not isinstance(value, list) or len(value) != 3:
            raise TypeError(f"ui_value must be a three-item list for {kind} parameters")
        if kind == "vec3":
            return tuple(
                _finite_number(component, field="ui_value component") for component in value
            )
        if any(
            isinstance(component, bool)
            or not isinstance(component, int)
            or component < 0
            or component > 255
            for component in value
        ):
            raise TypeError("ui_value components must be ints in 0..255 for rgb parameters")
        return tuple(int(component) for component in value)

    raise AssertionError(f"unreachable parameter kind: {kind!r}")


def _parse_states(
    value: object,
    meta_by_key: Mapping[ParameterKey, ParamMeta],
    *,
    section: str = "states",
) -> _ParsedSection[dict[ParameterKey, ParsedState]]:
    entries = _list_entries(value, section)
    out: dict[ParameterKey, ParsedState] = {}
    issues = list(entries.issues)
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(ParamStoreDecodeIssue(section, index, "expected an object"))
            continue
        try:
            _require_record_fields(item, required=_STATE_FIELDS)
            key = _parse_key(item)
            meta = meta_by_key.get(key)
            if meta is None:
                raise ValueError("matching meta is missing")
            if key in out:
                raise ValueError("duplicate parameter key")
            override = item["override"]
            if not isinstance(override, bool):
                raise TypeError("override must be a bool")
            cc_key, cc_error = _parse_cc_key(item["cc_key"])
            if cc_error is not None:
                raise TypeError(cc_error)
            cc_key = validate_cc_key(
                cc_key,
                kind=meta.kind,
                op=key.op,
            )
            state = ParamState(
                override=override,
                ui_value=_parse_state_value(item["ui_value"], meta),
                cc_key=cc_key,
            )
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_entry_issue(section, index, exc))
            continue
        out[key] = ParsedState(value=ParamStateSnapshot.from_state(state))
    return _ParsedSection(out, tuple(issues))


def _parse_explicit(
    value: object,
    state_keys: frozenset[ParameterKey],
) -> _ParsedSection[dict[ParameterKey, bool]]:
    entries = _list_entries(value, "explicit")
    out: dict[ParameterKey, bool] = {}
    issues = list(entries.issues)
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(ParamStoreDecodeIssue("explicit", index, "expected an object"))
            continue
        try:
            _require_record_fields(item, required=_EXPLICIT_FIELDS)
            key = _parse_key(item)
            raw_explicit = item["explicit"]
            if not isinstance(raw_explicit, bool):
                raise TypeError("explicit must be a bool")
            if key not in state_keys:
                raise ValueError("matching state/meta is missing")
            if key in out:
                raise ValueError("duplicate parameter key")
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_entry_issue("explicit", index, exc))
            continue
        out[key] = raw_explicit
    for key in state_keys:
        if key in out:
            continue
        issues.append(
            ParamStoreDecodeIssue(
                "explicit",
                None,
                f"metadata is missing for {key.op}/{key.site_id}/{key.arg}",
            )
        )
        out[key] = False
    return _ParsedSection(out, tuple(issues))


def _parse_labels(
    value: object,
) -> _ParsedSection[dict[tuple[str, str], str]]:
    entries = _list_entries(value, "labels")
    out: dict[tuple[str, str], str] = {}
    issues = list(entries.issues)
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(ParamStoreDecodeIssue("labels", index, "expected an object"))
            continue
        try:
            _require_record_fields(item, required=_LABEL_FIELDS)
            op = item["op"]
            site_id = item["site_id"]
            label = item["label"]
            if (
                not isinstance(op, str)
                or not op.strip()
                or not isinstance(site_id, str)
                or not site_id.strip()
                or not isinstance(label, str)
            ):
                raise TypeError("op/site_id must be non-empty strings and label a string")
            group = (op, site_id)
            if group in out:
                raise ValueError("duplicate parameter group")
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_entry_issue("labels", index, exc))
            continue
        if len(label) > MAX_LABEL_LENGTH:
            issues.append(ParamStoreDecodeIssue("labels", index, "label was truncated"))
            label = label[:MAX_LABEL_LENGTH]
        out[group] = label
    return _ParsedSection(out, tuple(issues))


def _parse_ordinals(
    value: object,
    *,
    required_groups: frozenset[tuple[str, str]],
) -> _ParsedSection[dict[str, dict[str, int]]]:
    if not isinstance(value, dict):
        value = {}
        issues = [ParamStoreDecodeIssue("ordinals", None, "expected an object")]
    else:
        issues = []
    out: dict[str, dict[str, int]] = {}
    provided_groups: set[tuple[str, str]] = set()
    entry_index = 0
    for op, raw_mapping in value.items():
        if not isinstance(op, str) or not op.strip() or not isinstance(raw_mapping, dict):
            issues.append(
                ParamStoreDecodeIssue(
                    "ordinals",
                    entry_index,
                    "expected a non-empty op and an object",
                )
            )
            entry_index += 1
            continue
        requested: list[tuple[str, int]] = []
        for site_id, raw_ordinal in raw_mapping.items():
            if isinstance(site_id, str) and site_id.strip():
                provided_groups.add((op, site_id))
            if (
                not isinstance(site_id, str)
                or not site_id.strip()
                or isinstance(raw_ordinal, bool)
                or not isinstance(raw_ordinal, int)
            ):
                issues.append(
                    ParamStoreDecodeIssue(
                        "ordinals",
                        entry_index,
                        "ordinal must be an int",
                    )
                )
                entry_index += 1
                continue
            requested.append((site_id, raw_ordinal))
            entry_index += 1
        if requested:
            values = [ordinal for _site_id, ordinal in requested]
            if set(values) != set(range(1, len(requested) + 1)):
                issues.append(
                    ParamStoreDecodeIssue(
                        "ordinals",
                        None,
                        f"{op} ordinals were compacted",
                    )
                )
            ordered = sorted(requested, key=lambda item: (item[1], item[0]))
            out[op] = {site_id: index for index, (site_id, _ordinal) in enumerate(ordered, start=1)}

    for op, site_id in sorted(required_groups):
        mapping = out.setdefault(op, {})
        if site_id not in mapping:
            if (op, site_id) not in provided_groups:
                issues.append(
                    ParamStoreDecodeIssue(
                        "ordinals",
                        None,
                        f"ordinal is missing for parameter group {op}/{site_id}",
                    )
                )
            mapping[site_id] = len(mapping) + 1
    return _ParsedSection(out, tuple(issues))


def _parse_effect_topologies(
    value: object,
) -> _ParsedSection[dict[str, tuple[EffectStepTopology, ...]]]:
    entries = _list_entries(value, "effect_steps")
    issues = list(entries.issues)
    by_chain: dict[str, list[tuple[int, EffectStepTopology]]] = {}
    invalid_chains: set[str] = set()
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(ParamStoreDecodeIssue("effect_steps", index, "expected an object"))
            continue
        raw_chain_id = item.get("chain_id")
        chain_hint = raw_chain_id if isinstance(raw_chain_id, str) else None
        try:
            _require_record_fields(item, required=_EFFECT_STEP_FIELDS)
            op = item["op"]
            site_id = item["site_id"]
            chain_id = item["chain_id"]
            step_index = item["step_index"]
            n_inputs = item["n_inputs"]
            if (
                not isinstance(op, str)
                or not op.strip()
                or not isinstance(site_id, str)
                or not site_id.strip()
                or not isinstance(chain_id, str)
                or not chain_id.strip()
            ):
                raise ValueError("op, site_id, and chain_id must be non-empty strings")
            if isinstance(step_index, bool) or not isinstance(step_index, int) or step_index < 0:
                raise ValueError("step_index must be an int >= 0")
            if isinstance(n_inputs, bool) or not isinstance(n_inputs, int) or n_inputs < 1:
                raise ValueError("n_inputs must be an int >= 1")
            step = EffectStepTopology(op, site_id, n_inputs, step_index)
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_entry_issue("effect_steps", index, exc))
            if chain_hint:
                invalid_chains.add(chain_hint)
            continue
        by_chain.setdefault(chain_id, []).append((index, step))

    topologies: dict[str, tuple[EffectStepTopology, ...]] = {}
    for chain_id, indexed_steps in by_chain.items():
        if chain_id in invalid_chains:
            continue
        ordered = sorted(indexed_steps, key=lambda item: item[1].code_index)
        indices = [step.code_index for _index, step in ordered]
        if indices != list(range(len(ordered))):
            issues.append(
                ParamStoreDecodeIssue(
                    "effect_steps",
                    ordered[0][0],
                    "step_index must be contiguous from 0",
                )
            )
            continue
        keys = [step.key for _index, step in ordered]
        if len(set(keys)) != len(keys):
            issues.append(
                ParamStoreDecodeIssue(
                    "effect_steps",
                    ordered[0][0],
                    "duplicate step identity",
                )
            )
            continue
        topology = tuple(step for _index, step in ordered)
        topologies[chain_id] = topology
    return _ParsedSection(topologies, tuple(issues))


def _parse_chain_ordinals(
    value: object,
    *,
    topologies: Mapping[str, Sequence[EffectStepTopology]],
) -> _ParsedSection[dict[str, int]]:
    if not isinstance(value, dict):
        raw: dict[object, object] = {}
        issues = [ParamStoreDecodeIssue("chain_ordinals", None, "expected an object")]
    else:
        raw = value
        issues = []
    out: dict[str, int] = {}
    provided_chain_ids: set[str] = set()
    used: set[int] = set()
    for index, (chain_id, ordinal) in enumerate(raw.items()):
        if not isinstance(chain_id, str) or not chain_id.strip():
            issues.append(
                ParamStoreDecodeIssue(
                    "chain_ordinals", index, "chain_id must be a non-empty string"
                )
            )
            continue
        if chain_id not in topologies:
            issues.append(
                ParamStoreDecodeIssue(
                    "chain_ordinals",
                    index,
                    "matching effect topology is missing",
                )
            )
            continue
        provided_chain_ids.add(chain_id)
        if (
            isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or ordinal < 1
            or ordinal in used
        ):
            issues.append(
                ParamStoreDecodeIssue(
                    "chain_ordinals",
                    index,
                    "ordinal must be a unique int >= 1",
                )
            )
            continue
        out[chain_id] = ordinal
        used.add(ordinal)
    next_ordinal = max(used, default=0) + 1
    for chain_id in topologies:
        if chain_id in out:
            continue
        if chain_id not in provided_chain_ids:
            issues.append(
                ParamStoreDecodeIssue(
                    "chain_ordinals",
                    None,
                    f"ordinal is missing for effect chain {chain_id}",
                )
            )
        while next_ordinal in used:
            next_ordinal += 1
        out[chain_id] = next_ordinal
        used.add(next_ordinal)
        next_ordinal += 1
    return _ParsedSection(out, tuple(issues))


def _parse_effect_order_overrides(
    value: object,
    *,
    topologies: Mapping[str, Sequence[EffectStepTopology]],
) -> _ParsedSection[dict[str, EffectOrder]]:
    entries = _list_entries(value, "ui.effect_order_overrides")
    out: dict[str, EffectOrder] = {}
    issues = list(entries.issues)
    seen_chains: set[str] = set()
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(
                ParamStoreDecodeIssue(
                    "ui.effect_order_overrides",
                    index,
                    "expected an object",
                )
            )
            continue
        try:
            _require_record_fields(item, required=_EFFECT_ORDER_FIELDS)
            chain_id = item["chain_id"]
            steps_obj = item["steps"]
            if not isinstance(chain_id, str) or not chain_id.strip():
                raise ValueError("chain_id must be a non-empty string")
            if chain_id in seen_chains:
                raise ValueError("duplicate chain_id")
            topology = topologies.get(chain_id)
            if topology is None:
                raise ValueError("matching effect topology is missing")
            if not isinstance(steps_obj, list) or not steps_obj:
                raise ValueError("steps must be a non-empty list")
            order: list[EffectStepKey] = []
            for step in steps_obj:
                if not isinstance(step, dict):
                    raise ValueError("each step must be an object")
                _require_record_fields(
                    step,
                    required=_EFFECT_ORDER_STEP_FIELDS,
                )
                op = step["op"]
                site_id = step["site_id"]
                if (
                    not isinstance(op, str)
                    or not op.strip()
                    or not isinstance(site_id, str)
                    or not site_id.strip()
                ):
                    raise ValueError("step op and site_id must be non-empty strings")
                order.append((op, site_id))
            if len(set(order)) != len(order):
                raise ValueError("duplicate step")
            code_order = tuple(step.key for step in topology)
            normalized = tuple(order)
            if len(normalized) != len(code_order) or set(normalized) != set(code_order):
                raise ValueError("steps must be an exact permutation of the effect topology")
            n_inputs_by_key = {step.key: step.n_inputs for step in topology}
            if any(
                n_inputs_by_key[key] > 1 and order_index != 0
                for order_index, key in enumerate(normalized)
            ):
                raise ValueError("multi-input effect must remain at the start of its chain")
        except (TypeError, ValueError) as exc:
            issues.append(_entry_issue("ui.effect_order_overrides", index, exc))
            continue
        seen_chains.add(chain_id)
        if normalized != code_order:
            out[chain_id] = normalized
    return _ParsedSection(out, tuple(issues))


def _parse_ui_parameter_keys(
    value: object,
    *,
    section: str,
    state_keys: frozenset[ParameterKey],
) -> _ParsedSection[frozenset[ParameterKey]]:
    entries = _list_entries(value, section)
    out: set[ParameterKey] = set()
    issues = list(entries.issues)
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(
                ParamStoreDecodeIssue(
                    section,
                    index,
                    "expected op/site_id/arg object",
                )
            )
            continue
        try:
            _require_record_fields(item, required=_PARAMETER_KEY_FIELDS)
            key = _parse_key(item)
            if key not in state_keys:
                raise ValueError("matching state/meta is missing")
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_entry_issue(section, index, exc))
            continue
        out.add(key)
    return _ParsedSection(frozenset(out), tuple(issues))


def _parse_ui(
    value: object,
    *,
    state_keys: frozenset[ParameterKey],
    topologies: Mapping[str, Sequence[EffectStepTopology]],
) -> _ParsedSection[_ParsedUi]:
    if not isinstance(value, dict):
        value = {}
        issues = [ParamStoreDecodeIssue("ui", None, "expected an object")]
    else:
        issues = []
        try:
            _require_record_fields(value, required=_UI_FIELDS)
        except (TypeError, ValueError) as exc:
            issues.append(ParamStoreDecodeIssue("ui", None, str(exc)))

    raw_collapsed = value.get("collapsed_headers", [])
    collapsed: set[CollapsedHeaderKey] = set()
    if not isinstance(raw_collapsed, list):
        issues.append(ParamStoreDecodeIssue("ui.collapsed_headers", None, "expected a list"))
    else:
        for index, item in enumerate(raw_collapsed):
            try:
                key = decode_collapsed_header_key(item)
                if key in collapsed:
                    raise ValueError("duplicate collapsed header key")
            except (TypeError, ValueError) as exc:
                issues.append(
                    ParamStoreDecodeIssue(
                        "ui.collapsed_headers",
                        index,
                        str(exc),
                    )
                )
                continue
            collapsed.add(key)

    overrides = _parse_effect_order_overrides(
        value.get("effect_order_overrides", []),
        topologies=topologies,
    )
    locked = _parse_ui_parameter_keys(
        value.get("locked_parameters", []),
        section="ui.locked_parameters",
        state_keys=state_keys,
    )
    favorites = _parse_ui_parameter_keys(
        value.get("favorite_parameters", []),
        section="ui.favorite_parameters",
        state_keys=state_keys,
    )
    issues.extend(overrides.issues)
    issues.extend(locked.issues)
    issues.extend(favorites.issues)
    return _ParsedSection(
        _ParsedUi(
            collapsed_headers=frozenset(collapsed),
            effect_order_overrides=overrides.value,
            locked_parameters=locked.value,
            favorite_parameters=favorites.value,
        ),
        tuple(issues),
    )


def _parse_optional_int(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an int or null")
    return int(value)


def _parse_optional_finite_number(
    value: object,
    *,
    field: str,
) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field=field)


def _parse_variation_collapsed_headers(
    value: object,
    *,
    section: str,
) -> _ParsedSection[dict[CollapsedHeaderKey, bool]]:
    if not isinstance(value, list):
        return _ParsedSection(
            {},
            (ParamStoreDecodeIssue(section, None, "expected a list"),),
        )

    out: dict[CollapsedHeaderKey, bool] = {}
    issues: list[ParamStoreDecodeIssue] = []
    for index, item in enumerate(value):
        try:
            if type(item) is not dict:
                raise TypeError("collapsed header state must be an object")
            collapsed = item.get("collapsed")
            if type(collapsed) is not bool:
                raise TypeError("collapsed must be an exact bool")
            key = decode_collapsed_header_key(
                {field: field_value for field, field_value in item.items() if field != "collapsed"}
            )
            if key in out:
                raise ValueError("duplicate collapsed header key")
        except (TypeError, ValueError) as exc:
            issues.append(
                ParamStoreDecodeIssue(
                    section,
                    index,
                    str(exc),
                )
            )
            continue
        out[key] = collapsed
    return _ParsedSection(out, tuple(issues))


def _parse_variation_order_step(step: object) -> EffectStepKey:
    if not isinstance(step, dict):
        raise TypeError("each order step must be an object")
    _require_record_fields(step, required=_EFFECT_ORDER_STEP_FIELDS)
    op = step["op"]
    site_id = step["site_id"]
    if (
        not isinstance(op, str)
        or not op.strip()
        or not isinstance(site_id, str)
        or not site_id.strip()
    ):
        raise TypeError("order step op and site_id must be non-empty strings")
    return op, site_id


def _parse_variation_topology_step(
    step: object,
) -> tuple[str, str, int]:
    if not isinstance(step, dict):
        raise TypeError("each topology step must be an object")
    _require_record_fields(
        step,
        required=_VARIATION_TOPOLOGY_STEP_FIELDS,
    )
    op = step["op"]
    site_id = step["site_id"]
    n_inputs = step["n_inputs"]
    if (
        not isinstance(op, str)
        or not op.strip()
        or not isinstance(site_id, str)
        or not site_id.strip()
    ):
        raise TypeError("topology op and site_id must be non-empty strings")
    if isinstance(n_inputs, bool) or not isinstance(n_inputs, int) or n_inputs < 1:
        raise TypeError("topology n_inputs must be an int >= 1")
    return op, site_id, int(n_inputs)


def _parse_variation_effect_order_state(
    value: object,
    *,
    section: str,
) -> _ParsedSection[
    tuple[
        dict[str, EffectOrder | None],
        dict[str, EffectTopologySignature],
    ]
]:
    entries = _list_entries(value, section)
    order_state: dict[str, EffectOrder | None] = {}
    signatures: dict[str, EffectTopologySignature] = {}
    issues = list(entries.issues)
    for index, item in enumerate(entries.value):
        if not isinstance(item, dict):
            issues.append(ParamStoreDecodeIssue(section, index, "expected an object"))
            continue
        try:
            _require_record_fields(
                item,
                required=_VARIATION_EFFECT_ORDER_FIELDS,
            )
            chain_id = item["chain_id"]
            if not isinstance(chain_id, str) or not chain_id.strip() or chain_id in order_state:
                raise ValueError("chain_id must be a unique non-empty string")

            topology_obj = item["topology"]
            if not isinstance(topology_obj, list):
                raise TypeError("topology must be a list")
            topology = tuple(_parse_variation_topology_step(step) for step in topology_obj)
            topology_keys = tuple((op, site_id) for op, site_id, _ in topology)
            if len(set(topology_keys)) != len(topology_keys):
                raise ValueError("topology contains duplicate step identities")

            steps_obj = item["steps"]
            if steps_obj is None:
                order: EffectOrder | None = None
            else:
                if not isinstance(steps_obj, list) or not steps_obj:
                    raise TypeError("steps must be a non-empty list or null")
                order = tuple(_parse_variation_order_step(step) for step in steps_obj)
                if (
                    len(order) != len(topology_keys)
                    or len(set(order)) != len(order)
                    or set(order) != set(topology_keys)
                ):
                    raise ValueError("steps must be an exact permutation of topology")
                n_inputs_by_key = {(op, site_id): n_inputs for op, site_id, n_inputs in topology}
                if any(
                    n_inputs_by_key[key] > 1 and order_index != 0
                    for order_index, key in enumerate(order)
                ):
                    raise ValueError("multi-input effect must remain at the start")
        except (KeyError, TypeError, ValueError) as exc:
            issues.append(_entry_issue(section, index, exc))
            continue

        order_state[chain_id] = order
        signatures[chain_id] = tuple(sorted(topology))

    return _ParsedSection(
        (order_state, signatures),
        tuple(issues),
    )


def _parse_variation(
    item: object,
    *,
    index: int,
) -> _ParsedSection[Variation | None]:
    section = f"variations[{index}]"
    if not isinstance(item, dict):
        return _ParsedSection(
            None,
            (ParamStoreDecodeIssue("variations", index, "expected an object"),),
        )

    try:
        _require_record_fields(item, required=_VARIATION_FIELDS)
        snapshot_obj = item["parameter_snapshot"]
        if not isinstance(snapshot_obj, dict):
            raise TypeError("parameter_snapshot must be an object")
        _require_record_fields(
            snapshot_obj,
            required=_VARIATION_SNAPSHOT_FIELDS,
        )

        name = item["name"]
        created_at = _finite_number(
            item["created_at"],
            field="created_at",
        )
        note = item["note"]
        seed = _parse_optional_int(item["seed"], field="seed")
        t = _parse_optional_finite_number(item["t"], field="t")
        thumbnail_path = item["thumbnail_path"]
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if not isinstance(note, str):
            raise TypeError("note must be a string")
        if thumbnail_path is not None and not isinstance(
            thumbnail_path,
            str,
        ):
            raise TypeError("thumbnail_path must be a string or null")
    except (KeyError, TypeError, ValueError) as exc:
        return _ParsedSection(
            None,
            (_entry_issue("variations", index, exc),),
        )

    snapshot_section = f"{section}.parameter_snapshot"
    meta = _parse_meta(
        snapshot_obj["meta"],
        section=f"{snapshot_section}.meta",
    )
    states = _parse_states(
        snapshot_obj["states"],
        meta.value,
        section=f"{snapshot_section}.states",
    )
    state_keys = frozenset(states.value)
    canonical_meta = {key: value for key, value in meta.value.items() if key in state_keys}
    meta_orphan_issues = tuple(
        ParamStoreDecodeIssue(
            f"{snapshot_section}.meta",
            meta_index,
            "matching state is missing",
        )
        for meta_index, key in enumerate(meta.value)
        if key not in state_keys
    )
    collapsed = _parse_variation_collapsed_headers(
        snapshot_obj["collapsed_headers"],
        section=f"{snapshot_section}.collapsed_headers",
    )
    order = _parse_variation_effect_order_state(
        snapshot_obj["effect_order_state"],
        section=f"{snapshot_section}.effect_order_state",
    )
    order_state, topology_signatures = order.value

    try:
        variation = Variation(
            name=name,
            created_at=created_at,
            note=note,
            seed=seed,
            t=t,
            parameter_snapshot=ParameterAdjustmentSnapshot(
                adjustments={
                    key: ParameterAdjustment(
                        state=parsed.value,
                        meta=canonical_meta[key],
                    )
                    for key, parsed in states.value.items()
                },
                collapsed_by_header=collapsed.value,
                effect_order_state=order_state,
                effect_topology_signatures=topology_signatures,
            ),
            thumbnail_path=thumbnail_path,
        )
    except (TypeError, ValueError) as exc:
        return _ParsedSection(
            None,
            (
                *meta.issues,
                *states.issues,
                *meta_orphan_issues,
                *collapsed.issues,
                *order.issues,
                _entry_issue("variations", index, exc),
            ),
        )

    return _ParsedSection(
        variation,
        (
            *meta.issues,
            *states.issues,
            *meta_orphan_issues,
            *collapsed.issues,
            *order.issues,
        ),
    )


def _parse_variations(
    value: object,
) -> _ParsedSection[tuple[Variation, ...]]:
    entries = _list_entries(value, "variations")
    out: list[Variation] = []
    issues = list(entries.issues)
    names: set[str] = set()
    for index, item in enumerate(entries.value):
        parsed = _parse_variation(item, index=index)
        issues.extend(parsed.issues)
        variation = parsed.value
        if variation is None:
            continue
        if variation.name in names:
            issues.append(ParamStoreDecodeIssue("variations", index, "duplicate variation name"))
            continue
        names.add(variation.name)
        out.append(variation)
    return _ParsedSection(tuple(out), tuple(issues))


__all__ = [
    "PARAM_STORE_SCHEMA_VERSION",
    "ParamStoreDecodeIssue",
    "ParamStoreSchemaError",
    "ParsedParamStore",
    "UnsupportedParamStoreSchemaError",
    "param_store_schema_version",
    "parse_param_store_payload",
]
