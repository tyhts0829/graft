"""mutable operation builder と immutable catalog snapshot を分離する。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias, cast

from grafix.core.definition_fingerprint import (
    EvaluationSpecFingerprint,
    ParameterSchemaFingerprint,
)
from grafix.core.operation_declaration import (
    CachePolicy,
    EvaluationOpRef,
    EvaluationOpSpec,
    OpDeclaration,
    OpKind,
)
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.value_validation import exact_bool, exact_string_choice

OperationKey: TypeAlias = tuple[OpKind, str]


def _operation_key(kind: object, name: object) -> OperationKey:
    """catalog lookup 用の canonical operation key を返す。"""

    canonical_kind = exact_string_choice(
        kind,
        name="operation kind",
        choices=("primitive", "effect"),
    )
    canonical_name = identity_string(name, name="operation name")
    return (cast(OpKind, canonical_kind), canonical_name)


@dataclass(frozen=True, slots=True)
class OperationCatalogEntry:
    """declaration と二種類の fingerprint を公開する catalog entry。"""

    declaration: OpDeclaration
    evaluation: EvaluationOpSpec = field(init=False)

    def __post_init__(self) -> None:
        if type(self.declaration) is not OpDeclaration:
            raise TypeError("declaration は exact OpDeclaration である必要があります")
        object.__setattr__(self, "evaluation", self.declaration.evaluation_spec)

    @property
    def name(self) -> str:
        """operation 名。"""

        return self.declaration.name

    @property
    def kind(self) -> OpKind:
        """operation 種別。"""

        return self.declaration.kind

    @property
    def evaluation_fingerprint(self) -> EvaluationSpecFingerprint:
        """geometry evaluation contract の fingerprint。"""

        return self.declaration.evaluation_fingerprint

    @property
    def schema_fingerprint(self) -> ParameterSchemaFingerprint:
        """parameter schema contract の fingerprint。"""

        return self.declaration.schema_fingerprint

    @property
    def ref(self) -> EvaluationOpRef:
        """DAG が保持する exact operation reference。"""

        return self.evaluation.ref

    @property
    def schema(self) -> ParameterOpSchema:
        """selector/GUI が参照する parameter schema。"""

        return self.declaration.schema

    @property
    def evaluator(self) -> Callable[..., RealizedGeometry]:
        """runtime dispatch が呼び出す evaluator。"""

        return self.evaluation.evaluator

    @property
    def n_inputs(self) -> int:
        """effect の入力数。primitive は 0。"""

        return self.evaluation.n_inputs

    @property
    def description(self) -> str:
        """公開 catalog の短い説明。"""

        return self.declaration.description

    @property
    def doc(self) -> str:
        """authoring callable の docstring。"""

        return self.declaration.doc

    @property
    def source(self) -> str | None:
        """authoring callable の source file。"""

        return self.declaration.source

    @property
    def provenance(self) -> str:
        """authoring callable の module/qualname。"""

        return self.declaration.provenance

    @property
    def accepted_args(self) -> tuple[str, ...]:
        """受理する固定 argument 名。"""

        return self.declaration.accepted_args

    @property
    def required_args(self) -> tuple[str, ...]:
        """default を持たない argument 名。"""

        return self.declaration.required_args

    @property
    def accepts_var_kwargs(self) -> bool:
        """任意 keyword argument を受け取るか。"""

        return self.declaration.accepts_var_kwargs

    @property
    def cache_policy(self) -> CachePolicy:
        """evaluation cache policy。"""

        return self.evaluation.cache_policy

    @property
    def defaults(self) -> Mapping[str, Any]:
        """parameter default mapping。"""

        return self.schema.defaults

    @property
    def meta(self) -> Mapping[str, ParamMeta]:
        """parameter metadata mapping。"""

        return self.schema.meta


@dataclass(frozen=True, slots=True)
class OperationCatalog(Mapping[OperationKey, OperationCatalogEntry]):
    """一 generation 内で変化しない operation catalog snapshot。"""

    _entries: Mapping[OperationKey, OperationCatalogEntry]

    def __post_init__(self) -> None:
        entries: dict[OperationKey, OperationCatalogEntry] = {}
        for raw_key, entry in self._entries.items():
            if type(raw_key) is not tuple or len(raw_key) != 2:
                raise TypeError("operation catalog key は (kind, name) tuple です")
            key = _operation_key(raw_key[0], raw_key[1])
            if type(entry) is not OperationCatalogEntry:
                raise TypeError("operation catalog value は exact OperationCatalogEntry です")
            if key != (entry.kind, entry.name):
                raise ValueError("operation catalog key と declaration が一致しません")
            if key in entries:
                raise ValueError(f"operation {key!r} は既に登録されています")
            entries[key] = entry
        object.__setattr__(self, "_entries", MappingProxyType(entries))

    def __getitem__(self, key: OperationKey) -> OperationCatalogEntry:
        canonical_key = _operation_key(key[0], key[1])
        return self._entries[canonical_key]

    def __iter__(self) -> Iterator[OperationKey]:
        return iter(sorted(self._entries))

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        if type(key) is not tuple or len(key) != 2:
            return False
        try:
            canonical_key = _operation_key(key[0], key[1])
        except (TypeError, ValueError):
            return False
        return canonical_key in self._entries

    def resolve(self, kind: OpKind, name: str) -> OperationCatalogEntry:
        """kind/name に一致する entry を返す。"""

        return self._entries[_operation_key(kind, name)]

    def resolve_ref(self, ref: EvaluationOpRef) -> OperationCatalogEntry:
        """evaluation fingerprint まで一致する entry を返す。

        Raises
        ------
        KeyError
            kind/name が catalog に存在しない場合。
        LookupError
            同名 entry の evaluation fingerprint が異なる場合。
        """

        if type(ref) is not EvaluationOpRef:
            raise TypeError("ref は exact EvaluationOpRef である必要があります")
        # EvaluationOpRef は生成時に kind/name を canonical 化済みである。
        # hot path では同じ検証を繰り返さず、immutable catalog を直接引く。
        entry = self._entries[(ref.kind, ref.name)]
        if entry.evaluation_fingerprint != ref.fingerprint:
            raise LookupError(
                f"operation {ref.kind} {ref.name!r} の evaluation fingerprint が一致しません"
            )
        return entry

    def entries(
        self,
        *,
        kind: OpKind | None = None,
    ) -> tuple[OperationCatalogEntry, ...]:
        """entry を deterministic な kind/name 順で返す。"""

        if kind is None:
            keys = sorted(self._entries)
        else:
            canonical_kind = exact_string_choice(
                kind,
                name="operation kind",
                choices=("primitive", "effect"),
            )
            keys = sorted(key for key in self._entries if key[0] == canonical_kind)
        return tuple(self._entries[key] for key in keys)

    def public_entries(self, *, kind: OpKind) -> tuple[OperationCatalogEntry, ...]:
        """private name を除く kind 別 entry を deterministic に返す。"""

        return tuple(entry for entry in self.entries(kind=kind) if not entry.name.startswith("_"))


class OperationCatalogBuilder:
    """operation declaration を検証して snapshot 前に組み立てる builder。"""

    __slots__ = ("_entries",)

    def __init__(self, seed: OperationCatalog | None = None) -> None:
        """空、または既存 snapshot を seed にした builder を作る。"""

        if seed is not None and type(seed) is not OperationCatalog:
            raise TypeError("seed は exact OperationCatalog または None です")
        self._entries: dict[OperationKey, OperationCatalogEntry] = (
            {} if seed is None else dict(seed._entries)
        )

    def register(
        self,
        declaration: OpDeclaration,
        *,
        overwrite: bool = False,
    ) -> OperationCatalogEntry:
        """declaration を一件登録し、同名置換は明示指定時だけ行う。

        duplicate 検証と entry 構築が成功するまで builder は変更しない。
        ``overwrite=True`` でも対象 key 以外の entry は同じ object を保つ。
        """

        if type(declaration) is not OpDeclaration:
            raise TypeError("declaration は exact OpDeclaration である必要があります")
        overwrite_b = exact_bool(overwrite, name="overwrite")
        key = (declaration.kind, declaration.name)
        if key in self._entries and not overwrite_b:
            raise ValueError(f"{declaration.kind} {declaration.name!r} は既に登録されています")
        entry = OperationCatalogEntry(declaration=declaration)
        self._entries[key] = entry
        return entry

    def freeze(self) -> OperationCatalog:
        """現在の entry を defensive copy した immutable snapshot を返す。"""

        return OperationCatalog(self._entries)


def compose_operation_catalogs(
    base: OperationCatalog,
    overlay: OperationCatalog,
) -> OperationCatalog:
    """``overlay`` の同名 declaration を優先して二 snapshot を合成する。"""

    if type(base) is not OperationCatalog or type(overlay) is not OperationCatalog:
        raise TypeError("base/overlay は exact OperationCatalog である必要があります")
    # Catalog は immutable なので、片側が空なら既存 snapshot 自体が合成結果になる。
    # authoring 定義がない通常の G/E 呼び出しで builtin 全件を再検証・再構築しない。
    if not overlay:
        return base
    if not base:
        return overlay
    builder = OperationCatalogBuilder(base)
    for entry in overlay.entries():
        builder.register(entry.declaration, overwrite=True)
    return builder.freeze()


_BOUND_OPERATION_CATALOG: ContextVar[OperationCatalog | None] = ContextVar(
    "grafix_bound_operation_catalog",
    default=None,
)


@contextmanager
def bind_operation_catalog(catalog: OperationCatalog) -> Iterator[OperationCatalog]:
    """現在の execution context へ immutable operation catalog を束縛する。"""

    if type(catalog) is not OperationCatalog:
        raise TypeError("catalog は exact OperationCatalog である必要があります")
    token = _BOUND_OPERATION_CATALOG.set(catalog)
    try:
        yield catalog
    finally:
        _BOUND_OPERATION_CATALOG.reset(token)


def current_operation_catalog() -> OperationCatalog:
    """bound snapshot、または builtin + default authoring snapshot を返す。"""

    # import cycle を作らず、束縛外 authoring convenience の時だけ合成する。
    from grafix.core.authoring_definitions import (
        current_registration_target,
        default_authoring_definitions,
    )
    from grafix.core.builtins import builtin_operation_catalog

    target = current_registration_target()
    if target is not None:
        return target.snapshot().operations

    bound = _BOUND_OPERATION_CATALOG.get()
    if bound is not None:
        return bound

    return compose_operation_catalogs(
        builtin_operation_catalog(),
        default_authoring_definitions.snapshot().operations,
    )


__all__ = [
    "OperationCatalog",
    "OperationCatalogBuilder",
    "OperationCatalogEntry",
    "OperationKey",
    "bind_operation_catalog",
    "compose_operation_catalogs",
    "current_operation_catalog",
]
