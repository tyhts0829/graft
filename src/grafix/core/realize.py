"""immutable evaluation context による Geometry DAG 評価を提供する。"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import NoReturn, Protocol

from grafix.core.evaluation_config import (
    bind_evaluation_config,
    current_evaluation_config,
)
from grafix.core.evaluation_context import (
    EvaluationContext,
    EvaluationFingerprint,
    EvaluationResources,
    ExternalDependenciesFingerprint,
    bind_external_dependency,
)
from grafix.core.geometry import Geometry, GeometryId
from grafix.core.lifecycle import CleanupErrors
from grafix.core.operation_catalog import bind_operation_catalog, current_operation_catalog
from grafix.core.operation_diagnostics import emit_operation_diagnostic
from grafix.core.preview_quality import current_preview_quality, preview_quality_context
from grafix.core.realized_geometry import RealizedGeometry, concat_realized_geometries
from grafix.core.resource_budget import ensure_geometry_output, resource_budget_context
from grafix.core.runtime_limits import DEFAULT_FINAL_RUNTIME_LIMITS, RuntimeLimits
from grafix.core.runtime_config import without_runtime_config
from grafix.core.value_validation import exact_integer, exact_string


class PerformanceRecorder(Protocol):
    """RealizeSession が依存する最小 performance 記録契約。"""

    enabled: bool

    def record_operation(self, name: str, elapsed_ns: int) -> None: ...

    def record_layer(self, name: str, elapsed_ns: int) -> None: ...

    def record_cache(
        self,
        *,
        hits: int = 0,
        misses: int = 0,
        evictions: int = 0,
    ) -> None: ...


class RealizeError(RuntimeError):
    """Geometry の評価中に発生した通常の失敗を表す。"""


class CatalogMismatchError(RealizeError):
    """Geometry が固定した operation ref と session catalog の不一致。"""


@dataclass(frozen=True, slots=True)
class GeometryCacheKey:
    """CPU/inflight/RealizedLayer/GPU が共有する typed cache key。"""

    geometry_id: GeometryId
    evaluation: EvaluationFingerprint
    external_dependencies: ExternalDependenciesFingerprint
    uncached_generation: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "geometry_id",
            exact_string(self.geometry_id, name="geometry cache key id"),
        )
        if not self.geometry_id:
            raise ValueError("geometry cache key id は空にできません")
        if type(self.evaluation) is not EvaluationFingerprint:
            raise TypeError("evaluation は exact EvaluationFingerprint です")
        if type(self.external_dependencies) is not ExternalDependenciesFingerprint:
            raise TypeError(
                "external_dependencies は exact ExternalDependenciesFingerprint です"
            )
        if self.uncached_generation is not None:
            object.__setattr__(
                self,
                "uncached_generation",
                exact_integer(
                    self.uncached_generation,
                    name="uncached_generation",
                    minimum=1,
                ),
            )


@dataclass(frozen=True, slots=True)
class CacheStats:
    """RealizeCacheStore の統計スナップショット。"""

    hits: int
    misses: int
    evictions: int
    entries: int
    bytes: int


class RealizeCacheStore:
    """catalog generation の外側で親 runtime が所有する bounded LRU。"""

    __slots__ = (
        "_cache",
        "_cache_bytes",
        "_closed",
        "_evictions",
        "_hits",
        "_lock",
        "_max_bytes",
        "_max_entries",
        "_misses",
    )

    def __init__(self, *, max_bytes: int, max_entries: int) -> None:
        self._max_bytes = exact_integer(max_bytes, name="max_bytes", minimum=0)
        self._max_entries = exact_integer(max_entries, name="max_entries", minimum=0)
        self._lock = threading.Lock()
        self._cache: OrderedDict[GeometryCacheKey, RealizedGeometry] = OrderedDict()
        self._cache_bytes = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._closed = False

    @classmethod
    def from_runtime_limits(cls, limits: RuntimeLimits) -> RealizeCacheStore:
        """RuntimeLimits の cache 上限だけを parent store へ固定する。"""

        if type(limits) is not RuntimeLimits:
            raise TypeError("limits は exact RuntimeLimits です")
        return cls(
            max_bytes=limits.cpu_cache_bytes,
            max_entries=limits.cpu_cache_entries,
        )

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def get(self, key: GeometryCacheKey) -> RealizedGeometry | None:
        """key を lookup し、hit 時だけ LRU/stat を更新する。"""

        if type(key) is not GeometryCacheKey:
            raise TypeError("key は exact GeometryCacheKey です")
        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの RealizeCacheStore は使用できません")
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self._hits += 1
            return cached

    def record_staged_hit(self) -> None:
        """transaction-local entry の hit を store-wide stats へ加える。"""

        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの RealizeCacheStore は使用できません")
            self._hits += 1

    def record_miss(self) -> None:
        """実 evaluator を開始する miss を一度記録する。"""

        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの RealizeCacheStore は使用できません")
            self._misses += 1

    def put(self, key: GeometryCacheKey, result: RealizedGeometry) -> int:
        """result を bounded LRU へ格納し、eviction 数を返す。"""

        if type(key) is not GeometryCacheKey:
            raise TypeError("key は exact GeometryCacheKey です")
        if type(result) is not RealizedGeometry:
            raise TypeError("result は exact RealizedGeometry です")
        size = result.byte_size
        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの RealizeCacheStore は使用できません")
            if size > self._max_bytes:
                emit_operation_diagnostic(
                    op="runtime.cpu_cache",
                    original_value=size,
                    effective_value=self._max_bytes,
                    reason="result exceeded the CPU cache limit and was not cached",
                    severity="warning",
                )
                return 0
            if self._max_entries == 0:
                return 0

            previous = self._cache.pop(key, None)
            if previous is not None:
                self._cache_bytes -= previous.byte_size

            projected_bytes = self._cache_bytes + size
            byte_limit_reached = projected_bytes > self._max_bytes
            evicted_count = 0
            while self._cache and (
                len(self._cache) >= self._max_entries
                or self._cache_bytes + size > self._max_bytes
            ):
                _, evicted = self._cache.popitem(last=False)
                self._cache_bytes -= evicted.byte_size
                evicted_count += 1
            self._evictions += evicted_count

            if evicted_count and byte_limit_reached:
                emit_operation_diagnostic(
                    op="runtime.cpu_cache",
                    original_value=projected_bytes,
                    effective_value=self._max_bytes,
                    reason=f"CPU cache limit evicted {evicted_count} entrie(s)",
                    severity="info",
                )

            self._cache[key] = result
            self._cache_bytes += size
            return evicted_count

    def stats(self) -> CacheStats:
        """store-wide cache statistics を返す。"""

        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                entries=len(self._cache),
                bytes=self._cache_bytes,
            )

    def clear(self) -> None:
        """完了済み cache entry を破棄する。"""

        with self._lock:
            if self._closed:
                return
            self._cache.clear()
            self._cache_bytes = 0

    def close(self) -> None:
        """store を一度だけ閉じて全 entry を解放する。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cache.clear()
            self._cache_bytes = 0


