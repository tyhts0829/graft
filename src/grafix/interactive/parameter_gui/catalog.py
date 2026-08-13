"""
Purpose:
    operation/preset catalogを、evaluatorを持たないParameter GUI用schema snapshotへ射影する。
Use when:
    GUIのoperation分類、selector表示、parameter metadata、またはcatalog交換を変更する場合。
Constraints:
    - 同じgenerationのoperation/preset schemaから一度にcaptureする。
    - evaluatorやmutable catalog ownerをGUI entryへ含めない。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias, cast

from grafix.core.operation_catalog import OperationCatalog, current_operation_catalog
from grafix.core.operation_declaration import OpKind
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.operation_selector import selector_spec
from grafix.core.parameters.identity import identity_string
from grafix.core.preset_catalog import PresetCatalog, current_preset_catalog
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string_choice,
)

ParameterGuiEntryKind: TypeAlias = Literal[
    "primitive",
    "effect",
    "preset",
    "selector",
]


@dataclass(frozen=True, slots=True)
class ParameterGuiCatalogEntry:
    """1 parameter operation の evaluator-free GUI view。"""

    op: str
    kind: ParameterGuiEntryKind
    call_name: str
    schema: ParameterOpSchema
    n_inputs: int
    accepted_args: tuple[str, ...]
    accepts_var_kwargs: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "op", identity_string(self.op, name="parameter op"))
        kind = cast(
            ParameterGuiEntryKind,
            exact_string_choice(
                self.kind,
                name="parameter GUI entry kind",
                choices=("primitive", "effect", "preset", "selector"),
            ),
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "call_name",
            identity_string(self.call_name, name="parameter call name"),
        )
        if type(self.schema) is not ParameterOpSchema:
            raise TypeError("schema は exact ParameterOpSchema である必要があります")
        n_inputs = exact_integer(self.n_inputs, name="n_inputs", minimum=0)
        if kind in {"primitive", "preset"} and n_inputs != 0:
            raise ValueError(f"{kind} GUI entry の n_inputs は 0 です")
        if kind == "effect" and n_inputs < 1:
            raise ValueError("effect GUI entry の n_inputs は 1 以上です")
        object.__setattr__(self, "n_inputs", n_inputs)
        object.__setattr__(
            self,
            "accepted_args",
            tuple(
                identity_string(argument, name="accepted argument")
                for argument in self.accepted_args
            ),
        )
        object.__setattr__(
            self,
            "accepts_var_kwargs",
            exact_bool(self.accepts_var_kwargs, name="accepts_var_kwargs"),
        )


@dataclass(frozen=True, slots=True, eq=False)
class ParameterGuiCatalog:
    """1 GUI session が所有する immutable schema snapshot。

    ``eq=False`` は catalog generation の identity を object identity とするためである。
    table cache は mutable registry revision ではなく、この固定 object を key に使う。
    """

    _operations: Mapping[tuple[OpKind, str], ParameterGuiCatalogEntry]
    _presets: Mapping[str, ParameterGuiCatalogEntry]
    _selectors: Mapping[str, ParameterGuiCatalogEntry]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_operations",
            MappingProxyType(dict(self._operations)),
        )
        object.__setattr__(self, "_presets", MappingProxyType(dict(self._presets)))
        object.__setattr__(
            self,
            "_selectors",
            MappingProxyType(dict(self._selectors)),
        )

    @classmethod
    def capture(
        cls,
        operations: OperationCatalog,
        presets: PresetCatalog,
    ) -> ParameterGuiCatalog:
        """operation/preset snapshot を evaluator-free projection へ一度だけ変換する。"""

        if type(operations) is not OperationCatalog:
            raise TypeError("operations は exact OperationCatalog である必要があります")
        if type(presets) is not PresetCatalog:
            raise TypeError("presets は exact PresetCatalog である必要があります")

        projected_operations: dict[tuple[OpKind, str], ParameterGuiCatalogEntry] = {}
        for source in operations.entries():
            projected_operations[(source.kind, source.name)] = ParameterGuiCatalogEntry(
                op=source.name,
                kind=cast(ParameterGuiEntryKind, source.kind),
                call_name=source.name,
                schema=source.schema,
                n_inputs=source.n_inputs,
                accepted_args=source.accepted_args,
                accepts_var_kwargs=source.accepts_var_kwargs,
            )

        projected_presets: dict[str, ParameterGuiCatalogEntry] = {}
        for declaration in presets.declarations():
            op = declaration.display_op
            projected_presets[op] = ParameterGuiCatalogEntry(
                op=op,
                kind="preset",
                call_name=declaration.name,
                schema=declaration.schema,
                n_inputs=0,
                accepted_args=declaration.schema.param_order,
                accepts_var_kwargs=False,
            )

        projected_selectors: dict[str, ParameterGuiCatalogEntry] = {}
        if operations.public_entries(kind="primitive"):
            primitive_selector = selector_spec(
                operations,
                kind="primitive",
                n_inputs=0,
            )
            projected_selectors[primitive_selector.op] = ParameterGuiCatalogEntry(
                op=primitive_selector.op,
                kind="selector",
                call_name="select",
                schema=primitive_selector.schema,
                n_inputs=0,
                accepted_args=tuple(primitive_selector.schema.meta),
                accepts_var_kwargs=False,
            )

        effect_arities = sorted(
            {entry.n_inputs for entry in operations.public_entries(kind="effect")}
        )
        for n_inputs in effect_arities:
            effect_selector = selector_spec(
                operations,
                kind="effect",
                n_inputs=n_inputs,
            )
            projected_selectors[effect_selector.op] = ParameterGuiCatalogEntry(
                op=effect_selector.op,
                kind="selector",
                call_name="select",
                schema=effect_selector.schema,
                n_inputs=n_inputs,
                accepted_args=tuple(effect_selector.schema.meta),
                accepts_var_kwargs=False,
            )

        return cls(
            _operations=projected_operations,
            _presets=projected_presets,
            _selectors=projected_selectors,
        )

    def resolve_operation(
        self,
        kind: OpKind,
        name: str,
    ) -> ParameterGuiCatalogEntry:
        """kind/name が一致する operation schema entry を返す。"""

        return self._operations[(kind, identity_string(name, name="operation name"))]

    def resolve(self, op: str) -> ParameterGuiCatalogEntry | None:
        """ParameterKey.op に対応する entry を旧分類優先順で返す。"""

        name = identity_string(op, name="parameter op")
        preset = self._presets.get(name)
        if preset is not None:
            return preset
        primitive = self._operations.get(("primitive", name))
        if primitive is not None:
            return primitive
        effect = self._operations.get(("effect", name))
        if effect is not None:
            return effect
        return self._selectors.get(name)

    def contains(self, op: str, *, kind: ParameterGuiEntryKind | None = None) -> bool:
        """op が存在し、任意の kind と一致するか返す。"""

        entry = self.resolve(op)
        return entry is not None and (kind is None or entry.kind == kind)

    def is_preset(self, op: str) -> bool:
        """op が preset parameter identity なら True を返す。"""

        return self.contains(op, kind="preset")

    def is_primitive_parameter(self, op: str) -> bool:
        """op が primitive または primitive selector なら True を返す。"""

        entry = self.resolve(op)
        return entry is not None and (
            entry.kind == "primitive" or (entry.kind == "selector" and entry.n_inputs == 0)
        )

    def is_effect_parameter(self, op: str) -> bool:
        """op が effect または effect selector なら True を返す。"""

        entry = self.resolve(op)
        return entry is not None and (
            entry.kind == "effect" or (entry.kind == "selector" and entry.n_inputs > 0)
        )

    def entries(
        self,
        *,
        kind: ParameterGuiEntryKind | None = None,
    ) -> tuple[ParameterGuiCatalogEntry, ...]:
        """GUI entry を deterministic な kind/op 順で返す。"""

        all_entries = (
            *self._operations.values(),
            *self._presets.values(),
            *self._selectors.values(),
        )
        return tuple(
            sorted(
                (entry for entry in all_entries if kind is None or entry.kind == kind),
                key=lambda entry: (entry.kind, entry.op),
            )
        )

    def __iter__(self) -> Iterator[ParameterGuiCatalogEntry]:
        return iter(self.entries())


def current_parameter_gui_catalog() -> ParameterGuiCatalog:
    """現在束縛された operation/preset catalog を GUI snapshot へ固定する。"""

    return ParameterGuiCatalog.capture(
        current_operation_catalog(),
        current_preset_catalog(),
    )


__all__ = [
    "ParameterGuiCatalog",
    "ParameterGuiCatalogEntry",
    "ParameterGuiEntryKind",
    "current_parameter_gui_catalog",
]
