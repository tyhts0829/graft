"""
Purpose:
    operation の evaluation contract と parameter schema を、process を跨いで比較できる canonical fingerprint にする。
Use when:
    declaration identity、geometry cache invalidation、source reload の catalog 一致判定を変更・調査する場合。
Constraints:
    - process counter、object identity、absolute path、import 順を identity に使わない。
    - evaluation fingerprint と schema fingerprint を分離し、schema-only 変更で geometry cache を失効させない。
    - canonical 化できない定義は弱い fallback identity に逃げず明示的に拒否する。
See:
    architecture.md §4
"""

from __future__ import annotations

import dataclasses
import dis
import functools
import hashlib
import inspect
import math
import struct
import sys
import types
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final, NoReturn, cast

from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.python_module_identity import canonical_authoring_module_name

_DIGEST_LENGTH: Final = 64
_LOCATION_GLOBALS: Final = frozenset({"__file__", "__cached__", "__loader__"})
_MODULE_CONTENT_FINGERPRINT_ATTRIBUTE: Final = "__grafix_content_fingerprint__"


class DefinitionFingerprintError(ValueError):
    """定義の意味を決定的な bytes に変換できない場合の例外。"""


def _validate_digest(value: object, *, name: str) -> str:
    """SHA-256 の lowercase hex digest を検証する。"""

    if type(value) is not str:
        raise TypeError(f"{name} は SHA-256 hex 文字列である必要があります")
    if len(value) != _DIGEST_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} は SHA-256 lowercase hex 文字列である必要があります")
    return value


@dataclass(frozen=True, slots=True, order=True)
class EvaluationSpecFingerprint:
    """evaluator と geometry へ影響する契約の opaque fingerprint。"""

    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "digest",
            _validate_digest(self.digest, name="evaluation fingerprint"),
        )

    def __str__(self) -> str:
        """永続化に使える hex digest を返す。"""

        return self.digest


@dataclass(frozen=True, slots=True, order=True)
class ParameterSchemaFingerprint:
    """parameter schema と selector 表示契約の opaque fingerprint。"""

    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "digest",
            _validate_digest(self.digest, name="parameter schema fingerprint"),
        )

    def __str__(self) -> str:
        """永続化に使える hex digest を返す。"""

        return self.digest


@dataclass(frozen=True, slots=True, order=True)
class ModuleContentFingerprint:
    """loader が実行した exact module bytes の typed SHA-256 identity。"""

    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "digest",
            _validate_digest(self.digest, name="module content fingerprint"),
        )

    def __str__(self) -> str:
        return self.digest


def attach_module_content_fingerprint(
    module: types.ModuleType,
    content: bytes,
) -> ModuleContentFingerprint:
    """module へ、実行直前の source bytes と一致する typed identity を付与する。"""

    if type(module) is not types.ModuleType:
        raise TypeError("module は exact ModuleType です")
    if type(content) is not bytes:
        raise TypeError("content は exact bytes です")
    fingerprint = ModuleContentFingerprint(hashlib.sha256(content).hexdigest())
    setattr(module, _MODULE_CONTENT_FINGERPRINT_ATTRIBUTE, fingerprint)
    return fingerprint


def _frame(tag: bytes, *parts: bytes) -> bytes:
    """型 tag と length-prefix 付き payload を曖昧さなく連結する。"""

    framed = bytearray(tag)
    framed.extend(b"[")
    for part in parts:
        framed.extend(len(part).to_bytes(8, "big"))
        framed.extend(part)
    framed.extend(b"]")
    return bytes(framed)


def _symbol_name(value: object) -> str:
    """診断用に object identity を含まない型名を返す。"""

    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


