"""
Purpose:
    一 generation の evaluation contract と、external dependency を preflight する bounded resource owner を定義する。
Use when:
    evaluation/cache identity、quality/config isolation、external asset lease、resource lifetime を変更・調査する場合。
Constraints:
    - ``EvaluationContext`` は immutable で close 対象でなく、``EvaluationResources`` と lifecycle を混ぜない。
    - cache identity は参照した operation、quality/config、lookup 時の external dependency に限り、catalog 全体の revision を入れない。
    - preflight fingerprint と evaluator resource は同じ lease に束ね、evaluator が path を再解決・再 open しない。
Side effects:
    preflight は operation の external hook を実行し、owner-local resource を構築・再利用することがある。
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import struct
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeVar, cast

from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.operation_catalog import OperationCatalog
from grafix.core.operation_declaration import EvaluationOpRef
from grafix.core.preview_quality import PreviewQuality
from grafix.core.value_validation import canonical_immutable_value

if TYPE_CHECKING:
    from grafix.core.font_resources import FontResources
    from grafix.core.geometry import Geometry, GeometryId


_DIGEST_SIZE = 64
_EMPTY_EXTERNAL_DEPENDENCIES_DIGEST = hashlib.sha256(
    b"grafix.external-dependencies.v1[]"
).hexdigest()
_MAX_PROVIDER_LISTS = 4096
_ResourceT = TypeVar("_ResourceT")


def _digest(value: object, *, name: str) -> str:
    """SHA-256 lowercase hex digest を検証して返す。"""

    if type(value) is not str:
        raise TypeError(f"{name} は SHA-256 hex 文字列である必要があります")
    if len(value) != _DIGEST_SIZE or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{name} は SHA-256 lowercase hex 文字列である必要があります")
    return value


def _frame(tag: bytes, *parts: bytes) -> bytes:
    framed = bytearray(tag)
    framed.extend(b"[")
    for part in parts:
        framed.extend(len(part).to_bytes(8, "big"))
        framed.extend(part)
    framed.extend(b"]")
    return bytes(framed)


def _canonical_context_value(value: object) -> bytes:
    """EvaluationConfig の閉じた値集合を型付き bytes にする。"""

    value_type = type(value)
    if value is None:
        return _frame(b"none")
    if value_type is bool:
        return _frame(b"bool", b"1" if value else b"0")
    if value_type is int:
        return _frame(b"int", str(value).encode("ascii"))
    if value_type is float:
        number = cast(float, value)
        if not math.isfinite(number):
            raise ValueError("evaluation context に非有限 float は使用できません")
        return _frame(b"float64", struct.pack(">d", 0.0 if number == 0.0 else number))
    if value_type is str:
        return _frame(b"str", cast(str, value).encode("utf-8"))
    if isinstance(value, Path):
        return _frame(b"path", value.as_posix().encode("utf-8"))
    if value_type is tuple:
        return _frame(
            b"tuple",
            *(_canonical_context_value(item) for item in cast(tuple[object, ...], value)),
        )
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        parts = [type(value).__qualname__.encode("utf-8")]
        for item in dataclasses.fields(value):
            parts.append(
                _frame(
                    b"field",
                    item.name.encode("utf-8"),
                    _canonical_context_value(getattr(value, item.name)),
                )
            )
        return _frame(b"dataclass", *parts)
    raise TypeError(
        "evaluation context fingerprint に使用できない値型です: "
        f"{value_type.__module__}.{value_type.__qualname__}"
    )


@dataclass(frozen=True, slots=True, order=True)
class EvaluationFingerprint:
    """quality と effective config の opaque fingerprint。"""

    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "digest",
            _digest(self.digest, name="evaluation fingerprint"),
        )

    def __str__(self) -> str:
        return self.digest


@dataclass(frozen=True, slots=True, order=True)
class ExternalDependenciesFingerprint:
    """lookup 時点の可変 external dependency fingerprint。"""

    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "digest",
            _digest(self.digest, name="external dependencies fingerprint"),
        )

    def __str__(self) -> str:
        return self.digest


EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT = ExternalDependenciesFingerprint(
    _EMPTY_EXTERNAL_DEPENDENCIES_DIGEST
)


def evaluation_fingerprint(
    *,
    quality: PreviewQuality,
    config: EvaluationConfig,
) -> EvaluationFingerprint:
    """quality と effective config から決定的 fingerprint を作る。"""

    if quality not in {"draft", "final"}:
        raise ValueError(f"unknown preview quality: {quality!r}")
    if type(config) is not EvaluationConfig:
        raise TypeError("config は exact EvaluationConfig である必要があります")
    payload = _frame(
        b"grafix.evaluation-context.v1",
        quality.encode("ascii"),
        _canonical_context_value(config),
    )
    return EvaluationFingerprint(hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """一 catalog generation の評価契約を固定した immutable value。"""

    catalog: OperationCatalog
    quality: PreviewQuality
    config: EvaluationConfig
    fingerprint: EvaluationFingerprint = field(init=False)

    def __post_init__(self) -> None:
        if type(self.catalog) is not OperationCatalog:
            raise TypeError("catalog は exact OperationCatalog である必要があります")
        if self.quality not in {"draft", "final"}:
            raise ValueError(f"unknown preview quality: {self.quality!r}")
        if type(self.config) is not EvaluationConfig:
            raise TypeError("config は exact EvaluationConfig である必要があります")
        object.__setattr__(
            self,
            "fingerprint",
            evaluation_fingerprint(quality=self.quality, config=self.config),
        )

    def __hash__(self) -> int:
        """mapping/callable identity を使わず value fingerprint で hash する。"""

        return hash(self.fingerprint)


@dataclass(frozen=True, slots=True)
class _ExternalDependencyRequest:
    operation: EvaluationOpRef
    args: tuple[tuple[str, object], ...]
    geometry_id: GeometryId


@dataclass(frozen=True, slots=True)
class ExternalDependencyLease:
    """preflight fingerprint と evaluator が使う同一 resource を束ねる。"""

    fingerprint: object
    resource: object
    fingerprint_value: object = field(init=False)

    def __post_init__(self) -> None:
        fingerprint = self.fingerprint
        canonical_method = getattr(fingerprint, "canonical_value", None)
        raw_value = canonical_method() if callable(canonical_method) else fingerprint
        canonical = canonical_immutable_value(
            raw_value,
            name="external dependency lease fingerprint",
        )
        object.__setattr__(
            self,
            "fingerprint_value",
            canonical_immutable_value(
                (
                    type(fingerprint).__module__,
                    type(fingerprint).__qualname__,
                    canonical,
                ),
                name="typed external dependency lease fingerprint",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExternalDependencySnapshot:
    """root lookup 一回の fingerprint と node 別 lease resource。"""

    fingerprint: ExternalDependenciesFingerprint
    leases: Mapping[GeometryId, object]

    def __post_init__(self) -> None:
        if type(self.fingerprint) is not ExternalDependenciesFingerprint:
            raise TypeError(
                "fingerprint は exact ExternalDependenciesFingerprint である必要があります"
            )
        leases: dict[GeometryId, object] = {}
        for geometry_id, resource in self.leases.items():
            if type(geometry_id) is not str or not geometry_id:
                raise TypeError("external dependency geometry id は空でない str です")
            leases[geometry_id] = resource
        object.__setattr__(self, "leases", MappingProxyType(leases))


_CURRENT_EXTERNAL_DEPENDENCY: ContextVar[object | None] = ContextVar(
    "grafix_current_external_dependency",
    default=None,
)


@contextmanager
def bind_external_dependency(
    snapshot: ExternalDependencySnapshot,
    geometry_id: GeometryId,
) -> Iterator[None]:
    """node evaluator 区間だけ preflight 済み resource を束縛する。"""

    if type(snapshot) is not ExternalDependencySnapshot:
        raise TypeError("snapshot は exact ExternalDependencySnapshot です")
    if type(geometry_id) is not str or not geometry_id:
        raise TypeError("geometry_id は空でない str です")
    resource = snapshot.leases.get(geometry_id)
    if resource is None:
        yield
        return
    token = _CURRENT_EXTERNAL_DEPENDENCY.set(resource)
    try:
        yield
    finally:
        _CURRENT_EXTERNAL_DEPENDENCY.reset(token)


def current_external_dependency(expected_type: type[_ResourceT]) -> _ResourceT:
    """現在 node の preflight resource を exact expected type として返す。"""

    if not isinstance(expected_type, type):
        raise TypeError("expected_type は type である必要があります")
    resource = _CURRENT_EXTERNAL_DEPENDENCY.get()
    if type(resource) is not expected_type:
        raise RuntimeError(
            "preflight 済み external dependency がありません: "
            f"expected={expected_type.__module__}.{expected_type.__qualname__}"
        )
    return cast(_ResourceT, resource)


class EvaluationResources:
    """generation 間で借用される bounded evaluation resource owner。

    external-dependency provider list の bounded memo と、遅延構築する
    :class:`~grafix.core.font_resources.FontResources` を保持する。子
    ``RealizeSession`` はこの owner を閉じず、generation owner が最後に閉じる。
    """

    __slots__ = ("_closed", "_fonts", "_lock", "_provider_lists")

    def __init__(self) -> None:
        self._closed = False
        self._fonts: object | None = None
        self._lock = threading.Lock()
        self._provider_lists: OrderedDict[
            GeometryId,
            tuple[_ExternalDependencyRequest, ...],
        ] = OrderedDict()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _providers(
        self,
        geometry: Geometry,
        context: EvaluationContext,
    ) -> tuple[_ExternalDependencyRequest, ...]:
        from grafix.core.geometry import Geometry

        if type(geometry) is not Geometry:
            raise TypeError("geometry は exact Geometry である必要があります")
        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの EvaluationResources は使用できません")
            cached = self._provider_lists.get(geometry.id)
            if cached is not None:
                self._provider_lists.move_to_end(geometry.id)
                return cached

        provider_refs = frozenset(
            ref
            for ref in geometry.operation_refs
            if context.catalog.resolve_ref(ref).declaration.external_dependency_hook
            is not None
        )
        if not provider_refs:
            providers: tuple[_ExternalDependencyRequest, ...] = ()
        else:
            requests: list[_ExternalDependencyRequest] = []
            visited: set[GeometryId] = set()
            stack = [geometry]
            while stack:
                node = stack.pop()
                if node.id in visited:
                    continue
                visited.add(node.id)
                operation = node.operation
                if operation is not None and operation in provider_refs:
                    requests.append(
                        _ExternalDependencyRequest(
                            operation=operation,
                            args=node.args,
                            geometry_id=node.id,
                        )
                    )
                stack.extend(node.inputs)
            providers = tuple(
                sorted(
                    requests,
                    key=lambda request: (
                        request.operation.kind,
                        request.operation.name,
                        request.operation.fingerprint.digest,
                        request.geometry_id,
                    ),
                )
            )

        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの EvaluationResources は使用できません")
            self._provider_lists[geometry.id] = providers
            self._provider_lists.move_to_end(geometry.id)
            while len(self._provider_lists) > _MAX_PROVIDER_LISTS:
                self._provider_lists.popitem(last=False)
        return providers

    def preflight_external_dependencies(
        self,
        geometry: Geometry,
        context: EvaluationContext,
    ) -> ExternalDependencySnapshot:
        """provider を一度ずつ lookup し、key と evaluator 用 lease を返す。"""

        providers = self._providers(geometry, context)
        if not providers:
            return ExternalDependencySnapshot(
                fingerprint=EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
                leases={},
            )

        parts: list[bytes] = []
        resources: dict[GeometryId, object] = {}
        for request in providers:
            entry = context.catalog.resolve_ref(request.operation)
            hook = entry.declaration.external_dependency_hook
            if hook is None:
                raise RuntimeError("external dependency provider が catalog と一致しません")
            lease = hook(
                args=request.args,
                context=context,
                resources=self,
            )
            if type(lease) is not ExternalDependencyLease:
                raise TypeError(
                    f"external dependency hook {request.operation.name!r} は "
                    "exact ExternalDependencyLease を返す必要があります"
                )
            resources[request.geometry_id] = lease.resource
            parts.append(
                _frame(
                    b"provider",
                    request.operation.kind.encode("ascii"),
                    request.operation.name.encode("utf-8"),
                    request.operation.fingerprint.digest.encode("ascii"),
                    request.geometry_id.encode("ascii"),
                    repr(lease.fingerprint_value).encode("utf-8"),
                )
            )
        return ExternalDependencySnapshot(
            fingerprint=ExternalDependenciesFingerprint(
                hashlib.sha256(
                    _frame(b"grafix.external-dependencies.v1", *parts)
                ).hexdigest()
            ),
            leases=resources,
        )

    @property
    def fonts(self) -> FontResources:
        """font slice が提供する bounded FontResources を遅延所有する。"""

        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの EvaluationResources は使用できません")
            fonts = self._fonts
            if fonts is None:
                from grafix.core.font_resources import FontResources

                fonts = FontResources()
                self._fonts = fonts
            return cast("FontResources", fonts)

    def clear(self) -> None:
        """provider list memo を破棄する。"""

        with self._lock:
            if self._closed:
                return
            self._provider_lists.clear()
            fonts = self._fonts
        if fonts is not None:
            fonts.clear()  # type: ignore[attr-defined]

    def close(self) -> None:
        """resource owner を一度だけ閉じる。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._provider_lists.clear()
            fonts = self._fonts
            self._fonts = None
        if fonts is not None:
            fonts.close()  # type: ignore[attr-defined]


__all__ = [
    "EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT",
    "ExternalDependencyLease",
    "ExternalDependencySnapshot",
    "EvaluationConfig",
    "EvaluationContext",
    "EvaluationFingerprint",
    "EvaluationResources",
    "ExternalDependenciesFingerprint",
    "bind_external_dependency",
    "current_external_dependency",
    "evaluation_fingerprint",
]