@dataclass(slots=True)
class _InflightEntry:
    condition: threading.Condition
    done: bool = False
    result: RealizedGeometry | None = None
    error: BaseException | None = None


@dataclass(slots=True)
class _CacheTransaction:
    entries: OrderedDict[GeometryCacheKey, RealizedGeometry]
    commit_requested: bool = False

    def commit(self) -> None:
        self.commit_requested = True


@dataclass(slots=True)
class _EvaluationFrame:
    geometry: Geometry
    key: GeometryCacheKey
    cacheable: bool
    inflight: _InflightEntry | None
    inputs: tuple[Geometry, ...]
    next_input: int
    realized_inputs: list[RealizedGeometry]


def _close_owned_dependencies(
    *,
    resources: EvaluationResources | None,
    cache_store: RealizeCacheStore | None,
    initial_error: BaseException | None = None,
) -> None:
    """session-owned dependency を順に閉じ、最初の例外を保持する。"""

    errors = CleanupErrors(initial_error=initial_error)
    if resources is not None:
        errors.attempt(resources.close, "close owned evaluation resources")
    if cache_store is not None:
        errors.attempt(cache_store.close, "close owned realize cache store")
    errors.raise_if_any()


class RealizeSession:
    """明示 dependency を借用し、省略された resource/cache store だけを所有する。"""

    def __init__(
        self,
        *,
        context: EvaluationContext | None = None,
        resources: EvaluationResources | None = None,
        cache_store: RealizeCacheStore | None = None,
        runtime_limits: RuntimeLimits = DEFAULT_FINAL_RUNTIME_LIMITS,
        profiler: PerformanceRecorder | None = None,
    ) -> None:
        if type(runtime_limits) is not RuntimeLimits:
            raise TypeError("runtime_limits は exact RuntimeLimits です")
        owns_resources = resources is None
        owns_cache_store = cache_store is None
        selected_resources: EvaluationResources | None = None
        selected_store: RealizeCacheStore | None = None
        try:
            # application owner が明示注入した dependency は借用する。standalone
            # 利用のために省略された resource/store だけを session が所有する。
            selected_context = (
                EvaluationContext(
                    catalog=current_operation_catalog(),
                    quality=current_preview_quality(),
                    config=current_evaluation_config(),
                )
                if context is None
                else context
            )
            if type(selected_context) is not EvaluationContext:
                raise TypeError("context は exact EvaluationContext です")

            selected_resources = (
                EvaluationResources() if owns_resources else resources
            )
            if type(selected_resources) is not EvaluationResources:
                raise TypeError("resources は exact EvaluationResources です")
            if selected_resources.closed:
                raise RuntimeError("close 済み EvaluationResources は借用できません")

            selected_store = (
                RealizeCacheStore.from_runtime_limits(runtime_limits)
                if owns_cache_store
                else cache_store
            )
            if type(selected_store) is not RealizeCacheStore:
                raise TypeError("cache_store は exact RealizeCacheStore です")
            if selected_store.closed:
                raise RuntimeError("close 済み RealizeCacheStore は借用できません")

            self._context = selected_context
            self._resources = selected_resources
            self._cache_store = selected_store
            self._runtime_limits = runtime_limits
            self._profiler = profiler
            self._lock = threading.Lock()
            self._cache_transaction_local = threading.local()
            self._evaluation_local = threading.local()
            self._inflight: dict[GeometryCacheKey, _InflightEntry] = {}
            self._uncached_generation = 0
            self._active_realizations = 0
            self._owns_resources = owns_resources
            self._owns_cache_store = owns_cache_store
            self._closed = False
        except BaseException as error:
            _close_owned_dependencies(
                resources=selected_resources if owns_resources else None,
                cache_store=selected_store if owns_cache_store else None,
                initial_error=error,
            )
            raise

    def __enter__(self) -> RealizeSession:
        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの RealizeSession は再利用できません")
        return self

    def __exit__(
        self,
        _exc_type: object,
        exc: BaseException | None,
        _traceback: object,
    ) -> None:
        initial_error = exc
        errors = CleanupErrors(initial_error=initial_error)
        errors.attempt(self.close, "close realize session")
        errors.raise_if_any()

    @property
    def context(self) -> EvaluationContext:
        return self._context

    @property
    def resources(self) -> EvaluationResources:
        return self._resources

    @property
    def cache_store(self) -> RealizeCacheStore:
        return self._cache_store

    @property
    def runtime_limits(self) -> RuntimeLimits:
        return self._runtime_limits

    @contextlib.contextmanager
    def cache_transaction(self) -> Iterator[_CacheTransaction]:
        """scene aggregate 検査成功まで新規 store write を遅延する。"""

        if getattr(self._cache_transaction_local, "current", None) is not None:
            raise RuntimeError("cache transaction は入れ子にできません")
        transaction = _CacheTransaction(entries=OrderedDict())
        self._cache_transaction_local.current = transaction
        try:
            yield transaction
        finally:
            del self._cache_transaction_local.current
            if transaction.commit_requested:
                with self._lock:
                    should_commit = not self._closed
                if should_commit:
                    for key, result in transaction.entries.items():
                        evictions = self._cache_store.put(key, result)
                        if evictions:
                            self._record_cache(evictions=evictions)

    def stats(self) -> CacheStats:
        return self._cache_store.stats()

    @contextlib.contextmanager
    def profile_layer(self, name: str) -> Iterator[None]:
        profiler = self._profiler
        if profiler is None or not profiler.enabled:
            yield
            return
        started_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            profiler.record_layer(str(name), time.perf_counter_ns() - started_ns)

    def close(self) -> None:
        """新規評価を禁止し、session-owned dependency を一度だけ閉じる。

        実行中の評価がある場合は、その最後の caller が評価終了後に閉じる。
        明示注入された borrowed dependency は閉じない。
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            resources, cache_store = self._take_owned_dependencies_for_cleanup()
        _close_owned_dependencies(resources=resources, cache_store=cache_store)

    def _take_owned_dependencies_for_cleanup(
        self,
    ) -> tuple[EvaluationResources | None, RealizeCacheStore | None]:
        """lock 内で未使用の owned dependency を一度だけ引き渡す。"""

        if not self._closed or self._active_realizations != 0:
            return None, None
        resources = self._resources if self._owns_resources else None
        cache_store = self._cache_store if self._owns_cache_store else None
        self._owns_resources = False
        self._owns_cache_store = False
        return resources, cache_store

    def _finish_realization(self) -> None:
        """active call を終了し、deferred close があれば最後の caller が行う。"""

        with self._lock:
            if self._active_realizations <= 0:
                raise RuntimeError("active realization counter が不正です")
            self._active_realizations -= 1
            resources, cache_store = self._take_owned_dependencies_for_cleanup()
        _close_owned_dependencies(resources=resources, cache_store=cache_store)

    @staticmethod
    def _validate_geometry(geometry: Geometry) -> None:
        """catalog を走査せず、root geometry 自体の前提だけを検証する。

        operation ref の catalog 整合性は直後の external dependency preflight が
        全 ref を一度ずつ解決する際に同時に検証する。ここで同じ ref 列を先に
        resolve すると、external dependency を持たない通常 geometry でも毎 frame
        二重走査になるためである。
        """

        if type(geometry) is not Geometry:
            raise TypeError("geometry は exact Geometry です")
        if not geometry.fully_bound:
            raise CatalogMismatchError(
                f"Geometry に未解決 operation があります: id={geometry.id}, op={geometry.op!r}"
            )

    def realize(self, geometry: Geometry) -> RealizedGeometry:
        result, _ = self.realize_with_key(geometry)
        return result

    def realize_with_key(
        self,
        geometry: Geometry,
    ) -> tuple[RealizedGeometry, GeometryCacheKey]:
        with self._lock:
            if self._closed:
                raise RuntimeError("close 済みの RealizeSession は使用できません")
            self._active_realizations += 1
        try:
            with (
                without_runtime_config(),
                bind_evaluation_config(self._context.config),
                bind_operation_catalog(self._context.catalog),
                preview_quality_context(self._context.quality),
            ):
                result = self._realize_with_key_active(geometry)
        except BaseException as error:
            errors = CleanupErrors(initial_error=error)
            errors.attempt(self._finish_realization, "finish failed realization")
            errors.raise_if_any()
            raise
        self._finish_realization()
        return result

    def _realize_with_key_active(
        self,
        geometry: Geometry,
    ) -> tuple[RealizedGeometry, GeometryCacheKey]:
        self._validate_geometry(geometry)
        try:
            external_snapshot = self._resources.preflight_external_dependencies(
                geometry,
                self._context,
            )
        except (KeyError, LookupError) as error:
            raise CatalogMismatchError(
                "Geometry operation ref と evaluation catalog が一致しません: "
                f"id={geometry.id}"
            ) from error
        except BaseException as error:  # noqa: BLE001
            if not isinstance(error, Exception):
                raise
            raise RealizeError(
                "Geometry の external dependency 解決に失敗した: "
                f"id={geometry.id}"
            ) from error
        key = GeometryCacheKey(
            geometry_id=geometry.id,
            evaluation=self._context.fingerprint,
            external_dependencies=external_snapshot.fingerprint,
        )
        if geometry.cacheable:
            cached = self._get_cached(key)
            if cached is not None:
                return cached, key

        previous_snapshot = getattr(self._evaluation_local, "external_snapshot", None)
        if previous_snapshot is not None:
            raise RuntimeError("同じ RealizeSession で評価を入れ子にできません")
        self._evaluation_local.external_snapshot = external_snapshot
        try:
            result = self._realize(geometry, key)
        finally:
            del self._evaluation_local.external_snapshot
        if geometry.cacheable:
            return result, key
        with self._lock:
            self._uncached_generation += 1
            generation = self._uncached_generation
        return result, GeometryCacheKey(
            geometry_id=geometry.id,
            evaluation=self._context.fingerprint,
            external_dependencies=external_snapshot.fingerprint,
            uncached_generation=generation,
        )

    def _node_key(self, geometry: Geometry, root_key: GeometryCacheKey) -> GeometryCacheKey:
        return GeometryCacheKey(
            geometry_id=geometry.id,
            evaluation=root_key.evaluation,
            external_dependencies=root_key.external_dependencies,
        )

    def _realize(
        self,
        geometry: Geometry,
        root_key: GeometryCacheKey,
    ) -> RealizedGeometry:
        frames: list[_EvaluationFrame] = []
        current: Geometry | None = geometry
        pending: RealizedGeometry | None = None
        try:
            while True:
                if current is not None:
                    started = self._start_evaluation(
                        current,
                        self._node_key(current, root_key),
                        cacheable=current.cacheable,
                    )
                    if isinstance(started, RealizedGeometry):
                        pending = started
                        current = None
                    else:
                        frames.append(started)
                        if started.inputs:
                            current = started.inputs[0]
                            started.next_input = 1
                            continue
                        pending = self._finish_evaluation(started)
                        frames.pop()
                        current = None

                if pending is None:
                    raise RuntimeError("Geometry evaluator が結果を返しませんでした")
                if not frames:
                    return pending

                parent = frames[-1]
                parent.realized_inputs.append(pending)
                pending = None
                if parent.next_input < len(parent.inputs):
                    current = parent.inputs[parent.next_input]
                    parent.next_input += 1
                    continue
                pending = self._finish_evaluation(parent)
                frames.pop()
                current = None
        except BaseException as error:  # noqa: BLE001
            self._abort_evaluations(frames, error)

    def _start_evaluation(
        self,
        geometry: Geometry,
        key: GeometryCacheKey,
        *,
        cacheable: bool,
    ) -> RealizedGeometry | _EvaluationFrame:
        if not cacheable:
            self._cache_store.record_miss()
            self._record_cache(misses=1)
            try:
                return _EvaluationFrame(
                    geometry=geometry,
                    key=key,
                    cacheable=False,
                    inflight=None,
                    inputs=self._evaluation_inputs(geometry),
                    next_input=0,
                    realized_inputs=[],
                )
            except BaseException as error:  # noqa: BLE001
                if not isinstance(error, Exception):
                    raise
                raise RealizeError(f"Geometry の評価に失敗した: id={geometry.id}") from error

        with self._lock:
            cached = self._get_cached(key)
            if cached is not None:
                return cached
            entry = self._inflight.get(key)
            if entry is None:
                entry = _InflightEntry(condition=threading.Condition(self._lock))
                self._inflight[key] = entry
                self._cache_store.record_miss()
                self._record_cache(misses=1)
            else:
                while not entry.done:
                    entry.condition.wait()
                if entry.error is not None:
                    if not isinstance(entry.error, Exception):
                        raise entry.error
                    raise RealizeError(
                        f"Geometry の評価に失敗した: id={geometry.id}"
                    ) from entry.error
                if entry.result is None:
                    raise RuntimeError("inflight entry に評価結果がありません")
                return entry.result

        try:
            return _EvaluationFrame(
                geometry=geometry,
                key=key,
                cacheable=True,
                inflight=entry,
                inputs=self._evaluation_inputs(geometry),
                next_input=0,
                realized_inputs=[],
            )
        except BaseException as error:  # noqa: BLE001
            with self._lock:
                completed = self._inflight.pop(key, None)
                if completed is entry:
                    completed.error = error
                    completed.done = True
                    completed.condition.notify_all()
            if not isinstance(error, Exception):
                raise
            raise RealizeError(f"Geometry の評価に失敗した: id={geometry.id}") from error

    @staticmethod
    def _evaluation_inputs(geometry: Geometry) -> tuple[Geometry, ...]:
        if geometry.op != "concat":
            return geometry.inputs
        return Geometry._flatten_concat_inputs(geometry.inputs)

    def _finish_evaluation(self, frame: _EvaluationFrame) -> RealizedGeometry:
        result = self._evaluate_geometry_node(frame.geometry, frame.realized_inputs)
        if not frame.cacheable:
            return result
        entry = frame.inflight
        if entry is None:
            raise RuntimeError("cacheable evaluation に inflight entry がありません")
        with self._lock:
            if not self._closed:
                self._store(frame.key, result)
            completed = self._inflight.pop(frame.key)
            if completed is not entry:
                raise RuntimeError("inflight entry の所有者が一致しません")
            completed.result = result
            completed.done = True
            completed.condition.notify_all()
        return result

    def _abort_evaluations(
        self,
        frames: list[_EvaluationFrame],
        error: BaseException,
    ) -> NoReturn:
        current_error = error
        while frames:
            frame = frames.pop()
            entry = frame.inflight
            if entry is not None:
                with self._lock:
                    completed = self._inflight.pop(frame.key, None)
                    if completed is entry:
                        completed.error = current_error
                        completed.done = True
                        completed.condition.notify_all()
            if isinstance(current_error, Exception):
                wrapped = RealizeError(
                    f"Geometry の評価に失敗した: id={frame.geometry.id}"
                )
                wrapped.__cause__ = current_error
                current_error = wrapped
        raise current_error

    def _get_cached(self, key: GeometryCacheKey) -> RealizedGeometry | None:
        transaction = getattr(self._cache_transaction_local, "current", None)
        if transaction is not None:
            staged = transaction.entries.get(key)
            if staged is not None:
                transaction.entries.move_to_end(key)
                self._cache_store.record_staged_hit()
                self._record_cache(hits=1)
                return staged
        cached = self._cache_store.get(key)
        if cached is not None:
            self._record_cache(hits=1)
        return cached

    def _record_cache(
        self,
        *,
        hits: int = 0,
        misses: int = 0,
        evictions: int = 0,
    ) -> None:
        profiler = self._profiler
        if profiler is not None and profiler.enabled:
            profiler.record_cache(hits=hits, misses=misses, evictions=evictions)

    @contextlib.contextmanager
    def _profile_operation(self, op: str) -> Iterator[None]:
        profiler = self._profiler
        if profiler is None or not profiler.enabled:
            yield
            return
        started_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            profiler.record_operation(op, time.perf_counter_ns() - started_ns)

    def _evaluate_geometry_node(
        self,
        geometry: Geometry,
        realized_inputs: Sequence[RealizedGeometry],
    ) -> RealizedGeometry:
        op = geometry.op
        with resource_budget_context(self._runtime_limits.per_operation):
            if op == "concat":

                def evaluate() -> RealizedGeometry:
                    ensure_geometry_output(
                        "concat",
                        vertices=sum(int(item.coords.shape[0]) for item in realized_inputs),
                        lines=sum(
                            max(0, int(item.offsets.size) - 1) for item in realized_inputs
                        ),
                        hint="入力 geometry または concat 対象数を減らしてください",
                    )
                    return concat_realized_geometries(*realized_inputs)

            else:
                operation = geometry.operation
                if operation is None:
                    raise CatalogMismatchError(f"未解決 operation です: {op!r}")
                try:
                    spec = self._context.catalog.resolve_ref(operation).evaluation
                except (KeyError, LookupError) as exc:
                    raise CatalogMismatchError(
                        f"operation ref が catalog と一致しません: {op!r}"
                    ) from exc
                if spec.cache_policy != geometry.cache_policy:
                    raise CatalogMismatchError(
                        f"operation cache policy が Geometry と一致しません: {op!r}"
                    )
                if operation.kind == "primitive":
                    if geometry.inputs or realized_inputs or spec.n_inputs != 0:
                        raise CatalogMismatchError(f"primitive arity が不正です: {op!r}")

                    def evaluate() -> RealizedGeometry:
                        return spec.evaluator(geometry.args)  # type: ignore[call-arg, no-any-return]

                else:
                    if len(geometry.inputs) != spec.n_inputs:
                        raise CatalogMismatchError(
                            f"effect {op!r} は入力 Geometry を {spec.n_inputs} 個必要とします"
                        )

                    def evaluate() -> RealizedGeometry:
                        return spec.evaluator(  # type: ignore[call-arg, no-any-return]
                            realized_inputs,
                            geometry.args,
                        )

            external_snapshot = getattr(self._evaluation_local, "external_snapshot", None)
            if external_snapshot is None:
                raise RuntimeError("external dependency snapshot がありません")
            with (
                self._profile_operation(op),
                bind_operation_catalog(self._context.catalog),
                bind_evaluation_config(self._context.config),
                preview_quality_context(self._context.quality),
                bind_external_dependency(external_snapshot, geometry.id),
            ):
                result = evaluate()
                ensure_geometry_output(
                    op,
                    vertices=int(result.coords.shape[0]),
                    lines=max(0, int(result.offsets.size) - 1),
                    hint="operation の入力または出力パラメータを減らしてください",
                )
                return result

    def _store(self, key: GeometryCacheKey, result: RealizedGeometry) -> None:
        transaction = getattr(self._cache_transaction_local, "current", None)
        if transaction is not None:
            transaction.entries.pop(key, None)
            transaction.entries[key] = result
            return
        evictions = self._cache_store.put(key, result)
        if evictions:
            self._record_cache(evictions=evictions)


def realize(
    geometry: Geometry,
    *,
    session: RealizeSession | None = None,
    context: EvaluationContext | None = None,
) -> RealizedGeometry:
    """Geometry を評価する。session 省略時は一時 parent owner を構成する。"""

    if session is not None:
        if context is not None:
            raise ValueError("session と context は同時に指定できません")
        return session.realize(geometry)
    selected_context = (
        EvaluationContext(
            catalog=current_operation_catalog(),
            quality=current_preview_quality(),
            config=current_evaluation_config(),
        )
        if context is None
        else context
    )
    # 省略 dependency の owner は RealizeSession に一意化する。helper 側で
    # resource/store を別途生成すると、body error と複数 close error の集約が
    # session の lifecycle contract から外れてしまうためである。
    with RealizeSession(context=selected_context) as owned_session:
        return owned_session.realize(geometry)


__all__ = [
    "CacheStats",
    "CatalogMismatchError",
    "GeometryCacheKey",
    "PerformanceRecorder",
    "RealizeCacheStore",
    "RealizeError",
    "RealizeSession",
    "realize",
]