class _CanonicalEncoder:
    """definition graph を決定的な typed bytes へ変換する。"""

    def __init__(self) -> None:
        self._active: set[int] = set()

    def encode(
        self,
        value: object,
        *,
        path: str,
        local_module: str | None = None,
    ) -> bytes:
        """対応する値を canonical bytes へ変換する。"""

        value_type = type(value)
        if value is None:
            return _frame(b"none")
        if value is Ellipsis:
            return _frame(b"ellipsis")
        if value is NotImplemented:
            return _frame(b"not-implemented")
        if value_type is bool:
            return _frame(b"bool", b"1" if value else b"0")
        if value_type is int:
            return _frame(b"int", str(value).encode("ascii"))
        if value_type is float:
            float_value = cast(float, value)
            if not math.isfinite(float_value):
                self._error(path, value, "非有限 float は使用できません")
            return _frame(b"float64", struct.pack(">d", float_value))
        if value_type is complex:
            complex_value = cast(complex, value)
            if not math.isfinite(complex_value.real) or not math.isfinite(complex_value.imag):
                self._error(path, value, "非有限 complex は使用できません")
            return _frame(
                b"complex128",
                struct.pack(">d", complex_value.real),
                struct.pack(">d", complex_value.imag),
            )
        if value_type is str:
            return _frame(b"str", cast(str, value).encode("utf-8"))
        if value_type is bytes:
            return _frame(b"bytes", cast(bytes, value))
        if value_type is range:
            range_value = cast(range, value)
            return _frame(
                b"range",
                self.encode(range_value.start, path=f"{path}.start"),
                self.encode(range_value.stop, path=f"{path}.stop"),
                self.encode(range_value.step, path=f"{path}.step"),
            )
        if value_type is slice:
            slice_value = cast(slice, value)
            return _frame(
                b"slice",
                self.encode(slice_value.start, path=f"{path}.start"),
                self.encode(slice_value.stop, path=f"{path}.stop"),
                self.encode(slice_value.step, path=f"{path}.step"),
            )
        if isinstance(value, Enum):
            enum_type = type(value)
            return _frame(
                b"enum",
                canonical_authoring_module_name(enum_type.__module__).encode("utf-8"),
                enum_type.__qualname__.encode("utf-8"),
                value.name.encode("utf-8"),
            )
        if isinstance(value, types.CodeType):
            return self._encode_code(value, path=path)
        if isinstance(value, types.ModuleType):
            return self._encode_module(value, path=path)
        if isinstance(value, functools.partial):
            return self._encode_partial(
                value,
                path=path,
                local_module=local_module,
            )
        if isinstance(value, types.MethodType):
            return self._encode_method(
                value,
                path=path,
                local_module=local_module,
            )
        if isinstance(value, types.FunctionType):
            if local_module is not None and value.__module__ != local_module:
                return self._encode_external_symbol(value, path=path)
            return self._encode_python_function(value, path=path)
        if inspect.isbuiltin(value):
            return self._encode_external_symbol(value, path=path)
        if isinstance(value, type):
            return self._encode_external_symbol(value, path=path)
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return self._encode_dataclass(
                value,
                path=path,
                local_module=local_module,
            )
        if isinstance(value, Mapping):
            return self._encode_mapping(
                value,
                path=path,
                local_module=local_module,
            )
        if value_type in {tuple, list}:
            return self._encode_sequence(
                cast(Sequence[object], value),
                path=path,
                local_module=local_module,
                tag=b"tuple" if value_type is tuple else b"list",
            )
        if value_type in {frozenset, set}:
            return self._encode_set(
                cast(Set[object], value),
                path=path,
                local_module=local_module,
                tag=b"frozenset" if value_type is frozenset else b"set",
            )
        if callable(value) and self._has_external_symbol_identity(value):
            return self._encode_external_symbol(value, path=path)
        self._error(path, value, "canonical 化できない値です")

    def _encode_sequence(
        self,
        value: Sequence[object],
        *,
        path: str,
        local_module: str | None,
        tag: bytes,
    ) -> bytes:
        self._enter(value, path=path)
        try:
            return _frame(
                tag,
                *(
                    self.encode(
                        item,
                        path=f"{path}[{index}]",
                        local_module=local_module,
                    )
                    for index, item in enumerate(value)
                ),
            )
        finally:
            self._leave(value)

    def _encode_set(
        self,
        value: Set[object],
        *,
        path: str,
        local_module: str | None,
        tag: bytes,
    ) -> bytes:
        self._enter(value, path=path)
        try:
            items = sorted(
                self.encode(
                    item,
                    path=f"{path}[]",
                    local_module=local_module,
                )
                for item in value
            )
            return _frame(tag, *items)
        finally:
            self._leave(value)

    def _encode_mapping(
        self,
        value: Mapping[object, object],
        *,
        path: str,
        local_module: str | None,
    ) -> bytes:
        self._enter(value, path=path)
        try:
            items: list[bytes] = []
            for key, item in value.items():
                key_bytes = self.encode(
                    key,
                    path=f"{path}.<key>",
                    local_module=local_module,
                )
                item_bytes = self.encode(
                    item,
                    path=f"{path}[{self._path_key(key)}]",
                    local_module=local_module,
                )
                items.append(_frame(b"item", key_bytes, item_bytes))
            items.sort()
            return _frame(b"mapping", *items)
        finally:
            self._leave(value)

    def _encode_dataclass(
        self,
        value: object,
        *,
        path: str,
        local_module: str | None,
    ) -> bytes:
        self._enter(value, path=path)
        try:
            value_type = type(value)
            parts = [
                canonical_authoring_module_name(value_type.__module__).encode("utf-8"),
                value_type.__qualname__.encode("utf-8"),
            ]
            for field in dataclasses.fields(cast(Any, value)):
                parts.append(
                    _frame(
                        b"field",
                        field.name.encode("utf-8"),
                        self.encode(
                            getattr(value, field.name),
                            path=f"{path}.{field.name}",
                            local_module=local_module,
                        ),
                    )
                )
            return _frame(b"dataclass", *parts)
        finally:
            self._leave(value)

    def _encode_partial(
        self,
        value: functools.partial[Any],
        *,
        path: str,
        local_module: str | None,
    ) -> bytes:
        self._enter(value, path=path)
        try:
            return _frame(
                b"partial",
                self.encode(
                    value.func,
                    path=f"{path}.func",
                    local_module=local_module,
                ),
                self.encode(
                    value.args,
                    path=f"{path}.args",
                    local_module=local_module,
                ),
                self.encode(
                    {} if value.keywords is None else value.keywords,
                    path=f"{path}.keywords",
                    local_module=local_module,
                ),
            )
        finally:
            self._leave(value)

    def _encode_method(
        self,
        value: types.MethodType,
        *,
        path: str,
        local_module: str | None,
    ) -> bytes:
        self._enter(value, path=path)
        try:
            return _frame(
                b"bound-method",
                self.encode(
                    value.__func__,
                    path=f"{path}.__func__",
                    local_module=local_module,
                ),
                self.encode(
                    value.__self__,
                    path=f"{path}.__self__",
                    local_module=local_module,
                ),
            )
        finally:
            self._leave(value)

    def _encode_python_function(
        self,
        value: types.FunctionType,
        *,
        path: str,
    ) -> bytes:
        if id(value) in self._active:
            return _frame(b"recursive-callable")
        self._enter(value, path=path)
        try:
            module_name = value.__module__ if type(value.__module__) is str else ""
            closure_parts: list[bytes] = []
            closure = value.__closure__ or ()
            if len(closure) != len(value.__code__.co_freevars):
                self._error(path, value, "closure と freevars が一致しません")
            for name, cell in zip(value.__code__.co_freevars, closure, strict=True):
                try:
                    cell_value = cell.cell_contents
                except ValueError:
                    cell_bytes = _frame(b"empty-cell")
                else:
                    cell_bytes = self.encode(
                        cell_value,
                        path=f"{path}.closure.{name}",
                        # decorator wrapper が別 module の元 evaluator を閉じ込める
                        # 場合も、external symbol 扱いせず callable 本体を追跡する。
                        local_module=None,
                    )
                closure_parts.append(_frame(b"closure", name.encode("utf-8"), cell_bytes))

            dependency_parts: list[bytes] = []
            raw_builtins = value.__globals__.get("__builtins__")
            builtins_namespace: Mapping[str, object]
            if isinstance(raw_builtins, Mapping):
                builtins_namespace = raw_builtins
            elif isinstance(raw_builtins, types.ModuleType):
                builtins_namespace = vars(raw_builtins)
            else:
                self._error(path, raw_builtins, "builtins namespace が不正です")
            for name in sorted(self._referenced_global_names(value.__code__)):
                if name in _LOCATION_GLOBALS:
                    raise DefinitionFingerprintError(
                        f"{path}.globals.{name}: location-dependent global は使用できません"
                    )
                if name in value.__globals__:
                    dependency = value.__globals__[name]
                elif name in builtins_namespace:
                    dependency = builtins_namespace[name]
                else:
                    raise DefinitionFingerprintError(
                        f"{path}.globals.{name}: referenced global を解決できません"
                    )
                dependency_parts.append(
                    _frame(
                        b"global",
                        name.encode("utf-8"),
                        self.encode(
                            dependency,
                            path=f"{path}.globals.{name}",
                            local_module=module_name,
                        ),
                    )
                )

            return _frame(
                b"python-function-v1",
                self._encode_code(value.__code__, path=f"{path}.__code__"),
                self.encode(
                    value.__defaults__,
                    path=f"{path}.__defaults__",
                    local_module=module_name,
                ),
                self.encode(
                    {} if value.__kwdefaults__ is None else value.__kwdefaults__,
                    path=f"{path}.__kwdefaults__",
                    local_module=module_name,
                ),
                _frame(b"closure-values", *closure_parts),
                _frame(b"referenced-globals", *dependency_parts),
            )
        finally:
            self._leave(value)

    @classmethod
    def _referenced_global_names(cls, code: types.CodeType) -> frozenset[str]:
        """code と内包する nested code が実際に load する global 名を返す。"""

        names = {
            cast(str, instruction.argval)
            for instruction in dis.get_instructions(code)
            if instruction.opname in {"LOAD_GLOBAL", "LOAD_NAME", "LOAD_FROM_DICT_OR_GLOBALS"}
            and type(instruction.argval) is str
        }
        for constant in code.co_consts:
            if isinstance(constant, types.CodeType):
                names.update(cls._referenced_global_names(constant))
        return frozenset(names)

    def _encode_code(self, value: types.CodeType, *, path: str) -> bytes:
        """filename と line table を除いた code 契約を固定する。"""

        constants = tuple(
            self.encode(constant, path=f"{path}.consts[{index}]")
            for index, constant in enumerate(value.co_consts)
        )
        return _frame(
            b"code-v1",
            self.encode(value.co_argcount, path=f"{path}.argcount"),
            self.encode(value.co_posonlyargcount, path=f"{path}.posonlyargcount"),
            self.encode(value.co_kwonlyargcount, path=f"{path}.kwonlyargcount"),
            self.encode(value.co_nlocals, path=f"{path}.nlocals"),
            self.encode(value.co_stacksize, path=f"{path}.stacksize"),
            self.encode(value.co_flags, path=f"{path}.flags"),
            self.encode(value.co_code, path=f"{path}.bytecode"),
            _frame(b"constants", *constants),
            self.encode(value.co_names, path=f"{path}.names"),
            self.encode(value.co_varnames, path=f"{path}.varnames"),
            self.encode(value.co_freevars, path=f"{path}.freevars"),
            self.encode(value.co_cellvars, path=f"{path}.cellvars"),
            self.encode(value.co_exceptiontable, path=f"{path}.exceptiontable"),
        )

    @staticmethod
    def _fingerprint_module_name(value: types.ModuleType) -> str:
        """module または最長 parent package の canonical 名を返す。"""

        actual_name = value.__name__
        explicit = getattr(value, "__grafix_fingerprint_name__", None)
        if explicit is not None:
            if type(explicit) is not str or not explicit:
                raise DefinitionFingerprintError(
                    "module.__grafix_fingerprint_name__: 空でない str が必要です"
                )
            return explicit
        parts = actual_name.split(".")
        for count in range(len(parts) - 1, 0, -1):
            parent_name = ".".join(parts[:count])
            parent = sys.modules.get(parent_name)
            if parent is None:
                continue
            parent_fingerprint_name = getattr(
                parent,
                "__grafix_fingerprint_name__",
                None,
            )
            if parent_fingerprint_name is None:
                continue
            if type(parent_fingerprint_name) is not str or not parent_fingerprint_name:
                raise DefinitionFingerprintError(
                    f"{parent_name}.__grafix_fingerprint_name__: 空でない str が必要です"
                )
            suffix = ".".join(parts[count:])
            return f"{parent_fingerprint_name}.{suffix}"
        return canonical_authoring_module_name(actual_name)

    def _encode_module(self, value: types.ModuleType, *, path: str) -> bytes:
        name = self._fingerprint_module_name(value)
        if type(name) is not str or not name:
            self._error(path, value, "module name がありません")

        content_fingerprint = getattr(
            value,
            _MODULE_CONTENT_FINGERPRINT_ATTRIBUTE,
            None,
        )
        if content_fingerprint is not None:
            if type(content_fingerprint) is not ModuleContentFingerprint:
                raise DefinitionFingerprintError(
                    f"{path}.{_MODULE_CONTENT_FINGERPRINT_ATTRIBUTE}: "
                    "exact ModuleContentFingerprint が必要です"
                )
            return _frame(
                b"module-snapshot-content-v1",
                name.encode("utf-8"),
                content_fingerprint.digest.encode("ascii"),
            )

        version = getattr(value, "__version__", None)
        if type(version) in {str, int, float, tuple}:
            try:
                version_bytes = self.encode(version, path=f"{path}.__version__")
            except DefinitionFingerprintError:
                version_bytes = b""
            else:
                return _frame(
                    b"module-version-v1",
                    name.encode("utf-8"),
                    version_bytes,
                )

        source_path = self._module_content_path(value)
        if source_path is not None:
            try:
                content = source_path.read_bytes()
            except OSError as exc:
                raise DefinitionFingerprintError(
                    f"{path}: module content を読み取れません: {name}"
                ) from exc
            return _frame(
                b"module-content-v1",
                name.encode("utf-8"),
                hashlib.sha256(content).digest(),
            )

        return _frame(
            b"module-runtime-v1",
            name.encode("utf-8"),
            sys.implementation.name.encode("utf-8"),
            str(sys.implementation.cache_tag).encode("utf-8"),
            bytes(sys.version_info[:3]),
        )

    @staticmethod
    def _module_content_path(value: types.ModuleType) -> Path | None:
        raw_path = getattr(value, "__file__", None)
        if type(raw_path) is not str or not raw_path:
            return None
        path = Path(raw_path)
        if path.suffix in {".pyc", ".pyo"}:
            source = path.with_suffix(".py")
            if source.is_file():
                return source
        return path if path.is_file() else None

    def _encode_external_symbol(self, value: object, *, path: str) -> bytes:
        module_name = getattr(value, "__module__", None)
        qualname = getattr(value, "__qualname__", None)
        if type(qualname) is not str:
            qualname = getattr(value, "__name__", None)
        if type(module_name) is not str or not module_name or type(qualname) is not str:
            self._error(path, value, "stable な module/qualname がありません")
        module = sys.modules.get(module_name)
        if module is None:
            raise DefinitionFingerprintError(
                f"{path}: dependency module がロードされていません: {module_name}"
            )
        fingerprint_module_name = self._fingerprint_module_name(module)
        return _frame(
            b"external-symbol-v1",
            fingerprint_module_name.encode("utf-8"),
            qualname.encode("utf-8"),
            self._encode_module(module, path=f"{path}.__module__"),
        )

    @staticmethod
    def _has_external_symbol_identity(value: object) -> bool:
        module_name = getattr(value, "__module__", None)
        symbol_name = getattr(value, "__qualname__", getattr(value, "__name__", None))
        return type(module_name) is str and type(symbol_name) is str

    def _enter(self, value: object, *, path: str) -> None:
        marker = id(value)
        if marker in self._active:
            raise DefinitionFingerprintError(f"{path}: 循環参照は canonical 化できません")
        self._active.add(marker)

    def _leave(self, value: object) -> None:
        self._active.remove(id(value))

    @staticmethod
    def _path_key(value: object) -> str:
        if type(value) in {str, int, bool}:
            return str(value)
        return f"<{_symbol_name(value)}>"

    @staticmethod
    def _error(path: str, value: object, reason: str) -> NoReturn:
        raise DefinitionFingerprintError(f"{path}: {reason} ({_symbol_name(value)})")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fingerprint_evaluation_spec(
    evaluator: Callable[..., object],
    *,
    decorator_options: Mapping[str, object] | None = None,
) -> EvaluationSpecFingerprint:
    """evaluator と geometry 向け decorator option の fingerprint を返す。

    Parameters
    ----------
    evaluator : Callable[..., object]
        評価本体。Python function の code、default、closure、実際に参照する
        global/helper を追跡する。
    decorator_options : Mapping[str, object] | None
        ``n_inputs``、cache policy、ABI version など評価結果へ影響する option。

    Returns
    -------
    EvaluationSpecFingerprint
        process、checkout path、定義行、mapping 順に依存しない fingerprint。

    Raises
    ------
    DefinitionFingerprintError
        dependency を canonical bytes に変換できない場合。
    TypeError
        evaluator または option mapping の型が不正な場合。
    """

    if not callable(evaluator):
        raise TypeError("evaluator は callable である必要があります")
    if decorator_options is not None and not isinstance(decorator_options, Mapping):
        raise TypeError("decorator_options は mapping または None である必要があります")

    encoder = _CanonicalEncoder()
    payload = _frame(
        b"evaluation-spec-fingerprint-v1",
        encoder.encode(evaluator, path="evaluator"),
        encoder.encode(
            {} if decorator_options is None else decorator_options,
            path="decorator_options",
        ),
    )
    return EvaluationSpecFingerprint(_sha256(payload))


