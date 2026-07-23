"""selector schema discovery と exact catalog runtime dispatch を接続する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias

from grafix.core.operation_catalog import OperationCatalog, current_operation_catalog
from grafix.core.operation_declaration import OpDeclaration
from grafix.core.operation_selector import (
    PRIMITIVE_SELECTOR_OP,
    SelectorKind,
    SelectorSpec,
    effect_selector_op,
    selector_param_key,
    selector_spec,
    validated_effect_selector,
    validate_effect_selector_n_inputs,
    validate_selector_target,
)
from grafix.core.parameters.identity import identity_string
from grafix.core.value_validation import exact_bool

from ._op_validation import validate_operation_kwargs
from ._param_resolution import resolve_api_params

ParamsByTarget: TypeAlias = Mapping[str, Mapping[str, Any]] | None
FrozenTargetParams: TypeAlias = tuple[
    tuple[str, tuple[tuple[str, Any], ...]],
    ...,
]


@dataclass(frozen=True, slots=True)
class ResolvedSelection:
    """selector が選んだ実 operation と Geometry 引数。"""

    selector_op: str
    target: str
    params: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True, slots=True)
class FrozenParamsByTarget:
    """一つの catalog/selector に対して固定した target 別引数。"""

    catalog: OperationCatalog
    selector: SelectorSpec
    values: FrozenTargetParams

    def __post_init__(self) -> None:
        if type(self.catalog) is not OperationCatalog:
            raise TypeError("catalog は exact OperationCatalog です")
        if type(self.selector) is not SelectorSpec:
            raise TypeError("selector は exact SelectorSpec です")
        if type(self.values) is not tuple:
            raise TypeError("values は tuple です")

    def __hash__(self) -> int:
        """catalog object identity に依存しない immutable hash を返す。"""

        return hash((self.selector.fingerprint, self.values))


def _selected_catalog(catalog: OperationCatalog | None) -> OperationCatalog:
    if catalog is None:
        return current_operation_catalog()
    if type(catalog) is not OperationCatalog:
        raise TypeError("catalog は exact OperationCatalog または None です")
    return catalog


def _target_declaration(
    *,
    catalog: OperationCatalog,
    selector: SelectorSpec,
    kind: SelectorKind,
    target: str,
) -> OpDeclaration:
    entry = catalog.resolve(kind, target)
    expected = selector.target_schema_fingerprints.get(target)
    if expected != entry.schema_fingerprint.digest:
        raise LookupError(
            f"selector schema と {kind} {target!r} の schema fingerprint が一致しません"
        )
    return entry.declaration


def freeze_params_by_target(
    params_by_target: ParamsByTarget,
    *,
    kind: SelectorKind,
    n_inputs: int | None = None,
    catalog: OperationCatalog | None = None,
    selector: SelectorSpec | None = None,
) -> FrozenParamsByTarget:
    """target 別 kwargs を一つの immutable catalog/schema に対して固定する。"""

    selected_catalog = _selected_catalog(catalog)
    if kind == "primitive":
        count = 0
    else:
        if n_inputs is None:
            raise ValueError("effect selector には n_inputs が必要です")
        count = validate_effect_selector_n_inputs(n_inputs)
    expected_selector = selector_spec(
        selected_catalog,
        kind=kind,
        n_inputs=count,
    )
    if selector is None:
        selected_selector = expected_selector
    else:
        if type(selector) is not SelectorSpec:
            raise TypeError("selector は exact SelectorSpec または None です")
        if (
            selector.kind != kind
            or selector.n_inputs != count
            or selector.fingerprint != expected_selector.fingerprint
        ):
            raise LookupError("selector schema と operation catalog が一致しません")
        selected_selector = selector
    return _freeze_params_by_target(
        params_by_target,
        catalog=selected_catalog,
        selector=selected_selector,
        kind=kind,
        n_inputs=n_inputs,
    )


def freeze_effect_params_by_target(
    target: str,
    params_by_target: ParamsByTarget,
    *,
    n_inputs: int,
    catalog: OperationCatalog | None = None,
) -> tuple[str, FrozenParamsByTarget]:
    """effect target と target 別引数を一つの selector snapshot に固定する。"""

    count = validate_effect_selector_n_inputs(n_inputs)
    selected_catalog = _selected_catalog(catalog)
    selected_target, selected_selector = validated_effect_selector(
        target,
        n_inputs=count,
        catalog=selected_catalog,
    )
    return selected_target, _freeze_params_by_target(
        params_by_target,
        catalog=selected_catalog,
        selector=selected_selector,
        kind="effect",
        n_inputs=count,
    )


def _freeze_params_by_target(
    params_by_target: ParamsByTarget,
    *,
    catalog: OperationCatalog,
    selector: SelectorSpec,
    kind: SelectorKind,
    n_inputs: int | None,
) -> FrozenParamsByTarget:
    """検証済み selector に target 別 kwargs を固定する。"""

    if params_by_target is None:
        return FrozenParamsByTarget(catalog, selector, ())
    if not isinstance(params_by_target, Mapping):
        raise TypeError("params_by_target は mapping または None である必要があります")

    frozen: list[tuple[str, tuple[tuple[str, Any], ...]]] = []
    for raw_target, raw_params in params_by_target.items():
        target = validate_selector_target(
            kind=kind,
            target=identity_string(raw_target, name="params_by_target target"),
            selector_spec=selector,
            n_inputs=n_inputs,
        )
        if not isinstance(raw_params, Mapping):
            raise TypeError(f"params_by_target[{target!r}] は mapping である必要があります")
        params = dict(raw_params)
        if any(type(name) is not str for name in params):
            raise TypeError("target parameter 名は str である必要があります")
        declaration = _target_declaration(
            catalog=catalog,
            selector=selector,
            kind=kind,
            target=target,
        )
        canonical = validate_operation_kwargs(
            op=target,
            spec=declaration,
            params=params,
        )
        frozen.append((target, tuple(sorted(canonical.items()))))
    frozen.sort(key=lambda item: item[0])
    return FrozenParamsByTarget(
        catalog,
        selector,
        tuple(frozen),
    )


def _params_for_target(frozen: FrozenParamsByTarget, target: str) -> dict[str, Any]:
    for name, items in frozen.values:
        if name == target:
            return dict(items)
    return {}


def _resolve_selection(
    *,
    kind: SelectorKind,
    selector: SelectorSpec,
    catalog: OperationCatalog,
    base_target: str,
    target_explicit: bool,
    frozen_params: FrozenParamsByTarget,
    site_id: str,
    n_inputs: int | None,
) -> ResolvedSelection:
    target_is_explicit = exact_bool(target_explicit, name="target_explicit")
    base_target_s = validate_selector_target(
        kind=kind,
        target=base_target,
        selector_spec=selector,
        n_inputs=n_inputs,
    )
    resolved_target = resolve_api_params(
        op=selector.op,
        site_id=site_id,
        user_params={"target": base_target_s},
        defaults={},
        meta={"target": selector.schema.meta["target"]},
        explicit_args={"target"} if target_is_explicit else set(),
    )["target"]
    target = validate_selector_target(
        kind=kind,
        target=identity_string(resolved_target, name="resolved selector target"),
        selector_spec=selector,
        n_inputs=n_inputs,
    )
    declaration = _target_declaration(
        catalog=catalog,
        selector=selector,
        kind=kind,
        target=target,
    )
    target_schema = declaration.schema
    code_params = validate_operation_kwargs(
        op=target,
        spec=declaration,
        params=_params_for_target(frozen_params, target),
    )

    visible_user_params = {
        selector_param_key(target, arg): value
        for arg, value in code_params.items()
        if arg in target_schema.meta
    }
    visible_defaults = {
        selector_param_key(target, arg): value
        for arg, value in target_schema.defaults.items()
        if arg in target_schema.meta
    }
    visible_meta = {
        selector_param_key(target, arg): selector.schema.meta[
            selector_param_key(target, arg)
        ]
        for arg in target_schema.meta
    }
    resolved_visible = resolve_api_params(
        op=selector.op,
        site_id=site_id,
        user_params=visible_user_params,
        defaults=visible_defaults,
        meta=visible_meta,
    )

    params = {
        arg: value for arg, value in code_params.items() if arg not in target_schema.meta
    }
    params.update(
        {
            arg: resolved_visible[selector_param_key(target, arg)]
            for arg in target_schema.meta
            if selector_param_key(target, arg) in resolved_visible
        }
    )
    missing = tuple(arg for arg in declaration.required_args if arg not in params)
    if missing:
        names = ", ".join(repr(name) for name in missing)
        raise TypeError(f"{kind} {target!r} に必要な引数がありません: {names}")
    return ResolvedSelection(selector_op=selector.op, target=target, params=params)


def resolve_primitive_selection(
    *,
    target: str,
    target_explicit: bool,
    params_by_target: FrozenParamsByTarget,
    site_id: str,
) -> ResolvedSelection:
    """primitive selector を exact catalog entry へ lower する。"""

    if type(params_by_target) is not FrozenParamsByTarget:
        raise TypeError("params_by_target は exact FrozenParamsByTarget です")
    selected_catalog = params_by_target.catalog
    selected_selector = params_by_target.selector
    if selected_selector.kind != "primitive" or selected_selector.n_inputs != 0:
        raise LookupError("primitive selector schema が一致しません")
    return _resolve_selection(
        kind="primitive",
        selector=selected_selector,
        catalog=selected_catalog,
        base_target=target,
        target_explicit=target_explicit,
        frozen_params=params_by_target,
        site_id=site_id,
        n_inputs=None,
    )


def resolve_effect_selection(
    *,
    target: str,
    target_explicit: bool,
    n_inputs: int,
    params_by_target: FrozenParamsByTarget,
    site_id: str,
) -> ResolvedSelection:
    """effect selector を exact catalog entry へ lower する。"""

    count = validate_effect_selector_n_inputs(n_inputs)
    if type(params_by_target) is not FrozenParamsByTarget:
        raise TypeError("params_by_target は exact FrozenParamsByTarget です")
    selected_catalog = params_by_target.catalog
    selected_selector = params_by_target.selector
    if selected_selector.kind != "effect" or selected_selector.n_inputs != count:
        raise LookupError("effect selector schema または n_inputs が一致しません")
    return _resolve_selection(
        kind="effect",
        selector=selected_selector,
        catalog=selected_catalog,
        base_target=target,
        target_explicit=target_explicit,
        frozen_params=params_by_target,
        site_id=site_id,
        n_inputs=count,
    )


__all__ = [
    "FrozenParamsByTarget",
    "PRIMITIVE_SELECTOR_OP",
    "ParamsByTarget",
    "ResolvedSelection",
    "effect_selector_op",
    "freeze_effect_params_by_target",
    "freeze_params_by_target",
    "resolve_effect_selection",
    "resolve_primitive_selection",
    "selector_param_key",
    "validate_effect_selector_n_inputs",
]
