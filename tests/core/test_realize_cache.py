"""RealizeSession の bounded LRU と inflight coordination をテストする。"""

from __future__ import annotations

import importlib
import hashlib
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
import pytest

from grafix import E, G
from grafix.core.authoring_definitions import RegistrationTarget, registration_scope
from grafix.core.definition_fingerprint import (
    EvaluationSpecFingerprint,
    ParameterSchemaFingerprint,
)
from grafix.core.evaluation_config import EvaluationConfig, current_evaluation_config
from grafix.core.evaluation_context import EvaluationContext, EvaluationResources
from grafix.core.geometry import Geometry
from grafix.core.operation_declaration import (
    CachePolicy,
    EvaluationOpRef,
    OpDeclaration,
    OpKind,
)
from grafix.core.operation_catalog import (
    OperationCatalog,
    OperationCatalogEntry,
    bind_operation_catalog,
)
from grafix.core.operation_schema import ParameterOpSchema
from grafix.core.preview_quality import PreviewQuality, current_preview_quality
from grafix.core.realize import (
    CatalogMismatchError,
    GeometryCacheKey,
    RealizeCacheStore,
    RealizeError,
    RealizeSession,
    realize,
)
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.resource_budget import ResourceBudget
from grafix.core.runtime_limits import RuntimeLimits
from grafix.core.runtime_config import (
    bind_runtime_config,
    current_runtime_config,
)
from grafix.runtime_config_loader import runtime_config

realize_module = importlib.import_module("grafix.core.realize")


_EMPTY_SCHEMA = ParameterOpSchema(
    meta={},
    defaults={},
    param_order=(),
    ui_visible={},
)


@dataclass(frozen=True, slots=True)
class _TestSpec:
    evaluator: Callable[..., RealizedGeometry]
    n_inputs: int
    cache_policy: CachePolicy = "content"


def _primitive_spec(
    evaluator: Callable[[tuple[tuple[str, object], ...]], RealizedGeometry],
) -> _TestSpec:
    return _TestSpec(evaluator=evaluator, n_inputs=0)


def _uncached_primitive_spec(
    evaluator: Callable[[tuple[tuple[str, object], ...]], RealizedGeometry],
) -> _TestSpec:
    return _TestSpec(
        evaluator=evaluator,
        n_inputs=0,
        cache_policy="none",
    )


def _effect_spec(
    evaluator: Callable[
        [Sequence[RealizedGeometry], tuple[tuple[str, object], ...]],
        RealizedGeometry,
    ],
) -> _TestSpec:
    return _TestSpec(evaluator=evaluator, n_inputs=1)


def _realized(n_vertices: int, *, value: float = 0.0) -> RealizedGeometry:
    coords = np.full((n_vertices, 3), value, dtype=np.float32)
    offsets = np.asarray([0, n_vertices], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


class _TestDeclarations:
    """test evaluator を immutable declaration として candidate へ登録する。"""

    def __init__(self, target: RegistrationTarget, *, kind: OpKind) -> None:
        self._target = target
        self._kind = kind
        self._versions: dict[str, int] = {}

    def register(
        self,
        name: str,
        spec: _TestSpec,
        *,
        replace: bool = False,
    ) -> None:
        version = self._versions.get(name, 0) + 1
        self._versions[name] = version
        digest = hashlib.sha256(
            f"grafix-test:{self._kind}:{name}:{version}:{spec.cache_policy}".encode()
        ).hexdigest()
        declaration = OpDeclaration(
            name=name,
            kind=self._kind,
            evaluator=spec.evaluator,
            schema=_EMPTY_SCHEMA,
            n_inputs=spec.n_inputs,
            cache_policy=spec.cache_policy,
            evaluator_abi="test-v1",
            version=("test-v1" if spec.cache_policy == "none" else None),
            external_dependency_hook=None,
            evaluation_fingerprint=EvaluationSpecFingerprint(digest),
            schema_fingerprint=ParameterSchemaFingerprint(
                hashlib.sha256(b"grafix-test-empty-schema").hexdigest()
            ),
            description="",
            doc="",
            source=None,
            source_owner="grafix.tests.realize",
            provenance=f"grafix.tests.realize:{self._kind}:{name}",
            accepted_args=(),
            required_args=(),
            accepts_var_kwargs=True,
        )
        self._target.register(declaration, overwrite=replace)

    @property
    def catalog(self) -> OperationCatalog:
        return self._target.snapshot().operations


_CatalogPair = tuple[_TestDeclarations, _TestDeclarations]


@pytest.fixture
def isolated_catalog() -> Iterator[_CatalogPair]:
    """各 test に空の scoped immutable-catalog builder を用意する。"""

    target = RegistrationTarget()
    declarations = (
        _TestDeclarations(target, kind="primitive"),
        _TestDeclarations(target, kind="effect"),
    )
    with registration_scope(target):
        yield declarations


def _evaluation_context(
    catalog: OperationCatalog,
    *,
    quality: PreviewQuality = "final",
) -> EvaluationContext:
    return EvaluationContext(
        catalog=catalog,
        quality=quality,
        config=EvaluationConfig(font_dirs=runtime_config().font_dirs),
    )


def test_session_reuses_same_content_across_geometry_instances(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(4)))
    first_geometry = Geometry.create("shape", params={"variant": 1})
    same_geometry = Geometry.create("shape", params={"variant": 1})

    with RealizeSession() as session:
        first = session.realize(first_geometry)
        second = session.realize(same_geometry)
        stats = session.stats()

    assert first_geometry.id == same_geometry.id
    assert second is first
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.entries == 1
    assert stats.bytes == first.byte_size


