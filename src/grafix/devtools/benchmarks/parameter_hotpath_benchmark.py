"""大規模 ParamStore の frame 固定費を分離して測る formal benchmark。"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from unittest.mock import patch

from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.favorites import (
    favorite_parameter_key_set,
    favorite_parameter_keys,
    set_parameters_favorite,
)
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.snapshot_ops import ParamSnapshot, store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.devtools.benchmarks.definition import (
    CaseDefinition,
    define_case,
    scaled_case_definitions,
)
from grafix.devtools.benchmarks.metrics import (
    cache_metrics,
    counter_metric,
    gauge_metric,
    summarize_nanoseconds,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    ContractResult,
    Metric,
    evaluate_contract,
    summarize_distribution,
)
import grafix.interactive.parameter_gui.table_commit as table_commit_module
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.group_blocks import (
    group_layout_from_rows,
    visible_group_layout,
)
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.parameter_filter import (
    parameter_search_token_may_be_dynamic,
    parameter_search_tokens,
)
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.table import TableEdits
from grafix.interactive.parameter_gui.table_commit import render_store_parameter_table
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    _parameter_table_model_for_store,
    parameter_table_view_for_store,
)

ParameterHotPathOperation = Literal[
    "layout_reuse",
    "merge_runtime_one",
    "merge_steady",
    "snapshot_one",
    "visibility_default",
    "visibility_search",
    "favorite_view",
]

_SCOPE = "core+parameter-hotpath(no-imgui)"
_SEARCH_MIDI_STRIDE = 1_000


def case_definitions() -> tuple[CaseDefinition, ...]:
    """Parameter GUI/runtime hot-path benchmark cases を返す。"""

    parameter_source = Path(__file__)
    definitions = [
        *_hotpath_case_definitions(),
        define_case(
            "system.parameter_snapshot_model",
            "parameter snapshot/model steady frames",
            category="system",
            suite="system",
            fixture="parameter_snapshot_model",
            parameters={"workload": "parameter_snapshot_model", "rows": 1_000, "frames": 60},
            tags=("system-diagnostic",),
            selectable_suites=("system",),
            setup=setup_parameter_snapshot_model,
            workload=workload_parameter_snapshot_model,
            support_source_files=(parameter_source,),
            self_sampling=True,
        ),
        *scaled_case_definitions(
            prefix="runtime.provenance",
            label="stable parameter provenance",
            values=(100, 1_000, 5_000),
            parameter_name="rows",
            category="runtime",
            suite="pipeline",
            fixture="parameter_store",
            setup=setup_provenance,
            workload=workload_provenance,
            suites=(("smoke", "pipeline"), ("pipeline",), ("soak",)),
            support_source_files=(parameter_source,),
            support_implementations=(benchmark_draw,),
        ),
        define_case(
            "runtime.provenance_changed.rows_1000",
            "changed parameter provenance (1,000)",
            category="runtime",
            suite="pipeline",
            fixture="parameter_store",
            parameters={"rows": 1_000, "changes_per_iteration": 2},
            tags=("changed", "exact-checksum"),
            selectable_suites=("pipeline",),
            setup=setup_provenance_changed,
            workload=workload_provenance_changed,
            support_source_files=(parameter_source,),
            support_implementations=(benchmark_draw,),
        ),
        *scaled_case_definitions(
            prefix="gui.parameter_table",
            label="parameter table steady view",
            values=(100, 1_000, 10_000),
            parameter_name="rows",
            category="gui",
            suite="gui",
            fixture="parameter_store",
            setup=setup_parameter_gui,
            workload=workload_parameter_gui,
            suites=(("smoke", "gui"), ("gui",), ("soak",)),
            support_source_files=(parameter_source,),
        ),
    ]
    return tuple(definitions)


@dataclass(slots=True)
class ParameterHotPathScenario:
    """同一 process 内で繰り返し利用する parameter hot-path state。"""

    operation: ParameterHotPathOperation
    rows: int
    samples: int
    store: ParamStore
    records: list[FrameParamRecord]
    target_key: ParameterKey
    target_meta: ParamMeta
    snapshot: ParamSnapshot
    initial_merge_ms: float
    table_cache: ParameterTableViewCache


def make_parameter_hot_path_scenario(
    parameters: dict[str, Any],
) -> ParameterHotPathScenario:
    """JSON-compatible な定義から benchmark state を構築する。"""

    operation = str(parameters["operation"])
    if operation not in {
        "layout_reuse",
        "merge_runtime_one",
        "merge_steady",
        "snapshot_one",
        "visibility_default",
        "visibility_search",
        "favorite_view",
    }:
        raise ValueError(f"未対応の parameter hot-path operation: {operation!r}")
    rows = int(parameters["rows"])
    samples = int(parameters.get("samples", 24))
    if rows < 1:
        raise ValueError("rows は 1 以上である必要があります")
    if samples < 1:
        raise ValueError("samples は 1 以上である必要があります")

    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=float(rows))
    records = [
        FrameParamRecord(
            key=ParameterKey(
                op="line",
                site_id=f"hotpath-{index:06d}",
                arg="length",
            ),
            base=float(index),
            meta=meta,
            explicit=False,
            effective=float(index),
            source="code",
        )
        for index in range(rows)
    ]
    store = ParamStore()
    started = time.perf_counter_ns()
    merge_frame_params(store, records)
    initial_merge_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    if operation == "visibility_search":
        midi_keys: list[ParameterKey] = []
        for index in range(0, rows, _SEARCH_MIDI_STRIDE):
            record = records[index]
            ok, error = update_state_from_ui(
                store,
                record.key,
                record.base,
                meta=record.meta,
                override=True,
                cc_key=index % 128,
            )
            if not ok:
                raise RuntimeError(f"search fixture MIDI setup failed: {error}")
            midi_keys.append(record.key)
        base_revision = store.revision
        runtime = store._read().runtime()
        for key in midi_keys:
            runtime.last_source_by_key[key] = "midi_live"
        runtime.record_effective_changes(midi_keys)
        store._mutation().commit_runtime(
            expected_revision=base_revision,
            runtime=runtime,
            runtime_value_keys=tuple(midi_keys),
        )
    snapshot = store_snapshot(store)
    target_key = records[0].key

    # setup 固定費と timed steady path を分離する。
    table_cache = ParameterTableViewCache(current_parameter_gui_catalog())
    model = _parameter_table_model_for_store(store, cache=table_cache)
    if operation == "layout_reuse":
        visible_group_layout(model.group_layout, (True,) * len(model.rows))
    if operation == "visibility_default":
        parameter_table_view_for_store(
            store,
            cache=table_cache,
            show_inactive_params=False,
        )
    elif operation == "visibility_search":
        parameter_table_view_for_store(
            store,
            cache=table_cache,
            show_inactive_params=False,
            filter_state=ParameterFilterState(
                query=f"hotpath-{rows - 1:06d}",
            ),
        )
    elif operation == "favorite_view":
        set_parameters_favorite(
            store,
            (record.key for record in records),
            favorite=True,
        )
        favorite_parameter_key_set(store)
        favorite_parameter_keys(store)

    return ParameterHotPathScenario(
        operation=cast(ParameterHotPathOperation, operation),
        rows=rows,
        samples=samples,
        store=store,
        records=records,
        target_key=target_key,
        target_meta=meta,
        snapshot=snapshot,
        initial_merge_ms=initial_merge_ms,
        table_cache=table_cache,
    )


def run_parameter_hot_path_scenario(
    scenario: ParameterHotPathScenario,
) -> BenchmarkOutput:
    """指定 hot path を self-sampling し、semantic contract と共に返す。"""

    if scenario.operation == "layout_reuse":
        return _run_layout_reuse(scenario)
    if scenario.operation == "merge_runtime_one":
        return _run_merge_runtime_one(scenario)
    if scenario.operation == "merge_steady":
        return _run_merge_steady(scenario)
    if scenario.operation == "snapshot_one":
        return _run_snapshot_one(scenario)
    if scenario.operation == "favorite_view":
        return _run_favorite_view(scenario)
    return _run_visibility(scenario)


def _run_layout_reuse(
    scenario: ParameterHotPathScenario,
) -> BenchmarkOutput:
    model = _parameter_table_model_for_store(
        scenario.store,
        cache=scenario.table_cache,
    )
    visible_mask = (True,) * len(model.rows)
    build_ms: list[float] = []
    reuse_ms: list[float] = []
    built_layout = None
    reused_layout = None

    for _ in range(scenario.samples):
        started = time.perf_counter_ns()
        built_layout = group_layout_from_rows(
            model.rows,
            primitive_header_by_group=model.primitive_header_by_group,
            layer_style_name_by_site_id=model.layer_style_name_by_site_id,
            effect_chain_header_by_id=model.effect_chain_header_by_id,
            step_info_by_site=model.step_info_by_site,
            effect_step_ordinal_by_site=model.effect_step_ordinal_by_site,
        )
        build_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)

        started = time.perf_counter_ns()
        reused_layout = visible_group_layout(model.group_layout, visible_mask)
        reuse_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)

    assert built_layout is not None
    assert reused_layout is not None
    built_signature = tuple(
        (
            block.group_id,
            block.header_id,
            block.header,
            tuple((item.row_index, item.visible_label) for item in block.items),
        )
        for block in built_layout
    )
    layout_signature = tuple(
        (
            block.group_id,
            block.header_id,
            block.header,
            tuple((item.row_index, item.visible_label) for item in block.items),
        )
        for block in reused_layout
    )
    built_digest = _digest_items(built_signature)
    layout_digest = _digest_items(layout_signature)
    distribution = summarize_distribution(reuse_ms)
    p95 = distribution.p95 if distribution.p95 is not None else distribution.max
    assert p95 is not None
    return BenchmarkOutput(
        value={
            "operation": scenario.operation,
            "rows": scenario.rows,
            "samples": scenario.samples,
            "built_blocks": len(built_layout),
            "layout_blocks": len(reused_layout),
            "layout_identity_reused": reused_layout is model.group_layout,
            "layout_digest": layout_digest,
        },
        metrics=(
            _distribution_metric("parameter_layout.build", build_ms),
            _distribution_metric("parameter_layout.stable_reuse", reuse_ms),
            _gauge_metric(
                "parameter_layout.blocks",
                len(reused_layout),
                unit="blocks",
            ),
        ),
        contracts=(
            _contract(
                "parameter_layout.stable_identity",
                "hard",
                reused_layout is model.group_layout,
                "eq",
                True,
                "all-visible stable view must reuse immutable group layout",
            ),
            _contract(
                "parameter_layout.block_count_exact",
                "hard",
                len(reused_layout),
                "eq",
                len(built_layout),
                "prebuilt layout must preserve canonical grouping cardinality",
            ),
            _contract(
                "parameter_layout.semantic_layout_exact",
                "hard",
                layout_digest,
                "eq",
                built_digest,
                "reused row/header/label layout must match a canonical rebuild",
            ),
            _contract(
                "parameter_layout.reference_p95",
                "soft",
                float(p95),
                "le",
                1.0,
                "reference target for 10k stable layout reuse is 1 ms p95",
            ),
        ),
    )


def _run_merge_steady(
    scenario: ParameterHotPathScenario,
) -> BenchmarkOutput:
    store = scenario.store
    revision_before = int(store.revision)
    table_revision_before = int(store.table_revision)
    effective_revision_before = int(store.effective_revision)
    semantic_digest_before = _store_semantic_digest(store)
    elapsed_ms: list[float] = []

    for _ in range(scenario.samples):
        records = _fresh_equivalent_records(scenario.records)
        started = time.perf_counter_ns()
        merge_frame_params(store, records)
        elapsed_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)

    revision_delta = int(store.revision) - revision_before
    table_revision_delta = int(store.table_revision) - table_revision_before
    effective_revision_delta = (
        int(store.effective_revision) - effective_revision_before
    )
    distribution = summarize_distribution(elapsed_ms)
    p95 = distribution.p95 if distribution.p95 is not None else distribution.max
    assert p95 is not None
    semantic_digest_after = _store_semantic_digest(store)
    return BenchmarkOutput(
        value={
            "operation": scenario.operation,
            "rows": scenario.rows,
            "samples": scenario.samples,
            "revision_delta": revision_delta,
            "table_revision_delta": table_revision_delta,
            "effective_revision_delta": effective_revision_delta,
            "semantic_digest_before": semantic_digest_before,
            "semantic_digest_after": semantic_digest_after,
            "initial_merge_ms": scenario.initial_merge_ms,
        },
        metrics=(
            _distribution_metric("parameter_merge.steady", elapsed_ms),
            _gauge_metric(
                "parameter_merge.initial_discovery",
                scenario.initial_merge_ms,
                unit="ms",
            ),
            _gauge_metric("parameter_merge.rows", scenario.rows, unit="rows"),
            _gauge_metric(
                "parameter_merge.revision_delta",
                revision_delta,
                unit="revisions",
            ),
            _gauge_metric(
                "parameter_merge.table_revision_delta",
                table_revision_delta,
                unit="revisions",
            ),
            _gauge_metric(
                "parameter_merge.effective_revision_delta",
                effective_revision_delta,
                unit="revisions",
            ),
        ),
        contracts=(
            _contract(
                "parameter_merge.steady.store_revision_stable",
                "hard",
                revision_delta,
                "eq",
                0,
                "stable frame must not advance persistent store revision",
            ),
            _contract(
                "parameter_merge.steady.table_revision_stable",
                "hard",
                table_revision_delta,
                "eq",
                0,
                "stable frame must not rebuild table structure",
            ),
            _contract(
                "parameter_merge.steady.effective_revision_stable",
                "hard",
                effective_revision_delta,
                "eq",
                0,
                "unchanged effective/source values must keep runtime revision",
            ),
            _contract(
                "parameter_merge.steady.semantic_state_stable",
                "hard",
                semantic_digest_after,
                "eq",
                semantic_digest_before,
                "stable merge must preserve snapshot/effective/source/explicit state",
            ),
            _contract(
                "parameter_merge.steady.reference_p95",
                "soft",
                float(p95),
                "le",
                8.0,
                "reference target for 10k steady merge is 8 ms p95",
            ),
        ),
    )


def _run_merge_runtime_one(
    scenario: ParameterHotPathScenario,
) -> BenchmarkOutput:
    """一 key の effective/source 変更が store 全件 copy へ戻らないことを測る。"""

    store = scenario.store
    key = scenario.target_key
    record = scenario.records[0]
    revision_before = int(store.revision)
    table_revision_before = int(store.table_revision)
    effective_revision_before = int(store.effective_revision)
    runtime_token_before = store._read().runtime_token()
    snapshot_before = store_snapshot(store)
    snapshot_digest_before = _snapshot_digest(snapshot_before)
    elapsed_ms: list[float] = []
    sparse_change_matches = 0

    for index in range(scenario.samples):
        effective = 0.25 if index % 2 == 0 else 0.75
        changed_record = dataclasses.replace(
            record,
            effective=effective,
            source="midi_live",
        )
        previous_effective_revision = store.effective_revision
        started = time.perf_counter_ns()
        merge_frame_params(store, [changed_record])
        elapsed_ms.append(
            (time.perf_counter_ns() - started) / 1_000_000.0
        )
        if store.effective_changes_since(
            previous_effective_revision
        ) == frozenset({key}):
            sparse_change_matches += 1

    revision_delta = int(store.revision) - revision_before
    table_revision_delta = int(store.table_revision) - table_revision_before
    effective_revision_delta = (
        int(store.effective_revision) - effective_revision_before
    )
    runtime_identity_reused = (
        store._read().runtime_token() == runtime_token_before
    )
    snapshot_after = store_snapshot(store)
    snapshot_identity_reused = snapshot_after is snapshot_before
    snapshot_digest_after = _snapshot_digest(snapshot_after)
    final_effective = store.last_effective_value(key)
    final_source = store.runtime_view().last_source_by_key.get(key)
    expected_final_effective = (
        0.25 if (scenario.samples - 1) % 2 == 0 else 0.75
    )
    distribution = summarize_distribution(elapsed_ms)
    p95 = (
        distribution.p95
        if distribution.p95 is not None
        else distribution.max
    )
    assert p95 is not None

    return BenchmarkOutput(
        value={
            "operation": scenario.operation,
            "rows": scenario.rows,
            "samples": scenario.samples,
            "revision_delta": revision_delta,
            "table_revision_delta": table_revision_delta,
            "effective_revision_delta": effective_revision_delta,
            "runtime_identity_reused": runtime_identity_reused,
            "snapshot_identity_reused": snapshot_identity_reused,
            "snapshot_digest_before": snapshot_digest_before,
            "snapshot_digest_after": snapshot_digest_after,
            "final_effective": final_effective,
            "final_source": final_source,
            "sparse_change_matches": sparse_change_matches,
        },
        metrics=(
            _distribution_metric(
                "parameter_merge.runtime_one",
                elapsed_ms,
            ),
            _gauge_metric(
                "parameter_merge.runtime_one.rows",
                scenario.rows,
                unit="rows",
            ),
            _gauge_metric(
                "parameter_merge.runtime_one.effective_revision_delta",
                effective_revision_delta,
                unit="revisions",
            ),
            _gauge_metric(
                "parameter_merge.runtime_one.sparse_change_matches",
                sparse_change_matches,
                unit="frames",
            ),
        ),
        contracts=(
            _contract(
                "parameter_merge.runtime_one.store_revision_stable",
                "hard",
                revision_delta,
                "eq",
                0,
                "runtime-only frame must not advance persistent revision",
            ),
            _contract(
                "parameter_merge.runtime_one.table_revision_stable",
                "hard",
                table_revision_delta,
                "eq",
                0,
                "runtime-only frame must not rebuild table structure",
            ),
            _contract(
                "parameter_merge.runtime_one.effective_revision_exact",
                "hard",
                effective_revision_delta,
                "eq",
                scenario.samples,
                "each changed runtime frame must advance effective revision",
            ),
            _contract(
                "parameter_merge.runtime_one.sparse_changes_exact",
                "hard",
                sparse_change_matches,
                "eq",
                scenario.samples,
                "each frame must report only the changed key",
            ),
            _contract(
                "parameter_merge.runtime_one.snapshot_identity",
                "hard",
                snapshot_identity_reused,
                "eq",
                True,
                "runtime-only changes must reuse the persistent snapshot",
            ),
            _contract(
                "parameter_merge.runtime_one.snapshot_semantics",
                "hard",
                snapshot_digest_after,
                "eq",
                snapshot_digest_before,
                "runtime-only changes must preserve persistent parameters",
            ),
            _contract(
                "parameter_merge.runtime_one.runtime_identity",
                "hard",
                runtime_identity_reused,
                "eq",
                True,
                "sparse runtime patch must preserve runtime identity",
            ),
            _contract(
                "parameter_merge.runtime_one.final_value",
                "hard",
                (final_effective, final_source),
                "eq",
                (expected_final_effective, "midi_live"),
                "last changed frame must publish its effective/source pair",
            ),
            _contract(
                "parameter_merge.runtime_one.reference_p95",
                "soft",
                float(p95),
                "le",
                0.1,
                "1,000-row sparse runtime merge reference is 0.1 ms p95",
            ),
        ),
    )


def _run_snapshot_one(
    scenario: ParameterHotPathScenario,
) -> BenchmarkOutput:
    store = scenario.store
    key = scenario.target_key
    meta = scenario.target_meta
    table_revision_before = int(store.table_revision)
    previous_snapshot = scenario.snapshot
    initial_snapshot = previous_snapshot
    initial_digest_before = _snapshot_digest(initial_snapshot)
    previous_value = previous_snapshot[key][1].ui_value
    elapsed_ms: list[float] = []
    old_snapshot_stable = 0
    new_snapshot_matches = 0
    rebuilt_entries = 0
    max_patch_entries = 0

    for index in range(scenario.samples):
        next_value = 0.25 if index % 2 == 0 else 0.75
        ok, error = update_state_from_ui(
            store,
            key,
            next_value,
            meta=meta,
            override=True,
        )
        if not ok:
            raise RuntimeError(f"snapshot benchmark update failed: {error}")

        started = time.perf_counter_ns()
        current_snapshot = store_snapshot(store)
        elapsed_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
        rebuilt_entries += int(store._snapshot_cache_rebuilt_entries)
        max_patch_entries = max(
            max_patch_entries,
            int(getattr(current_snapshot, "patch_entries", 0)),
        )
        old_snapshot_stable += int(previous_snapshot[key][1].ui_value == previous_value)
        new_snapshot_matches += int(current_snapshot[key][1].ui_value == next_value)
        previous_snapshot = current_snapshot
        previous_value = next_value

    scenario.snapshot = previous_snapshot
    initial_digest_after = _snapshot_digest(initial_snapshot)
    final_digest = _snapshot_digest(previous_snapshot)
    table_revision_delta = int(store.table_revision) - table_revision_before
    distribution = summarize_distribution(elapsed_ms)
    p95 = distribution.p95 if distribution.p95 is not None else distribution.max
    assert p95 is not None
    return BenchmarkOutput(
        value={
            "operation": scenario.operation,
            "rows": scenario.rows,
            "samples": scenario.samples,
            "snapshot_entries": len(previous_snapshot),
            "old_snapshot_stable": old_snapshot_stable,
            "new_snapshot_matches": new_snapshot_matches,
            "rebuilt_entries": rebuilt_entries,
            "max_patch_entries": max_patch_entries,
            "initial_digest": initial_digest_before,
            "final_digest": final_digest,
        },
        metrics=(
            _distribution_metric("parameter_snapshot.one_key", elapsed_ms),
            _gauge_metric(
                "parameter_snapshot.entries",
                len(previous_snapshot),
                unit="entries",
            ),
            _gauge_metric(
                "parameter_snapshot.table_revision_delta",
                table_revision_delta,
                unit="revisions",
            ),
            _gauge_metric(
                "parameter_snapshot.rebuilt_entries",
                rebuilt_entries,
                unit="entries",
            ),
            _gauge_metric(
                "parameter_snapshot.max_patch_entries",
                max_patch_entries,
                unit="entries",
            ),
        ),
        contracts=(
            _contract(
                "parameter_snapshot.one_key.entry_count",
                "hard",
                len(previous_snapshot),
                "eq",
                scenario.rows,
                "snapshot must retain every parameter entry",
            ),
            _contract(
                "parameter_snapshot.one_key.old_frame_immutable",
                "hard",
                old_snapshot_stable,
                "eq",
                scenario.samples,
                "previous frame snapshot must remain immutable",
            ),
            _contract(
                "parameter_snapshot.initial_checksum_stable",
                "hard",
                initial_digest_after,
                "eq",
                initial_digest_before,
                "initial snapshot checksum must remain immutable",
            ),
            _contract(
                "parameter_snapshot.one_key.current_value_exact",
                "hard",
                new_snapshot_matches,
                "eq",
                scenario.samples,
                "new snapshot must expose every one-key edit",
            ),
            _contract(
                "parameter_snapshot.one_key.table_revision_stable",
                "hard",
                table_revision_delta,
                "eq",
                0,
                "value-only snapshot churn must not change table structure",
            ),
            _contract(
                "parameter_snapshot.one_key.rebuild_is_sparse",
                "hard",
                rebuilt_entries,
                "eq",
                scenario.samples,
                "each one-key edit must rebuild exactly one snapshot entry",
            ),
            _contract(
                "parameter_snapshot.one_key.patch_is_bounded",
                "hard",
                max_patch_entries,
                "le",
                64,
                "snapshot overlay must remain within its materialization bound",
            ),
            _contract(
                "parameter_snapshot.one_key.reference_p95",
                "soft",
                float(p95),
                "le",
                1.0,
                "reference target for 10k one-key snapshot is 1 ms p95",
            ),
        ),
    )


def _run_visibility(
    scenario: ParameterHotPathScenario,
) -> BenchmarkOutput:
    store = scenario.store
    table_cache = scenario.table_cache
    search = scenario.operation == "visibility_search"
    build_count_before = int(table_cache.model_build_count)
    view_build_count_before = int(table_cache.view_build_count)
    elapsed_ms: list[float] = []
    static_search_ms: list[float] = []
    dynamic_search_ms: list[float] = []
    filtered_count = -1
    total_count = -1
    search_trace: list[tuple[str, int, str | None, str | None]] = []
    expected_search_counts: list[int] = []
    typing_queries = _search_typing_queries(scenario.rows)

    for sample_index in range(scenario.samples):
        state = None
        query = ""
        if search:
            # short/broad/static/dynamic/selective query を巡回し、同一 query の
            # stable cache hit ではなく実際の typing 中 rebuild を測る。
            query, expected_count = typing_queries[sample_index % len(typing_queries)]
            expected_search_counts.append(expected_count)
            state = ParameterFilterState(query=query)
        started = time.perf_counter_ns()
        view = parameter_table_view_for_store(
            store,
            cache=table_cache,
            show_inactive_params=False,
            filter_state=state,
        )
        elapsed = (time.perf_counter_ns() - started) / 1_000_000.0
        elapsed_ms.append(elapsed)
        if search:
            tokens = parameter_search_tokens(query)
            target = (
                dynamic_search_ms
                if any(parameter_search_token_may_be_dynamic(token) for token in tokens)
                else static_search_ms
            )
            target.append(elapsed)
        filtered_count = int(view.filtered_count)
        total_count = int(view.total_count)
        if search:
            visible = view.visible_row_indices
            first = None if not visible else _key_token(view.model.keys[visible[0]])
            last = None if not visible else _key_token(view.model.keys[visible[-1]])
            search_trace.append((query, filtered_count, first, last))

    model_builds = int(table_cache.model_build_count) - build_count_before
    view_builds = int(table_cache.view_build_count) - view_build_count_before
    expected_filtered = expected_search_counts[-1] if search else scenario.rows
    expected_view_builds = scenario.samples if search else 0
    search_count_digest = _digest_items(item[1] for item in search_trace)
    expected_search_count_digest = _digest_items(expected_search_counts)
    visibility_digest = _digest_items(
        _key_token(view.model.keys[index]) for index in view.visible_row_indices
    )
    distribution = summarize_distribution(elapsed_ms)
    p95 = distribution.p95 if distribution.p95 is not None else distribution.max
    assert p95 is not None
    target_ms = 8.0 if search else 1.0
    metrics = [
        _distribution_metric(
            ("parameter_visibility.search" if search else "parameter_visibility.default"),
            elapsed_ms,
        ),
        _gauge_metric(
            "parameter_visibility.filtered_count",
            filtered_count,
            unit="rows",
        ),
        _gauge_metric(
            "parameter_visibility.model_builds",
            model_builds,
            unit="count",
        ),
        _gauge_metric(
            "parameter_visibility.view_builds",
            view_builds,
            unit="count",
        ),
    ]
    if static_search_ms:
        metrics.append(
            _distribution_metric(
                "parameter_visibility.search_static",
                static_search_ms,
            )
        )
    if dynamic_search_ms:
        metrics.append(
            _distribution_metric(
                "parameter_visibility.search_dynamic",
                dynamic_search_ms,
            )
        )
    contracts = [
        _contract(
            "parameter_visibility.total_count",
            "hard",
            total_count,
            "eq",
            scenario.rows,
            "visibility view must retain the full model cardinality",
        ),
        _contract(
            "parameter_visibility.filtered_count",
            "hard",
            filtered_count,
            "eq",
            expected_filtered,
            "default/search mask must select the deterministic row count",
        ),
        (
            _contract(
                "parameter_visibility.search_trace_counts",
                "hard",
                search_count_digest,
                "eq",
                expected_search_count_digest,
                "typing sequence must preserve every broad/dynamic/selective count",
            )
            if search
            else _contract(
                "parameter_visibility.default_trace_not_applicable",
                "hard",
                True,
                "eq",
                True,
                "default visibility has no query trace",
            )
        ),
        _contract(
            "parameter_visibility.structure_reuse",
            "hard",
            model_builds,
            "eq",
            0,
            "stable visibility evaluation must reuse table structure",
        ),
        _contract(
            "parameter_visibility.mask_reuse",
            "hard",
            view_builds,
            "eq",
            expected_view_builds,
            (
                "query typing must rebuild one filter view per changed query"
                if search
                else "stable visibility must reuse the immutable mask"
            ),
        ),
        _contract(
            "parameter_visibility.reference_p95",
            "soft",
            float(p95),
            "le",
            target_ms,
            "reference target is 1 ms default / 8 ms search p95",
        ),
    ]
    if dynamic_search_ms:
        dynamic_distribution = summarize_distribution(dynamic_search_ms)
        dynamic_p95 = (
            dynamic_distribution.p95
            if dynamic_distribution.p95 is not None
            else dynamic_distribution.max
        )
        assert dynamic_p95 is not None
        contracts.append(
            _contract(
                "parameter_visibility.dynamic_search.reference_p95",
                "soft",
                float(dynamic_p95),
                "le",
                8.0,
                "dynamic source/MIDI search reference target is 8 ms p95",
            )
        )
    return BenchmarkOutput(
        value={
            "operation": scenario.operation,
            "rows": scenario.rows,
            "samples": scenario.samples,
            "filtered_count": filtered_count,
            "total_count": total_count,
            "model_builds": model_builds,
            "view_builds": view_builds,
            "search_mode": "query-change" if search else "stable",
            "search_trace": [list(item) for item in search_trace],
            "visibility_digest": visibility_digest,
        },
        metrics=tuple(metrics),
        contracts=tuple(contracts),
    )


def _run_favorite_view(
    scenario: ParameterHotPathScenario,
) -> BenchmarkOutput:
    store = scenario.store
    expected_keys = tuple(record.key for record in scenario.records)
    expected_digest = _digest_items(_key_token(key) for key in expected_keys)
    favorite_revision_before = int(store.favorite_revision)
    table_revision_before = int(store.table_revision)
    immutable_view = favorite_parameter_key_set(store)
    ordered_view = favorite_parameter_keys(store)
    elapsed_ms: list[float] = []
    identity_matches = 0

    for _ in range(scenario.samples):
        started = time.perf_counter_ns()
        current_set = favorite_parameter_key_set(store)
        current_ordered = favorite_parameter_keys(store)
        elapsed_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
        identity_matches += int(current_set is immutable_view and current_ordered is ordered_view)

    actual_digest = _digest_items(_key_token(key) for key in ordered_view)
    revision_delta = int(store.favorite_revision) - favorite_revision_before
    table_revision_delta = int(store.table_revision) - table_revision_before
    distribution = summarize_distribution(elapsed_ms)
    p95 = distribution.p95 if distribution.p95 is not None else distribution.max
    assert p95 is not None
    return BenchmarkOutput(
        value={
            "operation": scenario.operation,
            "rows": scenario.rows,
            "samples": scenario.samples,
            "favorite_count": len(immutable_view),
            "identity_matches": identity_matches,
            "favorite_digest": actual_digest,
        },
        metrics=(
            _distribution_metric("parameter_favorites.stable_view", elapsed_ms),
            _gauge_metric(
                "parameter_favorites.count",
                len(immutable_view),
                unit="rows",
            ),
            _gauge_metric(
                "parameter_favorites.favorite_revision_delta",
                revision_delta,
                unit="revisions",
            ),
            _gauge_metric(
                "parameter_favorites.table_revision_delta",
                table_revision_delta,
                unit="revisions",
            ),
        ),
        contracts=(
            _contract(
                "parameter_favorites.count_exact",
                "hard",
                len(immutable_view),
                "eq",
                scenario.rows,
                "favorite immutable view must retain every selected key",
            ),
            _contract(
                "parameter_favorites.order_exact",
                "hard",
                actual_digest,
                "eq",
                expected_digest,
                "favorite ordered view must preserve deterministic key order",
            ),
            _contract(
                "parameter_favorites.identity_reused",
                "hard",
                identity_matches,
                "eq",
                scenario.samples,
                "stable favorite revision must reuse set and ordered tuple identity",
            ),
            _contract(
                "parameter_favorites.favorite_revision_stable",
                "hard",
                revision_delta,
                "eq",
                0,
                "stable favorite reads must not advance favorite revision",
            ),
            _contract(
                "parameter_favorites.table_revision_stable",
                "hard",
                table_revision_delta,
                "eq",
                0,
                "stable favorite reads must not advance table revision",
            ),
            _contract(
                "parameter_favorites.reference_p95",
                "soft",
                float(p95),
                "le",
                1.0,
                "reference target for 10k stable favorite view is 1 ms p95",
            ),
        ),
    )


def _search_typing_queries(rows: int) -> tuple[tuple[str, int], ...]:
    """known fixture の broad/dynamic/selective typing sequence を返す。"""

    target = f"hotpath-{rows - 1:06d}"
    midi_rows = (rows + _SEARCH_MIDI_STRIDE - 1) // _SEARCH_MIDI_STRIDE
    ui_rows = rows - midi_rows
    return (
        ("h", rows),
        ("x", 0),
        ("ho", rows),
        ("xo", 0),
        ("hot", rows),
        ("hotp", rows),
        ("hotpath", rows),
        ("hotpath-", rows),
        ("hotpath-0", rows),
        ("hotpath-00", rows),
        ("m", midi_rows),
        ("u", ui_rows),
        ("ui", ui_rows),
        ("cod", 0),
        (target, 1),
    )


def _key_token(key: ParameterKey) -> str:
    return f"{key.op}|{key.site_id}|{key.arg}"


def _fresh_equivalent_records(
    records: list[FrameParamRecord],
) -> list[FrameParamRecord]:
    """本番 frame 同様、等価だが新しい record/key object を作る。"""

    return [
        FrameParamRecord(
            key=ParameterKey(
                op=record.key.op,
                site_id=record.key.site_id,
                arg=record.key.arg,
            ),
            base=record.base,
            meta=record.meta,
            effective=record.effective,
            source=record.source,
            explicit=bool(record.explicit),
        )
        for record in records
    ]


def _snapshot_digest(snapshot: ParamSnapshot) -> str:
    keys = sorted(
        snapshot,
        key=lambda key: (key.op, key.site_id, key.arg),
    )
    return _digest_items(
        (
            key.op,
            key.site_id,
            key.arg,
            meta,
            state.override,
            state.ui_value,
            state.cc_key,
            ordinal,
            label,
        )
        for key in keys
        for meta, state, ordinal, label in (snapshot[key],)
    )


def _store_semantic_digest(store: ParamStore) -> str:
    read = store._read()
    runtime = read.runtime()
    snapshot = store_snapshot(store)
    keys = sorted(
        snapshot,
        key=lambda key: (key.op, key.site_id, key.arg),
    )
    return _digest_items(
        (
            _snapshot_digest(snapshot),
            tuple(
                (
                    _key_token(key),
                    runtime.last_effective_by_key.get(key),
                    runtime.last_source_by_key.get(key),
                    read.explicit(key),
                )
                for key in keys
            ),
        )
    )


def _digest_items(items: Iterable[object]) -> str:
    digest = hashlib.sha256()
    for item in items:
        digest.update(repr(item).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _distribution_metric(name: str, values: list[float]) -> Metric:
    return Metric(
        name=name,
        kind="distribution",
        unit="ms",
        phase="measure",
        scope=_SCOPE,
        distribution=summarize_distribution(values),
    )


def _gauge_metric(name: str, value: object, *, unit: str) -> Metric:
    return Metric(
        name=name,
        kind="gauge",
        unit=unit,
        phase="measure",
        scope=_SCOPE,
        value=value,
    )


def _contract(
    contract_id: str,
    severity: str,
    actual: object,
    comparator: str,
    limit: object,
    reason: str,
) -> ContractResult:
    return evaluate_contract(
        contract_id=contract_id,
        severity=severity,
        actual=actual,
        comparator=comparator,
        limit=limit,
        reason=reason,
    )


def _hotpath_case_definitions() -> list[CaseDefinition]:
    """大規模 ParamStore の merge/snapshot/visibility cases を返す。"""

    definitions: list[CaseDefinition] = [
        define_case(
            "gui.parameter_layout.rows_10000",
            "stable parameter group layout (10,000 rows)",
            category="gui",
            suite="parameters",
            fixture="parameter_store_group_layout",
            parameters={
                "operation": "layout_reuse",
                "rows": 10_000,
                "samples": 24,
            },
            tags=(
                "PARAM-05",
                "group-layout",
                "no-imgui",
                "exact-checksum",
            ),
            selectable_suites=("parameters", "soak"),
            setup=setup_parameter_hotpath_scenario,
            workload=workload_parameter_hotpath_scenario,
            support_source_files=(Path(__file__),),
            self_sampling=True,
        )
    ]
    for rows, selectable_suites in (
        (1_000, ("parameters",)),
        (10_000, ("parameters", "soak")),
    ):
        definitions.extend(
            (
                define_case(
                    f"runtime.parameter_merge.rows_{rows}.change_steady",
                    f"stable parameter merge ({rows:,} rows)",
                    category="runtime",
                    suite="parameters",
                    fixture="parameter_store_stable_records",
                    parameters={
                        "operation": "merge_steady",
                        "rows": rows,
                        "samples": 24,
                    },
                    tags=(
                        "PARAM-06",
                        "stable-frame",
                        "no-imgui",
                        "exact-checksum",
                    ),
                    selectable_suites=selectable_suites,
                    setup=setup_parameter_hotpath_scenario,
                    workload=workload_parameter_hotpath_scenario,
                    support_source_files=(Path(__file__),),
                    self_sampling=True,
                ),
                define_case(
                    f"runtime.parameter_snapshot.rows_{rows}.change_one",
                    f"one-key parameter snapshot ({rows:,} rows)",
                    category="runtime",
                    suite="parameters",
                    fixture="parameter_store_single_key_snapshot",
                    parameters={
                        "operation": "snapshot_one",
                        "rows": rows,
                        "samples": 24,
                    },
                    tags=(
                        "PARAM-07",
                        "single-key",
                        "no-imgui",
                        "exact-checksum",
                    ),
                    selectable_suites=selectable_suites,
                    setup=setup_parameter_hotpath_scenario,
                    workload=workload_parameter_hotpath_scenario,
                    support_source_files=(Path(__file__),),
                    self_sampling=True,
                ),
                define_case(
                    f"gui.parameter_visibility.rows_{rows}.mode_default",
                    f"default parameter visibility ({rows:,} rows)",
                    category="gui",
                    suite="parameters",
                    fixture="parameter_store_visibility_default",
                    parameters={
                        "operation": "visibility_default",
                        "rows": rows,
                        "samples": 24,
                    },
                    tags=(
                        "PARAM-08",
                        "visibility",
                        "no-imgui",
                        "exact-checksum",
                    ),
                    selectable_suites=selectable_suites,
                    setup=setup_parameter_hotpath_scenario,
                    workload=workload_parameter_hotpath_scenario,
                    support_source_files=(Path(__file__),),
                    self_sampling=True,
                ),
            )
        )
    definitions.append(
        define_case(
            "runtime.parameter_merge.rows_1000.change_runtime_one",
            "one-key runtime parameter merge (1,000 rows)",
            category="runtime",
            suite="parameters",
            fixture="parameter_store_single_runtime_change",
            parameters={
                "operation": "merge_runtime_one",
                "rows": 1_000,
                "samples": 200,
            },
            tags=(
                "PARAM-06",
                "single-key",
                "runtime-only",
                "no-imgui",
                "exact-checksum",
            ),
            selectable_suites=("parameters",),
            setup=setup_parameter_hotpath_scenario,
            workload=workload_parameter_hotpath_scenario,
            support_source_files=(Path(__file__),),
            self_sampling=True,
        )
    )
    definitions.append(
        define_case(
            "gui.parameter_visibility.rows_10000.mode_search",
            "parameter search visibility (10,000 rows)",
            category="gui",
            suite="parameters",
            fixture="parameter_store_visibility_search",
            parameters={
                "operation": "visibility_search",
                "rows": 10_000,
                "samples": 24,
            },
            tags=(
                "PARAM-08",
                "search",
                "no-imgui",
                "exact-checksum",
            ),
            selectable_suites=("parameters", "soak"),
            setup=setup_parameter_hotpath_scenario,
            workload=workload_parameter_hotpath_scenario,
            support_source_files=(Path(__file__),),
            self_sampling=True,
        )
    )
    definitions.append(
        define_case(
            "gui.parameter_favorites.rows_10000",
            "stable parameter favorite view (10,000 rows)",
            category="gui",
            suite="parameters",
            fixture="parameter_store_favorite_view",
            parameters={
                "operation": "favorite_view",
                "rows": 10_000,
                "samples": 24,
            },
            tags=(
                "PARAM-09",
                "favorite",
                "no-imgui",
                "exact-checksum",
            ),
            selectable_suites=("parameters", "soak"),
            setup=setup_parameter_hotpath_scenario,
            workload=workload_parameter_hotpath_scenario,
            support_source_files=(Path(__file__),),
            self_sampling=True,
        )
    )
    return definitions


def benchmark_draw(_t: float) -> tuple[()]:
    return ()


def setup_provenance(parameters: dict[str, Any], _seed: int) -> object:
    from grafix.core.parameters import ParameterCaptureState
    from grafix.export.capture_provenance import CaptureProvenanceBuilder
    from grafix.runtime_config_loader import runtime_config

    store = parameter_store_fixture(rows=int(parameters["rows"]))
    builder = CaptureProvenanceBuilder(
        benchmark_draw,
        config=runtime_config(),
        parameter_state=ParameterCaptureState("code", "primary"),
        parameter_store_path=None,
    )
    return builder, store


def workload_provenance(state: object) -> BenchmarkOutput:
    builder, store = cast(tuple[Any, Any], state)
    provenance = builder.frame(
        store,
        t=0.0,
        frame_index=0,
        quality="draft",
        origin="interactive",
    )
    parameters = provenance.frame.parameters
    return BenchmarkOutput(
        value={
            "revision": int(parameters.revision),
            "entry_count": int(parameters.entry_count),
            "sha256": parameters.sha256,
        },
        metrics=(
            counter_metric(
                "entry_count",
                int(parameters.entry_count),
                unit="count",
                phase="measure",
                scope="provenance",
            ),
        ),
    )


def setup_provenance_changed(parameters: dict[str, Any], seed: int) -> object:
    from grafix.core.parameters.frame_params import FrameParamRecord

    builder, store = cast(
        tuple[Any, Any],
        setup_provenance(parameters, seed),
    )
    runtime = store._read().runtime()
    key = next(iter(runtime.last_effective_by_key))
    meta = store.get_meta(key)
    if meta is None:
        raise RuntimeError("provenance benchmark parameter metadata is missing")
    record = FrameParamRecord(
        key=key,
        base=runtime.last_effective_by_key[key],
        meta=meta,
        explicit=False,
        effective=runtime.last_effective_by_key[key],
        source="code",
    )
    return builder, store, record


def workload_provenance_changed(state: object) -> BenchmarkOutput:
    from grafix.core.parameters.merge_ops import merge_frame_params

    builder, store, record = cast(tuple[Any, Any, Any], state)
    # 1 workload 内で A→B と2回変更し、各snapshotを具体化する。最終Bを固定
    # するため、warmup/calibration回数によらずsemantic checksumは一定になる。
    merge_frame_params(store, [dataclasses.replace(record, effective=-1.0)])
    builder.frame(
        store,
        t=0.0,
        frame_index=0,
        quality="draft",
        origin="interactive",
    )
    merge_frame_params(store, [dataclasses.replace(record, effective=-2.0)])
    provenance = builder.frame(
        store,
        t=0.0,
        frame_index=0,
        quality="draft",
        origin="interactive",
    )
    parameters = provenance.frame.parameters
    return BenchmarkOutput(
        value={
            "revision": int(parameters.revision),
            "entry_count": int(parameters.entry_count),
            "sha256": parameters.sha256,
        },
        metrics=(
            counter_metric(
                "entry_count",
                int(parameters.entry_count),
                unit="count",
                phase="measure",
                scope="provenance",
            ),
            counter_metric(
                "changes_per_iteration",
                2,
                unit="count",
                phase="measure",
                scope="provenance",
            ),
        ),
    )


@dataclass(slots=True)
class _ParameterGuiBenchmarkState:
    """Formal GUI case の store と session-local table cache。"""

    store: ParamStore
    table_cache: ParameterTableViewCache


def setup_parameter_gui(parameters: dict[str, Any], _seed: int) -> object:
    store = parameter_store_fixture(rows=int(parameters["rows"]))
    return _ParameterGuiBenchmarkState(
        store=store,
        table_cache=ParameterTableViewCache(current_parameter_gui_catalog()),
    )


def workload_parameter_gui(state: object) -> BenchmarkOutput:
    if not isinstance(state, _ParameterGuiBenchmarkState):
        raise TypeError("parameter GUI benchmark state is invalid")

    view = parameter_table_view_for_store(
        state.store,
        cache=state.table_cache,
        show_inactive_params=True,
    )
    value = {
        "total_count": int(view.total_count),
        "filtered_count": int(view.filtered_count),
        "visible_count": int(sum(view.visible_mask)),
    }
    return BenchmarkOutput(
        value=value,
        metrics=tuple(
            counter_metric(
                name,
                metric_value,
                unit="count",
                phase="measure",
                scope="parameter_gui",
            )
            for name, metric_value in (
                ("total_count", value["total_count"]),
                ("filtered_count", value["filtered_count"]),
                ("visible_count", value["visible_count"]),
                ("model_builds", int(state.table_cache.model_build_count)),
            )
        ),
    )


def setup_parameter_hotpath_scenario(
    parameters: dict[str, Any],
    _seed: int,
) -> object:
    return make_parameter_hot_path_scenario(parameters)


def workload_parameter_hotpath_scenario(state: object) -> BenchmarkOutput:
    if not isinstance(state, ParameterHotPathScenario):
        raise TypeError("parameter hot-path scenario state is invalid")
    return run_parameter_hot_path_scenario(state)


def parameter_store_fixture(*, rows: int) -> ParamStore:
    row_count = max(1, int(rows))
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=float(row_count))
    records = [
        FrameParamRecord(
            key=ParameterKey(
                op="line",
                site_id=f"model-bench-{index:06d}",
                arg="length",
            ),
            base=float(index),
            meta=meta,
            explicit=False,
            effective=float(index),
            source="code",
        )
        for index in range(row_count)
    ]
    store = ParamStore()
    merge_frame_params(store, records)
    return store


def parameter_snapshot_model_workload(
    store: ParamStore,
    *,
    frames: int,
) -> dict[str, Any]:
    """実 UI を呼ばず、snapshot/model と毎frame準備だけを通す。"""

    frame_count = max(2, int(frames))
    table_cache = ParameterTableViewCache(current_parameter_gui_catalog())
    render_calls = 0
    visible_rows = 0
    widget_state = WidgetSessionState()

    def fake_render(render_input: Any, **_kwargs: Any) -> TableEdits:
        nonlocal render_calls, visible_rows
        render_calls += 1
        visible_rows = len(render_input.model_rows)
        rows = tuple(
            render_input.model_rows[item.row_index]
            for block in render_input.group_layout
            for item in block.items
        )
        return TableEdits(
            rows=rows,
            collapsed_headers=render_input.collapsed_headers,
            midi_learn_state=render_input.midi_learn_state,
        )

    samples: list[int] = []
    first_frame_ns = 0
    with patch.object(table_commit_module, "render_parameter_table", fake_render):
        for frame in range(frame_count):
            started = time.perf_counter_ns()
            table_view = parameter_table_view_for_store(
                store,
                cache=table_cache,
                show_inactive_params=True,
            )
            changed = render_store_parameter_table(
                store,
                table_view=table_view,
                widget_state=widget_state,
            )
            elapsed = time.perf_counter_ns() - started
            if changed.changed:
                raise RuntimeError("benchmark の fake UI が store を変更した")
            if frame == 0:
                first_frame_ns = elapsed
            else:
                samples.append(elapsed)

    build_count = table_cache.model_build_count
    steady = summarize_nanoseconds(samples)
    return {
        "output": {
            "frames": frame_count,
            "rows": visible_rows,
            "snapshot_entries": len(store_snapshot(store)),
            "render_calls": render_calls,
            "model_builds": build_count,
            "first_frame_ms": float(first_frame_ns) / 1_000_000.0,
            "steady_median_ms": steady["median_ms"],
            "steady_p95_ms": steady["p95_ms"],
        },
        "cache": {
            "hits": max(0, frame_count - build_count),
            "misses": build_count,
            "evictions": 0,
            "entries": int(build_count > 0),
            "bytes": 0,
        },
    }


def setup_parameter_snapshot_model(parameters: dict[str, Any], _seed: int) -> object:
    state = dict(parameters)
    state["store"] = parameter_store_fixture(rows=int(state["rows"]))
    return state


def workload_parameter_snapshot_model(state: object) -> BenchmarkOutput:
    values = cast(dict[str, Any], state)
    payload = parameter_snapshot_model_workload(values["store"], frames=int(values["frames"]))
    output = payload["output"]
    semantic = {key: output[key] for key in ("frames", "rows", "snapshot_entries", "render_calls")}
    cache = cast(dict[str, Any], payload["cache"])
    return BenchmarkOutput(
        value=semantic,
        metrics=(
            *(
                counter_metric(
                    name, int(output[name]), unit="count", phase="measure", scope="system"
                )
                for name in ("frames", "rows", "snapshot_entries", "render_calls", "model_builds")
            ),
            gauge_metric(
                "first_frame_ms",
                float(output["first_frame_ms"]),
                unit="ms",
                phase="measure",
                scope="system",
            ),
            gauge_metric(
                "steady_median_ms",
                float(output["steady_median_ms"]),
                unit="ms",
                phase="measure",
                scope="system",
            ),
            gauge_metric(
                "steady_p95_ms",
                float(output["steady_p95_ms"]),
                unit="ms",
                phase="measure",
                scope="system",
            ),
            *cache_metrics(cache, name="cache", phase="measure", scope="system"),
        ),
    )


__all__ = [
    "case_definitions",
    "ParameterHotPathScenario",
    "make_parameter_hot_path_scenario",
    "run_parameter_hot_path_scenario",
    "parameter_store_fixture",
    "parameter_snapshot_model_workload",
]
