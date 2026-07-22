"""公開 inspection 用の evaluator-free operation 情報。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from grafix.core.operation_declaration import OpKind
from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.meta import ParamMeta
from grafix.core.value_validation import exact_bool, exact_integer, exact_string


@dataclass(frozen=True, slots=True)
class OperationInfo:
    """利用者が operation を列挙・説明するための不変情報。"""

    name: str
    kind: OpKind
    n_inputs: int
    accepted_args: tuple[str, ...]
    required_args: tuple[str, ...]
    defaults: Mapping[str, Any]
    meta: Mapping[str, ParamMeta]
    description: str
    doc: str
    source: str | None
    provenance: str
    accepts_var_kwargs: bool

    def __post_init__(self) -> None:
        name = identity_string(self.name, name="operation name")
        if self.kind not in {"primitive", "effect"}:
            raise ValueError(f"未知の operation kind です: {self.kind!r}")
        kind = cast(OpKind, self.kind)
        n_inputs = exact_integer(self.n_inputs, name="n_inputs", minimum=0)
        if kind == "primitive" and n_inputs != 0:
            raise ValueError("primitive の n_inputs は 0 です")
        if kind == "effect" and n_inputs < 1:
            raise ValueError("effect の n_inputs は 1 以上です")

        accepted_args = tuple(
            identity_string(argument, name="accepted_args item")
            for argument in self.accepted_args
        )
        required_args = tuple(
            identity_string(argument, name="required_args item")
            for argument in self.required_args
        )
        if not set(required_args) <= set(accepted_args):
            raise ValueError("required_args は accepted_args に含める必要があります")

        defaults = dict(self.defaults)
        meta = dict(self.meta)
        if set(defaults) != set(meta):
            raise ValueError("defaults と meta の引数集合が一致しません")
        if any(type(value) is not ParamMeta for value in meta.values()):
            raise TypeError("meta の値は exact ParamMeta である必要があります")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "n_inputs", n_inputs)
        object.__setattr__(self, "accepted_args", accepted_args)
        object.__setattr__(self, "required_args", required_args)
        object.__setattr__(self, "defaults", MappingProxyType(defaults))
        object.__setattr__(self, "meta", MappingProxyType(meta))
        object.__setattr__(
            self,
            "description",
            exact_string(self.description, name="description"),
        )
        object.__setattr__(self, "doc", exact_string(self.doc, name="doc"))
        if self.source is not None:
            object.__setattr__(
                self,
                "source",
                exact_string(self.source, name="source"),
            )
        object.__setattr__(
            self,
            "provenance",
            identity_string(self.provenance, name="provenance"),
        )
        object.__setattr__(
            self,
            "accepts_var_kwargs",
            exact_bool(self.accepts_var_kwargs, name="accepts_var_kwargs"),
        )


__all__ = ["OperationInfo"]