def test_dag_evaluator_observes_only_evaluation_config(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    runtime = runtime_config()
    expected = EvaluationConfig(font_dirs=runtime.font_dirs)
    observed: list[EvaluationConfig] = []

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        observed.append(current_evaluation_config())
        with pytest.raises(RuntimeError, match="束縛されていません"):
            current_runtime_config()
        return _realized(1)

    primitives.register("evaluation_config_scope_probe", _primitive_spec(evaluate))
    catalog = primitives.catalog
    with bind_operation_catalog(catalog):
        geometry = G.evaluation_config_scope_probe()
    context = EvaluationContext(
        catalog=catalog,
        quality="final",
        config=expected,
    )

    with bind_runtime_config(runtime), RealizeSession(context=context) as session:
        session.realize(geometry)

    assert observed == [expected]


def test_module_convenience_call_does_not_share_global_cache(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    calls = 0

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        calls += 1
        return _realized(2)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")

    first = realize(geometry)
    second = realize(geometry)

    assert first is not second
    assert calls == 2
    assert not hasattr(realize_module, "realize_cache")


def test_evaluator_nested_operation_uses_the_session_catalog() -> None:
    target = RegistrationTarget()
    primitives = _TestDeclarations(target, kind="primitive")
    effects = _TestDeclarations(target, kind="effect")
    primitives.register(
        "realize_nested_inner_probe",
        _primitive_spec(lambda _args: _realized(1, value=3.0)),
    )
    inner_ref = primitives.catalog.resolve(
        "primitive",
        "realize_nested_inner_probe",
    ).ref
    effects.register(
        "realize_nested_effect_probe",
        _effect_spec(lambda inputs, _args: inputs[0]),
    )
    effect_ref = effects.catalog.resolve(
        "effect",
        "realize_nested_effect_probe",
    ).ref

    def evaluate_outer(
        _args: tuple[tuple[str, object], ...],
    ) -> RealizedGeometry:
        nested = G.realize_nested_inner_probe()
        assert nested.operation == inner_ref
        effected = E.realize_nested_effect_probe()(nested)
        assert effected.operation == effect_ref
        return _realized(2, value=7.0)

    primitives.register(
        "realize_nested_outer_probe",
        _primitive_spec(evaluate_outer),
    )
    catalog = primitives.catalog
    with bind_operation_catalog(catalog):
        geometry = G.realize_nested_outer_probe()

    with RealizeSession(context=_evaluation_context(catalog)) as session:
        result = session.realize(geometry)

    np.testing.assert_array_equal(result.coords, np.full((2, 3), 7.0))


@pytest.mark.parametrize(
    "order",
    [("draft", "final"), ("final", "draft")],
)
def test_draft_and_final_use_distinct_typed_keys_in_both_orders(
    isolated_catalog: _CatalogPair,
    order: tuple[PreviewQuality, PreviewQuality],
) -> None:
    primitives, _ = isolated_catalog

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        value = 1.0 if current_preview_quality() == "draft" else 2.0
        return _realized(2, value=value)

    primitives.register("quality_shape", _primitive_spec(evaluate))
    geometry = Geometry.create("quality_shape")
    limits = RuntimeLimits()
    store = RealizeCacheStore.from_runtime_limits(limits)
    resources = EvaluationResources()
    qualities: tuple[PreviewQuality, PreviewQuality] = ("draft", "final")
    sessions: dict[PreviewQuality, RealizeSession] = {
        quality: RealizeSession(
            context=_evaluation_context(primitives.catalog, quality=quality),
            resources=resources,
            cache_store=store,
            runtime_limits=limits,
        )
        for quality in qualities
    }
    results: dict[PreviewQuality, RealizedGeometry] = {}
    keys: dict[PreviewQuality, GeometryCacheKey] = {}
    try:
        for quality in order:
            result, key = sessions[quality].realize_with_key(geometry)
            results[quality] = result
            keys[quality] = key
        for quality in reversed(order):
            assert sessions[quality].realize(geometry) is results[quality]
    finally:
        for session in sessions.values():
            session.close()
        resources.close()
        store.close()

    assert keys["draft"] != keys["final"]
    assert keys["draft"].geometry_id == keys["final"].geometry_id == geometry.id
    np.testing.assert_array_equal(results["draft"].coords, np.full((2, 3), 1.0))
    np.testing.assert_array_equal(results["final"].coords, np.full((2, 3), 2.0))


def test_cache_policy_none_bypasses_cpu_cache_and_changes_gpu_key(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    calls = 0

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        calls += 1
        return _realized(2, value=float(calls))

    primitives.register("live", _uncached_primitive_spec(evaluate))
    geometry = Geometry.create("live")

    with RealizeSession() as session:
        first, first_key = session.realize_with_key(geometry)
        second, second_key = session.realize_with_key(geometry)
        stats = session.stats()

    assert calls == 2
    assert first is not second
    assert first_key != second_key
    assert stats.hits == 0
    assert stats.misses == 2
    assert stats.entries == 0


def test_uncached_input_makes_content_parent_uncached(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, effects = isolated_catalog
    primitive_calls = 0
    effect_calls = 0

    def evaluate_primitive(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal primitive_calls
        primitive_calls += 1
        return _realized(2, value=float(primitive_calls))

    def evaluate_effect(
        inputs: Sequence[RealizedGeometry],
        _args: tuple[tuple[str, object], ...],
    ) -> RealizedGeometry:
        nonlocal effect_calls
        effect_calls += 1
        return inputs[0]

    primitives.register("live", _uncached_primitive_spec(evaluate_primitive))
    effects.register("pass_through", _effect_spec(evaluate_effect))
    geometry = Geometry.create("pass_through", inputs=(Geometry.create("live"),))

    with RealizeSession() as session:
        session.realize(geometry)
        session.realize(geometry)
        stats = session.stats()

    assert primitive_calls == 2
    assert effect_calls == 2
    assert stats.entries == 0


def test_uncached_shared_input_is_evaluated_for_each_occurrence(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    calls = 0

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        calls += 1
        return _realized(2, value=float(calls))

    primitives.register("live", _uncached_primitive_spec(evaluate))
    live = Geometry.create("live")
    geometry = Geometry.concat([live, live])

    with RealizeSession() as session:
        result = session.realize(geometry)

    assert calls == 2
    np.testing.assert_array_equal(result.coords[:2], np.full((2, 3), 1.0))
    np.testing.assert_array_equal(result.coords[2:], np.full((2, 3), 2.0))


def test_deep_unary_dag_is_realized_without_python_recursion(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, effects = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(2)))
    effects.register(
        "pass_through",
        _effect_spec(lambda inputs, _args: inputs[0]),
    )
    geometry = Geometry.create("shape")
    for index in range(10_000):
        geometry = Geometry.create(
            "pass_through",
            inputs=(geometry,),
            params={"index": index},
        )

    with RealizeSession() as session:
        result = session.realize(geometry)

    assert result.coords.shape == (2, 3)


def test_deep_binary_concat_is_flattened_once_without_python_recursion(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    primitive_calls = 0

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal primitive_calls
        primitive_calls += 1
        return _realized(2)

    primitives.register("shape", _primitive_spec(evaluate))
    leaf = Geometry.create("shape")
    geometry = leaf
    for _ in range(9_999):
        geometry = geometry + leaf

    with RealizeSession() as session:
        result = session.realize(geometry)

    assert primitive_calls == 1
    assert result.coords.shape == (20_000, 3)
    assert result.offsets.size == 10_001


def test_shared_concat_dag_hits_resource_limit_without_exponential_expansion(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    primitive_calls = 0

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal primitive_calls
        primitive_calls += 1
        return _realized(1)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")
    depth = 5_000
    for _ in range(depth):
        geometry = geometry + geometry

    budget = ResourceBudget(
        max_output_vertices=100,
        max_output_lines=10,
        max_output_bytes=10_000,
    )
    with RealizeSession(
        runtime_limits=RuntimeLimits(per_operation=budget, scene=budget)
    ) as session:
        with pytest.raises(RealizeError, match="Geometry の評価に失敗"):
            session.realize(geometry)
        stats = session.stats()
        assert session._inflight == {}

    assert primitive_calls == 1
    assert stats.hits < 20
    assert stats.misses == depth + 1


def test_binary_and_bulk_concat_realize_in_the_same_leaf_order(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog

    def evaluate(args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        return _realized(2, value=float(cast(int, dict(args)["value"])))

    primitives.register("shape", _primitive_spec(evaluate))
    leaves = tuple(
        Geometry.create("shape", params={"value": value}) for value in (1, 2, 3)
    )
    binary = (leaves[0] + leaves[1]) + leaves[2]
    bulk = Geometry.concat(leaves)

    with RealizeSession() as session:
        binary_result = session.realize(binary)
        bulk_result = session.realize(bulk)

    assert binary.id != bulk.id
    np.testing.assert_array_equal(binary_result.coords, bulk_result.coords)
    np.testing.assert_array_equal(binary_result.offsets, bulk_result.offsets)


def test_warm_root_hit_skips_deep_cacheability_walk(
    isolated_catalog: _CatalogPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primitives, effects = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(2)))
    effects.register("pass_through", _effect_spec(lambda inputs, _args: inputs[0]))
    geometry = Geometry.create("shape")
    for index in range(5_000):
        geometry = Geometry.create(
            "pass_through",
            inputs=(geometry,),
            params={"index": index},
        )

    with RealizeSession() as session:
        first = session.realize(geometry)
        monkeypatch.setattr(
            session,
            "_realize",
            lambda *_args, **_kwargs: pytest.fail(
                "warm root hitでDAG evaluationを開始した"
            ),
        )
        second = session.realize(geometry)

    assert second is first


def test_preflight_is_the_single_catalog_validation_walk(
    isolated_catalog: _CatalogPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primitives, _ = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(2)))
    catalog = primitives.catalog
    geometry = Geometry.create("shape")
    calls: list[EvaluationOpRef] = []
    original_resolve_ref = OperationCatalog.resolve_ref

    def counting_resolve_ref(
        selected_catalog: OperationCatalog,
        ref: EvaluationOpRef,
    ) -> OperationCatalogEntry:
        if selected_catalog is catalog:
            calls.append(ref)
        return original_resolve_ref(selected_catalog, ref)

    monkeypatch.setattr(OperationCatalog, "resolve_ref", counting_resolve_ref)
    with RealizeSession(context=_evaluation_context(catalog)) as session:
        # evaluator dispatch 自体の resolve を除き、realize_with_key の preflight
        # 境界だけを観測する。
        monkeypatch.setattr(
            session,
            "_realize",
            lambda _geometry, _key: _realized(2),
        )
        session.realize(geometry)

    assert calls == list(geometry.operation_refs)


def test_input_preparation_failure_releases_inflight_leader(
    isolated_catalog: _CatalogPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primitives, _ = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(2)))
    geometry = Geometry.create("shape")
    session = RealizeSession()
    original = session._evaluation_inputs

    monkeypatch.setattr(
        session,
        "_evaluation_inputs",
        lambda _geometry: (_ for _ in ()).throw(ValueError("prepare failed")),
    )
    with pytest.raises(RealizeError, match=geometry.id):
        session.realize(geometry)
    assert session._inflight == {}

    monkeypatch.setattr(session, "_evaluation_inputs", original)
    assert session.realize(geometry).coords.shape == (2, 3)
    session.close()


def test_lru_evicts_least_recently_used_entry_by_byte_budget(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog

    def evaluate(args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        return _realized(cast(int, dict(args)["n"]))

    primitives.register("shape", _primitive_spec(evaluate))
    geometries = [Geometry.create("shape", params={"n": n}) for n in (3, 4, 5)]
    entry_size = _realized(5).byte_size

    with RealizeSession(
        runtime_limits=RuntimeLimits(cpu_cache_bytes=entry_size * 2)
    ) as session:
        first = session.realize(geometries[0])
        second = session.realize(geometries[1])
        assert session.realize(geometries[0]) is first

        session.realize(geometries[2])
        after_eviction = session.stats()
        assert session.realize(geometries[0]) is first
        assert session.realize(geometries[1]) is not second
        final_stats = session.stats()

    assert after_eviction.evictions == 1
    assert after_eviction.entries == 2
    assert after_eviction.bytes <= entry_size * 2
    assert final_stats.bytes <= entry_size * 2


def test_lru_evicts_least_recently_used_entry_by_entry_budget(
    isolated_catalog: _CatalogPair,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primitives, _ = isolated_catalog
    diagnostics: list[dict[str, object]] = []

    def evaluate(args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        return _realized(3, value=float(cast(int, dict(args)["value"])))

    primitives.register("shape", _primitive_spec(evaluate))
    geometries = [
        Geometry.create("shape", params={"value": value}) for value in range(3)
    ]
    monkeypatch.setattr(
        realize_module,
        "emit_operation_diagnostic",
        lambda **payload: diagnostics.append(payload),
    )

    with RealizeSession(
        runtime_limits=RuntimeLimits(
            cpu_cache_bytes=1_000_000,
            cpu_cache_entries=2,
        ),
    ) as session:
        first = session.realize(geometries[0])
        second = session.realize(geometries[1])
        assert session.realize(geometries[0]) is first

        session.realize(geometries[2])
        after_eviction = session.stats()
        assert session.realize(geometries[0]) is first
        assert session.realize(geometries[1]) is not second

    assert after_eviction.evictions == 1
    assert after_eviction.entries == 2
    assert diagnostics == []


def test_cache_transaction_applies_entry_budget_only_after_commit(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(3)))
    geometries = [
        Geometry.create("shape", params={"value": value}) for value in range(3)
    ]

    with RealizeSession(
        runtime_limits=RuntimeLimits(
            cpu_cache_bytes=1_000_000,
            cpu_cache_entries=2,
        ),
    ) as session:
        with session.cache_transaction() as transaction:
            results = [session.realize(geometry) for geometry in geometries]
            assert session.stats().entries == 0
            transaction.commit()

        committed = session.stats()
        assert session.realize(geometries[2]) is results[2]
        assert session.realize(geometries[0]) is not results[0]

    assert committed.entries == 2
    assert committed.evictions == 1


def test_result_larger_than_budget_is_delivered_but_not_cached(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    calls = 0

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        calls += 1
        return _realized(8)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")
    result_size = _realized(8).byte_size

    with RealizeSession(
        runtime_limits=RuntimeLimits(cpu_cache_bytes=result_size - 1)
    ) as session:
        first = session.realize(geometry)
        second = session.realize(geometry)
        stats = session.stats()

    assert first is not second
    assert calls == 2
    assert stats.misses == 2
    assert stats.entries == 0
    assert stats.bytes == 0


def test_inflight_avoids_duplicate_computation_under_concurrency(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=2.0)
        return _realized(4)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")
    session = RealizeSession()
    results: list[RealizedGeometry] = []
    errors: list[Exception] = []
    result_lock = threading.Lock()
    start = threading.Barrier(9)

    def worker() -> None:
        start.wait()
        try:
            result = session.realize(geometry)
        except Exception as exc:  # noqa: BLE001
            with result_lock:
                errors.append(exc)
        else:
            with result_lock:
                results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    start.wait()
    assert entered.wait(timeout=2.0)
    time.sleep(0.02)
    release.set()
    for thread in threads:
        thread.join(timeout=2.0)

    session.close()
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == len(threads)
    assert len({id(result) for result in results}) == 1
    assert calls == 1


def test_failed_inflight_notifies_waiters_and_allows_retry(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=2.0)
            raise ValueError("expected failure")
        return _realized(3)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")
    session = RealizeSession()
    errors: list[RealizeError] = []
    error_lock = threading.Lock()
    start = threading.Barrier(7)

    def worker() -> None:
        start.wait()
        try:
            session.realize(geometry)
        except RealizeError as exc:
            with error_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    start.wait()
    assert entered.wait(timeout=2.0)
    time.sleep(0.02)
    release.set()
    for thread in threads:
        thread.join(timeout=2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == len(threads)
    assert calls == 1
    assert session.realize(geometry).coords.shape == (3, 3)
    assert calls == 2
    session.close()


@pytest.mark.parametrize(
    "error_factory",
    [
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
        pytest.param(SystemExit, id="system-exit"),
    ],
)
def test_leader_preserves_process_control_exception_and_cleans_inflight(
    isolated_catalog: _CatalogPair,
    error_factory: Callable[[], BaseException],
) -> None:
    primitives, _ = isolated_catalog
    should_fail = True

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        if should_fail:
            raise error_factory()
        return _realized(2)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")

    with RealizeSession() as session:
        with pytest.raises((KeyboardInterrupt, SystemExit)) as exc_info:
            session.realize(geometry)
        should_fail = False
        result = session.realize(geometry)

    assert type(exc_info.value) is type(error_factory())
    assert result.coords.shape == (2, 3)


@pytest.mark.parametrize(
    "error_factory",
    [
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
        pytest.param(SystemExit, id="system-exit"),
    ],
)
def test_process_control_exception_keeps_its_type_for_leader_and_waiters(
    isolated_catalog: _CatalogPair,
    error_factory: Callable[[], BaseException],
) -> None:
    primitives, _ = isolated_catalog
    entered = threading.Event()
    release = threading.Event()
    start = threading.Barrier(7)
    should_fail = True
    calls = 0
    calls_lock = threading.Lock()

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        with calls_lock:
            calls += 1
        if should_fail:
            entered.set()
            assert release.wait(timeout=2.0)
            raise error_factory()
        return _realized(2)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")
    session = RealizeSession()
    errors: list[BaseException] = []
    error_lock = threading.Lock()

    def worker() -> None:
        start.wait()
        try:
            session.realize(geometry)
        except BaseException as exc:  # noqa: BLE001
            with error_lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    start.wait()
    assert entered.wait(timeout=2.0)
    time.sleep(0.02)
    release.set()
    for thread in threads:
        thread.join(timeout=2.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(errors) == len(threads)
    assert {type(error) for error in errors} == {type(error_factory())}
    assert calls == 1

    should_fail = False
    assert session.realize(geometry).coords.shape == (2, 3)
    assert calls == 2
    session.close()


def test_catalog_generations_share_only_compatible_per_operation_cache_entries(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(2, value=1.0)))
    primitives.register("unused", _primitive_spec(lambda _args: _realized(1)))
    geometry_a = Geometry.create("shape")
    catalog_a = primitives.catalog
    limits = RuntimeLimits()
    cache_store = RealizeCacheStore.from_runtime_limits(limits)
    resources_a = EvaluationResources()
    session_a = RealizeSession(
        context=_evaluation_context(catalog_a),
        resources=resources_a,
        cache_store=cache_store,
        runtime_limits=limits,
    )
    resources_unrelated = EvaluationResources()
    resources_b = EvaluationResources()
    session_unrelated: RealizeSession | None = None
    session_b: RealizeSession | None = None
    try:
        first, first_key = session_a.realize_with_key(geometry_a)
        primitives.register(
            "unused",
            _primitive_spec(lambda _args: _realized(1, value=9.0)),
            replace=True,
        )
        catalog_unrelated = primitives.catalog
        geometry_unrelated = Geometry.create("shape")
        session_unrelated = RealizeSession(
            context=_evaluation_context(catalog_unrelated),
            resources=resources_unrelated,
            cache_store=cache_store,
            runtime_limits=limits,
        )
        unrelated, unrelated_key = session_unrelated.realize_with_key(
            geometry_unrelated
        )

        primitives.register(
            "shape",
            _primitive_spec(lambda _args: _realized(2, value=2.0)),
            replace=True,
        )
        catalog_b = primitives.catalog
        geometry_b = Geometry.create("shape")
        session_b = RealizeSession(
            context=_evaluation_context(catalog_b),
            resources=resources_b,
            cache_store=cache_store,
            runtime_limits=limits,
        )
        second, second_key = session_b.realize_with_key(geometry_b)

        assert geometry_unrelated.id == geometry_a.id
        assert unrelated_key == first_key
        assert unrelated is first
        assert geometry_b.id != geometry_a.id
        assert second_key.geometry_id == geometry_b.id
        assert first_key.evaluation == second_key.evaluation
        assert session_a.realize(geometry_a) is first
        with pytest.raises(CatalogMismatchError):
            session_b.realize(geometry_a)
        assert cache_store.stats().misses == 2
    finally:
        session_a.close()
        if session_unrelated is not None:
            session_unrelated.close()
        if session_b is not None:
            session_b.close()
        resources_a.close()
        resources_unrelated.close()
        resources_b.close()
        cache_store.close()

    assert first is not second
    np.testing.assert_array_equal(first.coords, np.full((2, 3), 1.0, dtype=np.float32))
    np.testing.assert_array_equal(second.coords, np.full((2, 3), 2.0, dtype=np.float32))


def test_animation_soak_stays_bounded_and_keeps_static_upstream_hot(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, effects = isolated_catalog
    primitive_calls = 0
    effect_calls = 0

    def make_base(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal primitive_calls
        primitive_calls += 1
        return _realized(16)

    def animate(
        inputs: Sequence[RealizedGeometry],
        args: tuple[tuple[str, object], ...],
    ) -> RealizedGeometry:
        nonlocal effect_calls
        effect_calls += 1
        frame = float(cast(int, dict(args)["frame"]))
        return RealizedGeometry(
            coords=inputs[0].coords + np.float32(frame),
            offsets=inputs[0].offsets,
        )

    primitives.register("base", _primitive_spec(make_base))
    effects.register("animate", _effect_spec(animate))
    base = Geometry.create("base")
    entry_size = _realized(16).byte_size

    with RealizeSession(
        runtime_limits=RuntimeLimits(cpu_cache_bytes=entry_size * 4)
    ) as session:
        base_result = session.realize(base)
        for frame in range(5000):
            animated = Geometry.create("animate", inputs=(base,), params={"frame": frame})
            session.realize(animated)

        stats = session.stats()
        assert session.realize(base) is base_result

    assert primitive_calls == 1
    assert effect_calls == 5000
    assert stats.entries <= 4
    assert stats.bytes <= entry_size * 4
    assert stats.evictions > 0


def test_entry_bounded_animation_keeps_frequently_used_base_hot(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, effects = isolated_catalog
    primitive_calls = 0

    def make_base(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal primitive_calls
        primitive_calls += 1
        return _realized(2)

    primitives.register("base", _primitive_spec(make_base))
    effects.register(
        "animate",
        _effect_spec(lambda inputs, _args: inputs[0]),
    )
    base = Geometry.create("base")

    with RealizeSession(
        runtime_limits=RuntimeLimits(
            cpu_cache_bytes=1_000_000,
            cpu_cache_entries=4,
        ),
    ) as session:
        base_result = session.realize(base)
        for frame in range(100):
            session.realize(
                Geometry.create(
                    "animate",
                    inputs=(base,),
                    params={"frame": frame},
                )
            )
            assert session.stats().entries <= 4
        stats = session.stats()
        cached_base = session.realize(base)

    assert primitive_calls == 1
    assert cached_base is base_result
    assert stats.entries == 4
    assert stats.evictions > 0


def test_zero_cache_entry_budget_disables_cache(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    calls = 0

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        nonlocal calls
        calls += 1
        return _realized(2)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")

    with RealizeSession(
        runtime_limits=RuntimeLimits(cpu_cache_entries=0)
    ) as session:
        first = session.realize(geometry)
        second = session.realize(geometry)
        stats = session.stats()

    assert calls == 2
    assert first is not second
    assert stats.entries == 0
    assert stats.bytes == 0


def test_parent_store_owns_clear_and_session_close_only_ends_borrow(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    primitives.register("shape", _primitive_spec(lambda _args: _realized(4)))
    geometry = Geometry.create("shape")
    limits = RuntimeLimits()
    store = RealizeCacheStore.from_runtime_limits(limits)
    resources = EvaluationResources()
    context = _evaluation_context(primitives.catalog)
    session = RealizeSession(
        context=context,
        resources=resources,
        cache_store=store,
        runtime_limits=limits,
    )
    replacement: RealizeSession | None = None
    try:
        first = session.realize(geometry)
        store.clear()
        assert session.stats().entries == 0
        assert session.stats().bytes == 0
        second = session.realize(geometry)
        assert second is not first

        session.close()
        session.close()
        assert session.stats().entries == 1
        with pytest.raises(RuntimeError, match="close 済み"):
            session.realize(geometry)

        replacement = RealizeSession(
            context=context,
            resources=resources,
            cache_store=store,
            runtime_limits=limits,
        )
        assert replacement.realize(geometry) is second
    finally:
        session.close()
        if replacement is not None:
            replacement.close()
        resources.close()
        store.close()

    assert store.stats().entries == 0
    assert store.stats().bytes == 0


def test_session_closes_only_resources_and_store_omitted_by_the_caller() -> None:
    default_session = RealizeSession()
    owned_resources = default_session.resources
    owned_store = default_session.cache_store
    default_session.close()
    default_session.close()
    assert owned_resources.closed is True
    assert owned_store.closed is True

    borrowed_resources = EvaluationResources()
    resources_borrower = RealizeSession(resources=borrowed_resources)
    resources_borrower_store = resources_borrower.cache_store
    resources_borrower.close()
    assert borrowed_resources.closed is False
    assert resources_borrower_store.closed is True

    borrowed_store = RealizeCacheStore.from_runtime_limits(RuntimeLimits())
    store_borrower = RealizeSession(cache_store=borrowed_store)
    store_borrower_resources = store_borrower.resources
    store_borrower.close()
    assert store_borrower_resources.closed is True
    assert borrowed_store.closed is False

    borrowed_resources.close()
    borrowed_store.close()


def test_owned_close_attempts_every_dependency_and_preserves_first_base_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FirstCloseFailure(BaseException):
        pass

    class SecondCloseFailure(BaseException):
        pass

    session = RealizeSession()
    resources = session.resources
    store = session.cache_store
    original_resources_close = EvaluationResources.close
    original_store_close = RealizeCacheStore.close
    first_error = FirstCloseFailure("resources")
    second_error = SecondCloseFailure("store")
    calls: list[str] = []

    def close_resources(owner: EvaluationResources) -> None:
        calls.append("resources")
        original_resources_close(owner)
        raise first_error

    def close_store(owner: RealizeCacheStore) -> None:
        calls.append("store")
        original_store_close(owner)
        raise second_error

    monkeypatch.setattr(EvaluationResources, "close", close_resources)
    monkeypatch.setattr(RealizeCacheStore, "close", close_store)

    with pytest.raises(FirstCloseFailure) as exc_info:
        session.close()

    assert exc_info.value is first_error
    assert calls == ["resources", "store"]
    assert resources.closed is True
    assert store.closed is True
    session.close()
    assert calls == ["resources", "store"]


def test_context_exit_preserves_body_error_while_closing_every_owned_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RealizeSession()
    resources = session.resources
    store = session.cache_store
    original_resources_close = EvaluationResources.close
    body_error = RuntimeError("body failed")
    close_calls: list[str] = []

    def close_resources(owner: EvaluationResources) -> None:
        close_calls.append("resources")
        original_resources_close(owner)
        raise KeyboardInterrupt("resource close failed")

    original_store_close = RealizeCacheStore.close

    def close_store(owner: RealizeCacheStore) -> None:
        close_calls.append("store")
        original_store_close(owner)

    monkeypatch.setattr(EvaluationResources, "close", close_resources)
    monkeypatch.setattr(RealizeCacheStore, "close", close_store)

    with pytest.raises(RuntimeError) as exc_info:
        with session:
            raise body_error

    assert exc_info.value is body_error
    assert close_calls == ["resources", "store"]
    assert resources.closed is True
    assert store.closed is True


def test_constructor_failure_closes_created_resources_and_keeps_root_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CleanupFailure(BaseException):
        pass

    created_resources: list[EvaluationResources] = []
    close_calls: list[EvaluationResources] = []
    original_init = EvaluationResources.__init__
    original_close = EvaluationResources.close
    root_error = RuntimeError("cache store construction failed")

    def track_init(owner: EvaluationResources) -> None:
        original_init(owner)
        created_resources.append(owner)

    def close_then_fail(owner: EvaluationResources) -> None:
        close_calls.append(owner)
        original_close(owner)
        raise CleanupFailure("resource cleanup failed")

    def fail_store_creation(
        _cls: type[RealizeCacheStore],
        _limits: RuntimeLimits,
    ) -> RealizeCacheStore:
        raise root_error

    monkeypatch.setattr(EvaluationResources, "__init__", track_init)
    monkeypatch.setattr(EvaluationResources, "close", close_then_fail)
    monkeypatch.setattr(
        RealizeCacheStore,
        "from_runtime_limits",
        classmethod(fail_store_creation),
    )

    with pytest.raises(RuntimeError) as exc_info:
        RealizeSession()

    assert exc_info.value is root_error
    assert len(created_resources) == 1
    assert close_calls == created_resources
    assert created_resources[0].closed is True


def test_close_allows_inflight_leader_to_finish_without_repopulating_cache(
    isolated_catalog: _CatalogPair,
) -> None:
    primitives, _ = isolated_catalog
    entered = threading.Event()
    release = threading.Event()

    def evaluate(_args: tuple[tuple[str, object], ...]) -> RealizedGeometry:
        entered.set()
        assert release.wait(timeout=2.0)
        return _realized(4)

    primitives.register("shape", _primitive_spec(evaluate))
    geometry = Geometry.create("shape")
    session = RealizeSession()
    results: list[RealizedGeometry] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            results.append(session.realize(geometry))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(timeout=2.0)

    session.close()
    release.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert errors == []
    assert len(results) == 1
    assert session.stats().entries == 0
    assert session.stats().bytes == 0
    assert session.resources.closed is True
    assert session.cache_store.closed is True


def test_negative_cache_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="cpu_cache_bytes"):
        RuntimeLimits(cpu_cache_bytes=-1)
    with pytest.raises(ValueError, match="cpu_cache_entries"):
        RuntimeLimits(cpu_cache_entries=-1)