def fingerprint_parameter_schema(
    schema: ParameterOpSchema,
) -> ParameterSchemaFingerprint:
    """parameter schema と ``ui_visible`` の fingerprint を返す。

    Parameters
    ----------
    schema : ParameterOpSchema
        evaluator から独立した immutable parameter schema。

    Returns
    -------
    ParameterSchemaFingerprint
        mapping の挿入順に依存しない schema fingerprint。

    Raises
    ------
    DefinitionFingerprintError
        metadata、default、predicate を canonical 化できない場合。
    TypeError
        ``schema`` が :class:`ParameterOpSchema` でない場合。
    """

    if type(schema) is not ParameterOpSchema:
        raise TypeError("schema は exact ParameterOpSchema である必要があります")
    encoder = _CanonicalEncoder()
    payload = _frame(
        b"parameter-schema-fingerprint-v1",
        encoder.encode(schema.meta, path="schema.meta"),
        encoder.encode(schema.defaults, path="schema.defaults"),
        encoder.encode(schema.param_order, path="schema.param_order"),
        encoder.encode(schema.ui_visible, path="schema.ui_visible"),
    )
    return ParameterSchemaFingerprint(_sha256(payload))


__all__ = [
    "DefinitionFingerprintError",
    "EvaluationSpecFingerprint",
    "ModuleContentFingerprint",
    "ParameterSchemaFingerprint",
    "attach_module_content_fingerprint",
    "fingerprint_evaluation_spec",
    "fingerprint_parameter_schema",
]
