"""
Purpose:
    operation の authoring metadata、評価 spec、Geometry/effect step が固定する typed reference を immutable contract として定義する。
Use when:
    operation identity、arity、cache policy、external dependency hook、DAG が保持する version を変更・調査する場合。
Constraints:
    - evaluation/schema fingerprint は declaration 作成時に一度だけ確定し、catalog 構築時に再発行しない。
    - delayed effect step は evaluator と schema の両 fingerprint を固定し、旧 schema と新 evaluator を混ぜない。
    - ``cache_policy="none"`` の動的 operation には明示 version を必須とする。
See:
    grafix.core.definition_fingerprint
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar, cast

from grafix.core.definition_fingerprint import (
    DefinitionFingerprintError,
    EvaluationSpecFingerprint,
    ParameterSchemaFingerprint,
    fingerprint_evaluation_spec,
    fingerprint_parameter_schema,
)
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.parameters.identity import identity_string
from grafix.core.parameters.validation import validate_parameter_value
from grafix.core.value_validation import (
    canonical_immutable_value,
    exact_bool,
    exact_integer,
    exact_string,
    exact_string_choice,
)

OpKind = Literal["primitive", "effect"]
CachePolicy = Literal["content", "none"]
EvaluatorT = TypeVar("EvaluatorT", bound=Callable[..., object])
ExternalDependencyHook = Callable[..., object]
_OP_DECLARATION_ATTRIBUTE = "__grafix_operation_declaration__"

_WRAPPER_OWNED_ARGUMENTS = frozenset({"activate", "instance_key", "key", "shared"})


def _dynamic_evaluation_contract() -> None:
    """明示 version を持つ dynamic operation の stable fingerprint marker。"""


@dataclass(frozen=True, slots=True)
class EvaluationOpRef:
    """DAG node が固定する operation kind/name/evaluation version。"""

    kind: OpKind
    name: str
    fingerprint: EvaluationSpecFingerprint

    def __post_init__(self) -> None:
        kind = cast(
            OpKind,
            exact_string_choice(
                self.kind,
                name="operation ref kind",
                choices=("primitive", "effect"),
            ),
        )
        name = identity_string(self.name, name="operation ref name")
        if type(self.fingerprint) is not EvaluationSpecFingerprint:
            raise TypeError(
                "operation ref fingerprint は exact EvaluationSpecFingerprint である必要があります"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class EffectStepRef:
    """遅延 effect step が固定する evaluator と schema の参照。"""

    operation: EvaluationOpRef
    schema_fingerprint: ParameterSchemaFingerprint

    def __post_init__(self) -> None:
        if type(self.operation) is not EvaluationOpRef:
            raise TypeError("operation は exact EvaluationOpRef である必要があります")
        if self.operation.kind != "effect":
            raise ValueError("EffectStepRef の operation は effect である必要があります")
        if type(self.schema_fingerprint) is not ParameterSchemaFingerprint:
            raise TypeError(
                "schema_fingerprint は exact ParameterSchemaFingerprint である必要があります"
            )


@dataclass(frozen=True, slots=True)
class EvaluationOpSpec(Generic[EvaluatorT]):
    """realization が参照する最小 immutable operation spec。"""

    ref: EvaluationOpRef
    evaluator: EvaluatorT
    n_inputs: int
    cache_policy: CachePolicy
    external_dependency_hook: ExternalDependencyHook | None

    def __post_init__(self) -> None:
        if type(self.ref) is not EvaluationOpRef:
            raise TypeError("ref は exact EvaluationOpRef である必要があります")
        if not callable(self.evaluator):
            raise TypeError("evaluator は callable である必要があります")
        count = exact_integer(self.n_inputs, name="n_inputs", minimum=0)
        if self.ref.kind == "primitive" and count != 0:
            raise ValueError("primitive evaluation spec の n_inputs は 0 です")
        if self.ref.kind == "effect" and count < 1:
            raise ValueError("effect evaluation spec の n_inputs は 1 以上です")
        policy = cast(
            CachePolicy,
            exact_string_choice(
                self.cache_policy,
                name="cache_policy",
                choices=("content", "none"),
            ),
        )
        if self.external_dependency_hook is not None and not callable(
            self.external_dependency_hook
        ):
            raise TypeError("external_dependency_hook は callable または None です")
        object.__setattr__(self, "n_inputs", count)
        object.__setattr__(self, "cache_policy", policy)


@dataclass(frozen=True, slots=True)
class OpDeclaration(Generic[EvaluatorT]):
    """decorator が生成する operation 1 件の immutable declaration。

    Notes
    -----
    ``evaluation_fingerprint`` と ``schema_fingerprint`` は factory で一度だけ
    計算する。catalog snapshot の作成時には再発行しない。
    """

    name: str
    kind: OpKind
    evaluator: EvaluatorT
    schema: ParameterOpSchema
    n_inputs: int
    cache_policy: CachePolicy
    evaluator_abi: str
    version: str | None
    external_dependency_hook: ExternalDependencyHook | None
    evaluation_fingerprint: EvaluationSpecFingerprint
    schema_fingerprint: ParameterSchemaFingerprint
    description: str
    doc: str
    source: str | None
    source_owner: str
    provenance: str
    accepted_args: tuple[str, ...]
    required_args: tuple[str, ...]
    accepts_var_kwargs: bool

    def __post_init__(self) -> None:
        name = identity_string(self.name, name="operation name")
        if name == "concat":
            raise ValueError("'concat' は Grafix 内部予約 operation のため宣言できない")
        kind = cast(
            OpKind,
            exact_string_choice(
                self.kind,
                name="operation kind",
                choices=("primitive", "effect"),
            ),
        )
        if not callable(self.evaluator):
            raise TypeError("evaluator は callable である必要があります")
        if type(self.schema) is not ParameterOpSchema:
            raise TypeError("schema は exact ParameterOpSchema である必要があります")
        n_inputs = exact_integer(self.n_inputs, name="n_inputs", minimum=0)
        if kind == "primitive" and n_inputs != 0:
            raise ValueError("primitive の n_inputs は 0 である必要があります")
        if kind == "effect" and n_inputs < 1:
            raise ValueError("effect の n_inputs は 1 以上である必要があります")
        cache_policy = cast(
            CachePolicy,
            exact_string_choice(
                self.cache_policy,
                name="cache_policy",
                choices=("content", "none"),
            ),
        )
        evaluator_abi = identity_string(self.evaluator_abi, name="evaluator_abi")
        version = (
            None
            if self.version is None
            else identity_string(self.version, name="operation version")
        )
        if cache_policy == "none" and version is None:
            raise ValueError("cache_policy='none' には明示的な version が必要です")
        if self.external_dependency_hook is not None and not callable(
            self.external_dependency_hook
        ):
            raise TypeError("external_dependency_hook は callable または None です")
        if type(self.evaluation_fingerprint) is not EvaluationSpecFingerprint:
            raise TypeError(
                "evaluation_fingerprint は exact EvaluationSpecFingerprint である必要があります"
            )
        if type(self.schema_fingerprint) is not ParameterSchemaFingerprint:
            raise TypeError(
                "schema_fingerprint は exact ParameterSchemaFingerprint である必要があります"
            )

        accepted_args = tuple(
            identity_string(argument, name="accepted_args item") for argument in self.accepted_args
        )
        required_args = tuple(
            identity_string(argument, name="required_args item") for argument in self.required_args
        )
        unknown_required = set(required_args) - set(accepted_args)
        if unknown_required:
            names = ", ".join(sorted(unknown_required))
            raise ValueError(f"required_args は accepted_args に含める必要がある: {names}")

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "n_inputs", n_inputs)
        object.__setattr__(self, "cache_policy", cache_policy)
        object.__setattr__(self, "evaluator_abi", evaluator_abi)
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self,
            "description",
            exact_string(self.description, name="description"),
        )
        object.__setattr__(self, "doc", exact_string(self.doc, name="doc"))
        if self.source is not None:
            object.__setattr__(self, "source", exact_string(self.source, name="source"))
        object.__setattr__(
            self,
            "source_owner",
            identity_string(self.source_owner, name="source_owner"),
        )
        object.__setattr__(
            self,
            "provenance",
            identity_string(self.provenance, name="provenance"),
        )
        object.__setattr__(self, "accepted_args", accepted_args)
        object.__setattr__(self, "required_args", required_args)
        object.__setattr__(
            self,
            "accepts_var_kwargs",
            exact_bool(self.accepts_var_kwargs, name="accepts_var_kwargs"),
        )

    @property
    def ref(self) -> EvaluationOpRef:
        """DAG に固定する evaluation reference を返す。"""

        return EvaluationOpRef(
            kind=self.kind,
            name=self.name,
            fingerprint=self.evaluation_fingerprint,
        )

    @property
    def effect_step_ref(self) -> EffectStepRef:
        """effect step に固定する evaluation/schema reference を返す。

        Raises
        ------
        ValueError
            declaration が primitive の場合。
        """

        if self.kind != "effect":
            raise ValueError("primitive declaration から EffectStepRef は作れません")
        return EffectStepRef(
            operation=self.ref,
            schema_fingerprint=self.schema_fingerprint,
        )

    @property
    def evaluation_spec(self) -> EvaluationOpSpec[EvaluatorT]:
        """authoring metadata を除いた realization 用 spec を返す。"""

        return EvaluationOpSpec(
            ref=self.ref,
            evaluator=self.evaluator,
            n_inputs=self.n_inputs,
            cache_policy=self.cache_policy,
            external_dependency_hook=self.external_dependency_hook,
        )


def _operation_parameters(
    *,
    kind: OpKind,
    evaluator: Callable[..., object],
    n_inputs: int,
) -> tuple[inspect.Parameter, ...]:
    """wrapper が利用できる evaluator signature を検証する。"""

    try:
        parameters = tuple(inspect.signature(evaluator).parameters.values())
    except (TypeError, ValueError) as exc:
        raise TypeError("evaluator signature を取得できません") from exc
    if kind == "effect":
        if len(parameters) < n_inputs:
            raise TypeError(
                f"effect evaluator は Geometry 入力を {n_inputs} 個"
                "位置引数として宣言する必要があります"
            )
        for parameter in parameters[:n_inputs]:
            if parameter.kind not in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            }:
                raise TypeError("effect の Geometry 入力は位置引数である必要があります")
            if parameter.default is not inspect.Parameter.empty:
                raise TypeError("effect の Geometry 入力に default は指定できません")
        operation_parameters = parameters[n_inputs:]
    else:
        operation_parameters = parameters

    for parameter in operation_parameters:
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise TypeError("operation 引数は keyword で受け取れる必要があります")
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise TypeError("operation evaluator に可変位置引数は使用できません")

    reserved = sorted(
        parameter.name for parameter in parameters if parameter.name in _WRAPPER_OWNED_ARGUMENTS
    )
    if reserved:
        raise ValueError(f"wrapper 予約引数は使用できません: {reserved!r}")
    return operation_parameters


def _validate_schema_against_parameters(
    schema: ParameterOpSchema,
    parameters: tuple[inspect.Parameter, ...],
) -> None:
    """evaluator に同名引数がある schema default の一致を検証する。

    ``activate`` のように decorator wrapper が加える schema 引数は元 evaluator
    には存在しない。そのため schema-only 引数はここで推測せず、neutral schema
    の契約としてそのまま受理する。
    """

    by_name = {parameter.name: parameter for parameter in parameters}
    for name, default in schema.defaults.items():
        parameter = by_name.get(name)
        if parameter is None:
            continue
        if parameter.default is inspect.Parameter.empty:
            raise ValueError(f"schema 引数には evaluator default が必要です: {name!r}")
        canonical_default = validate_parameter_value(
            parameter.default,
            kind=schema.meta[name].kind,
            choices=schema.meta[name].choices,
        )
        if canonical_default != default or type(canonical_default) is not type(default):
            raise ValueError(f"schema default が evaluator default と一致しません: {name!r}")

    for parameter in parameters:
        if (
            parameter.name in schema.meta
            or parameter.default is inspect.Parameter.empty
            or parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        ):
            continue
        canonical_immutable_value(
            parameter.default,
            name=f"evaluator default {parameter.name!r}",
        )


def create_op_declaration(
    *,
    name: str,
    kind: OpKind,
    evaluator: EvaluatorT,
    schema: ParameterOpSchema,
    n_inputs: int,
    cache_policy: CachePolicy = "content",
    evaluator_abi: str = "1",
    version: str | None = None,
    external_dependency_hook: ExternalDependencyHook | None = None,
    decorator_options: Mapping[str, object] | None = None,
    source_owner: str | None = None,
    signature_source: Callable[..., object] | None = None,
    fingerprint_source: Callable[..., object] | None = None,
) -> OpDeclaration[EvaluatorT]:
    """検証済み evaluator と schema から immutable declaration を作る。

    Parameters
    ----------
    name : str
        catalog 内の operation 名。
    kind : {"primitive", "effect"}
        operation 種別。
    evaluator : Callable[..., object]
        geometry を評価する callable。
    schema : ParameterOpSchema
        evaluator から分離された parameter schema。
    n_inputs : int
        evaluator が先頭で受け取る Geometry 入力数。
    cache_policy : {"content", "none"}, default="content"
        evaluation cache の利用方針。
    evaluator_abi : str, default="1"
        native/compiled dependency を含む evaluator contract の明示 version。
    version : str | None, default=None
        動的 operation の author 指定 version。``cache_policy="none"`` では必須。
    external_dependency_hook : Callable[..., object] | None, default=None
        lookup ごとの外部依存を解決する hook。
    decorator_options : Mapping[str, object] | None, default=None
        geometry へ影響する追加 decorator option。
    source_owner : str | None, default=None
        reload 差分の所有元。省略時は evaluator module 名を使う。
    signature_source : Callable[..., object] | None, default=None
        authoring signature と説明を取得する callable。評価 adapter を作る
        decorator は元の user callable を渡す。
    fingerprint_source : Callable[..., object] | None, default=None
        evaluator を直接 canonical 化できない builtin だけが使う manifest ABI marker。

    Returns
    -------
    OpDeclaration
        fingerprint を一度だけ確定した immutable declaration。
    """

    name_s = identity_string(name, name="operation name")
    kind_s = cast(
        OpKind,
        exact_string_choice(
            kind,
            name="operation kind",
            choices=("primitive", "effect"),
        ),
    )
    if not callable(evaluator):
        raise TypeError("evaluator は callable である必要があります")
    if type(schema) is not ParameterOpSchema:
        raise TypeError("schema は exact ParameterOpSchema である必要があります")
    n_inputs_i = exact_integer(n_inputs, name="n_inputs", minimum=0)
    if kind_s == "primitive" and n_inputs_i != 0:
        raise ValueError("primitive の n_inputs は 0 である必要があります")
    if kind_s == "effect" and n_inputs_i < 1:
        raise ValueError("effect の n_inputs は 1 以上である必要があります")
    cache_policy_s = cast(
        CachePolicy,
        exact_string_choice(
            cache_policy,
            name="cache_policy",
            choices=("content", "none"),
        ),
    )
    evaluator_abi_s = identity_string(evaluator_abi, name="evaluator_abi")
    version_s = None if version is None else identity_string(version, name="operation version")
    if cache_policy_s == "none" and version_s is None:
        raise ValueError("cache_policy='none' には明示的な version が必要です")
    if external_dependency_hook is not None and not callable(external_dependency_hook):
        raise TypeError("external_dependency_hook は callable または None です")
    if decorator_options is not None and not isinstance(decorator_options, Mapping):
        raise TypeError("decorator_options は mapping または None です")

    authoring_callable = evaluator if signature_source is None else signature_source
    if not callable(authoring_callable):
        raise TypeError("signature_source は callable または None です")
    evaluation_callable = evaluator if fingerprint_source is None else fingerprint_source
    if not callable(evaluation_callable):
        raise TypeError("fingerprint_source は callable または None です")

    parameters = _operation_parameters(
        kind=kind_s,
        evaluator=authoring_callable,
        n_inputs=n_inputs_i,
    )
    _validate_schema_against_parameters(schema, parameters)
    module_name = getattr(authoring_callable, "__module__", None)
    qualname = getattr(
        authoring_callable,
        "__qualname__",
        getattr(authoring_callable, "__name__", None),
    )
    if type(module_name) is not str or not module_name:
        raise TypeError("evaluator に stable な module 名が必要です")
    if type(qualname) is not str or not qualname:
        raise TypeError("evaluator に stable な qualname が必要です")
    owner = (
        module_name
        if source_owner is None
        else identity_string(source_owner, name="source_owner")
    )
    accepted_args = tuple(
        parameter.name
        for parameter in parameters
        if parameter.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
    )
    required_args = tuple(
        parameter.name
        for parameter in parameters
        if parameter.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        and parameter.default is inspect.Parameter.empty
    )

    evaluation_options: dict[str, object] = {
        "kind": kind_s,
        "n_inputs": n_inputs_i,
        "cache_policy": cache_policy_s,
        "evaluator_abi": evaluator_abi_s,
        "version": version_s,
        "external_dependency_hook": external_dependency_hook,
        "decorator_options": {} if decorator_options is None else decorator_options,
        "dynamic_identity": (
            None
            if cache_policy_s != "none"
            else (owner, qualname)
        ),
    }
    try:
        evaluation_fingerprint = fingerprint_evaluation_spec(
            evaluation_callable,
            decorator_options=evaluation_options,
        )
    except DefinitionFingerprintError:
        if cache_policy_s != "none" or fingerprint_source is not None:
            raise
        evaluation_fingerprint = fingerprint_evaluation_spec(
            _dynamic_evaluation_contract,
            decorator_options=evaluation_options,
        )
    schema_fingerprint = fingerprint_parameter_schema(schema)

    doc = inspect.getdoc(authoring_callable) or ""
    description = " ".join(doc.split("\n\n", 1)[0].splitlines()).strip()
    try:
        source = inspect.getsourcefile(authoring_callable)
    except TypeError:
        source = None

    return OpDeclaration(
        name=name_s,
        kind=kind_s,
        evaluator=evaluator,
        schema=schema,
        n_inputs=n_inputs_i,
        cache_policy=cache_policy_s,
        evaluator_abi=evaluator_abi_s,
        version=version_s,
        external_dependency_hook=external_dependency_hook,
        evaluation_fingerprint=evaluation_fingerprint,
        schema_fingerprint=schema_fingerprint,
        description=description,
        doc=doc,
        source=source,
        source_owner=owner,
        provenance=f"{module_name}:{qualname}",
        accepted_args=accepted_args,
        required_args=required_args,
        accepts_var_kwargs=any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
        ),
    )


def attach_operation_declaration(
    func: Callable[..., object],
    declaration: OpDeclaration,
) -> None:
    """decorated callable へ bootstrap が回収する declaration を付与する。"""

    if not callable(func):
        raise TypeError("func は callable である必要があります")
    if type(declaration) is not OpDeclaration:
        raise TypeError("declaration は exact OpDeclaration である必要があります")
    try:
        setattr(func, _OP_DECLARATION_ATTRIBUTE, declaration)
    except (AttributeError, TypeError) as exc:
        raise TypeError("operation callable に declaration を付与できません") from exc


def operation_declaration(func: Callable[..., object]) -> OpDeclaration:
    """decorated callable に付与済みの exact declaration を返す。"""

    if not callable(func):
        raise TypeError("func は callable である必要があります")
    declaration = getattr(func, _OP_DECLARATION_ATTRIBUTE, None)
    if type(declaration) is not OpDeclaration:
        raise LookupError("callable に operation declaration が付与されていません")
    return declaration


__all__ = [
    "CachePolicy",
    "EffectStepRef",
    "EvaluationOpRef",
    "EvaluationOpSpec",
    "ExternalDependencyHook",
    "OpDeclaration",
    "OpKind",
    "attach_operation_declaration",
    "create_op_declaration",
    "operation_declaration",
]
