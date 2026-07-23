"""PARAM-01: 単一 parameter changed-frame の formal benchmark。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from grafix.core.parameters.adjustment_snapshot import ParameterAdjustmentSnapshot
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.devtools.benchmarks.definition import CaseDefinition, define_case
from grafix.devtools.benchmarks.parameter_hotpath_benchmark import (
    parameter_store_fixture,
)
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    ContractResult,
    Metric,
    evaluate_contract,
    summarize_distribution,
)
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.table_model import ParameterTableModel
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    _parameter_table_model_for_store,
    parameter_table_view_for_store,
)

_SCOPE = "core+parameter-table-model(no-imgui)"
_TARGET_KEY = ParameterKey(
    op="line",
    site_id="model-bench-000000",
    arg="length",
)


def case_definitions() -> tuple[CaseDefinition, ...]:
    """PARAM-01 の 100/1,000/10,000-row formal cases を返す。"""

    return tuple(
        define_case(
            f"gui.parameter_edit.rows_{rows}",
            f"single-key parameter changed-frame ({rows:,} rows)",
            category="gui",
            suite="gui",
            fixture="parameter_store_single_key_edit",
            parameters={"rows": rows, "changed_frames": changed_frames},
            tags=(
                "PARAM-01",
                "single-key",
                "changed-frame",
                "no-imgui",
                "exact-checksum",
            ),
            selectable_suites=selectable_suites,
            setup=setup_parameter_edit_scenario,
            workload=workload_parameter_edit_scenario,
            support_source_files=(
                Path(__file__),
                Path(__file__).with_name("parameter_hotpath_benchmark.py"),
            ),
            self_sampling=True,
        )
        for rows, changed_frames, selectable_suites in (
            (100, 12, ("smoke", "gui")),
            (1_000, 12, ("gui",)),
            (10_000, 6, ("soak",)),
        )
    )


def setup_parameter_edit_scenario(
    parameters: dict[str, Any],
    _seed: int,
) -> object:
    """Parameter edit scenario を構築する。"""

    return make_parameter_edit_scenario(parameters)


def workload_parameter_edit_scenario(state: object) -> BenchmarkOutput:
    """Parameter edit scenario を一回実行する。"""

    if not isinstance(state, ParameterEditScenario):
        raise TypeError("parameter edit scenario state is invalid")
    return run_parameter_edit_scenario(state)


@dataclass(slots=True)
class ParameterEditScenario:
    """再利用可能な parameter edit benchmark state。"""

    rows: int
    changed_frames: int
    store: ParamStore
    key: ParameterKey
    meta: ParamMeta
    history: ParamStoreHistory
    table_cache: ParameterTableViewCache
    model: ParameterTableModel


def make_parameter_edit_scenario(
    parameters: dict[str, Any],
) -> ParameterEditScenario:
    """JSON-compatible parameters から検証済み benchmark state を作る。"""

    rows = int(parameters["rows"])
    changed_frames = int(parameters["changed_frames"])
    if rows < 1:
        raise ValueError("rows は 1 以上である必要があります")
    if changed_frames < 1:
        raise ValueError("changed_frames は 1 以上である必要があります")

    store = parameter_store_fixture(rows=rows)
    meta = store.get_meta(_TARGET_KEY)
    if meta is None:
        raise RuntimeError("parameter edit benchmark metadata is missing")
    history = ParamStoreHistory(store)
    table_cache = ParameterTableViewCache(current_parameter_gui_catalog())
    model = _parameter_table_model_for_store(store, cache=table_cache)
    # 実際の slider 操作は Parameter panel が少なくとも 1 frame 描画された後に
    # 始まる。初回 view/layout 構築を changed-frame tail に混ぜず、操作中の
    # cache hit / sparse invalidation を同じ条件で測る。
    parameter_table_view_for_store(
        store,
        cache=table_cache,
        show_inactive_params=True,
    )
    return ParameterEditScenario(
        rows=rows,
        changed_frames=changed_frames,
        store=store,
        key=_TARGET_KEY,
        meta=meta,
        history=history,
        table_cache=table_cache,
        model=model,
    )


def run_parameter_edit_scenario(
    scenario: ParameterEditScenario,
) -> BenchmarkOutput:
    """単一 key の changed-frame hot path と Undo/Redo を検証・計測する。

    実 ImGui widget draw、RSS、allocation はこの scope に含めない。テーブルの
    immutable structure cache、変更行だけの sparse refresh、表示 mask overlay
    までを明示した CPU benchmark である。
    """

    store = scenario.store
    history = scenario.history
    key = scenario.key
    meta = scenario.meta
    changed_frames = scenario.changed_frames
    model = scenario.model
    table_cache = scenario.table_cache

    state_before = store.get_state(key)
    if state_before is None:
        raise RuntimeError("parameter edit benchmark state is missing")
    before_state = (
        state_before.ui_value,
        bool(state_before.override),
        state_before.cc_key,
    )
    start_revision = int(store.revision)
    start_table_revision = int(store.table_revision)
    start_value_revision = int(store.value_revision)
    start_build_count = int(table_cache.model_build_count)

    history_patch_ms: list[float] = []
    state_apply_ms: list[float] = []
    sparse_refresh_ms: list[float] = []
    structure_reuse_ms: list[float] = []
    value_overlay_ms: list[float] = []
    changed_frame_ms: list[float] = []
    successful_state_applies = 0
    sparse_value_matches = 0
    model_reuse_frames = 0
    max_changed_keys = 0
    max_changed_row_identities = 0
    visible_rows = 0
    full_snapshot_captures = 0

    low_value = 0.25
    high_value = 0.75
    ascending = float(state_before.ui_value) != high_value
    final_value = high_value if ascending else low_value

    original_full_capture = store.capture_adjustment_snapshot

    def counted_full_capture() -> ParameterAdjustmentSnapshot:
        nonlocal full_snapshot_captures
        full_snapshot_captures += 1
        return original_full_capture()

    with patch.object(
        store,
        "capture_adjustment_snapshot",
        counted_full_capture,
    ):
        for frame in range(1, changed_frames + 1):
            frame_started = time.perf_counter_ns()
            before_value_revision = int(store.value_revision)
            previous_model = model
            fraction = float(frame) / float(changed_frames)
            edited_value = (
                low_value + (high_value - low_value) * fraction
                if ascending
                else high_value - (high_value - low_value) * fraction
            )

            transaction_started = time.perf_counter_ns()
            with history.transaction(
                source=(key, "formal-parameter-edit"),
                patch=True,
            ):
                transaction_entered = time.perf_counter_ns()
                state_started = transaction_entered
                ok, error = update_state_from_ui(
                    store,
                    key,
                    edited_value,
                    meta=meta,
                    override=True,
                )
                state_finished = time.perf_counter_ns()
                transaction_exiting = time.perf_counter_ns()
            transaction_finished = time.perf_counter_ns()
            if not ok:
                raise RuntimeError(f"parameter edit failed: {error}")
            successful_state_applies += 1
            history_patch_ms.append(
                float(
                    (transaction_entered - transaction_started)
                    + (transaction_finished - transaction_exiting)
                )
                / 1_000_000.0
            )
            state_apply_ms.append(float(state_finished - state_started) / 1_000_000.0)

            changed_keys = store.value_changes_since(before_value_revision)
            changed_key_count = scenario.rows + 1 if changed_keys is None else len(changed_keys)
            max_changed_keys = max(max_changed_keys, changed_key_count)

            sparse_started = time.perf_counter_ns()
            model = _parameter_table_model_for_store(store, cache=table_cache)
            sparse_finished = time.perf_counter_ns()
            sparse_refresh_ms.append(float(sparse_finished - sparse_started) / 1_000_000.0)
            changed_row_identities = sum(
                before is not after
                for before, after in zip(
                    previous_model.rows,
                    model.rows,
                    strict=True,
                )
            )
            max_changed_row_identities = max(
                max_changed_row_identities,
                changed_row_identities,
            )
            row_index = model.row_index_by_key[key]
            sparse_value_matches += int(model.rows[row_index].ui_value == edited_value)

            reuse_started = time.perf_counter_ns()
            reused_model = _parameter_table_model_for_store(store, cache=table_cache)
            reuse_finished = time.perf_counter_ns()
            structure_reuse_ms.append(float(reuse_finished - reuse_started) / 1_000_000.0)
            model_reuse_frames += int(reused_model is model)

            overlay_started = time.perf_counter_ns()
            view = parameter_table_view_for_store(
                store,
                cache=table_cache,
                show_inactive_params=True,
            )
            overlay_finished = time.perf_counter_ns()
            value_overlay_ms.append(float(overlay_finished - overlay_started) / 1_000_000.0)
            visible_rows = int(view.filtered_count)
            changed_frame_ms.append(float(overlay_finished - frame_started) / 1_000_000.0)

    changed_revision_delta = int(store.revision) - start_revision
    changed_table_revision_delta = int(store.table_revision) - start_table_revision
    changed_value_revision_delta = int(store.value_revision) - start_value_revision
    changed_frame_model_builds = (
        int(table_cache.model_build_count) - start_build_count
    )
    final_state = store.get_state(key)
    if final_state is None:
        raise RuntimeError("parameter edit final state is missing")
    expected_final_state = (
        final_state.ui_value,
        bool(final_state.override),
        final_state.cc_key,
    )

    undo_changed = history.undo()
    undo_state = store.get_state(key)
    undo_matches = (
        undo_state is not None
        and (
            undo_state.ui_value,
            bool(undo_state.override),
            undo_state.cc_key,
        )
        == before_state
    )
    undo_model = _parameter_table_model_for_store(store, cache=table_cache)
    undo_model_matches = (
        undo_state is not None
        and undo_model.rows[undo_model.row_index_by_key[key]].ui_value == undo_state.ui_value
    )

    redo_changed = history.redo()
    redo_state = store.get_state(key)
    redo_matches = (
        redo_state is not None
        and (
            redo_state.ui_value,
            bool(redo_state.override),
            redo_state.cc_key,
        )
        == expected_final_state
    )
    redo_model = _parameter_table_model_for_store(store, cache=table_cache)
    redo_model_matches = (
        redo_state is not None
        and redo_model.rows[redo_model.row_index_by_key[key]].ui_value == redo_state.ui_value
    )
    total_revision_delta = int(store.revision) - start_revision
    total_model_builds = int(table_cache.model_build_count)
    scenario.model = redo_model

    output_value = {
        "scope": _SCOPE,
        "rows": scenario.rows,
        "changed_frames": changed_frames,
        "single_key_changed": max_changed_keys == 1,
        "single_row_refreshed": max_changed_row_identities == 1,
        "structure_reused": changed_frame_model_builds == 0,
        "undo_correct": bool(undo_changed and undo_matches and undo_model_matches),
        "redo_correct": bool(redo_changed and redo_matches and redo_model_matches),
        "real_imgui_measured": False,
        "rss_or_allocations_measured": False,
    }
    metrics = (
        _distribution_metric("param_edit.changed_frame.total", changed_frame_ms),
        _distribution_metric(
            "param_edit.changed_frame.history_patch",
            history_patch_ms,
        ),
        _distribution_metric(
            "param_edit.changed_frame.state_apply",
            state_apply_ms,
        ),
        _distribution_metric(
            "param_edit.changed_frame.value_sparse_refresh",
            sparse_refresh_ms,
        ),
        _distribution_metric(
            "param_edit.changed_frame.table_structure_model_reuse",
            structure_reuse_ms,
        ),
        _distribution_metric(
            "param_edit.changed_frame.value_overlay_view",
            value_overlay_ms,
        ),
        _counter_metric("param_edit.rows", scenario.rows),
        _counter_metric("param_edit.changed_frames", changed_frames),
        _counter_metric(
            "param_edit.changed_frame.full_snapshot_captures",
            full_snapshot_captures,
        ),
        _counter_metric(
            "param_edit.changed_frame.table_model_builds",
            changed_frame_model_builds,
        ),
        _counter_metric(
            "param_edit.changed_frame.successful_state_applies",
            successful_state_applies,
        ),
        _counter_metric(
            "param_edit.changed_frame.sparse_value_matches",
            sparse_value_matches,
        ),
        _counter_metric(
            "param_edit.changed_frame.model_reuse_frames",
            model_reuse_frames,
        ),
        _gauge_metric(
            "param_edit.changed_frame.max_changed_keys",
            max_changed_keys,
            unit="keys",
        ),
        _gauge_metric(
            "param_edit.changed_frame.max_changed_row_identities",
            max_changed_row_identities,
            unit="rows",
        ),
        _gauge_metric(
            "param_edit.changed_frame.revision_delta",
            changed_revision_delta,
            unit="revisions",
        ),
        _gauge_metric(
            "param_edit.changed_frame.table_revision_delta",
            changed_table_revision_delta,
            unit="revisions",
        ),
        _gauge_metric(
            "param_edit.changed_frame.value_revision_delta",
            changed_value_revision_delta,
            unit="revisions",
        ),
        _gauge_metric(
            "param_edit.undo_redo.total_revision_delta",
            total_revision_delta,
            unit="revisions",
        ),
        _gauge_metric(
            "param_edit.table_model_builds_total",
            total_model_builds,
            unit="count",
        ),
        _gauge_metric(
            "param_edit.final_value",
            float(redo_state.ui_value) if redo_state is not None else -1.0,
            unit="value",
        ),
        Metric(
            name="param_edit.measurement_scope",
            kind="gauge",
            unit="text",
            phase="measure",
            scope=_SCOPE,
            value=_SCOPE,
        ),
    )
    contracts = (
        _hard_contract(
            "param_edit.changed_frame.full_snapshot_zero",
            full_snapshot_captures,
            "eq",
            0,
            "single-key changed-frame must use patch history without full snapshot",
        ),
        _hard_contract(
            "param_edit.changed_frame.structure_build_zero",
            changed_frame_model_builds,
            "eq",
            0,
            "value edits must not rebuild table structure",
        ),
        _hard_contract(
            "param_edit.changed_frame.table_revision_stable",
            changed_table_revision_delta,
            "eq",
            0,
            "value edits must not advance table structure revision",
        ),
        _hard_contract(
            "param_edit.changed_frame.revision_exact",
            changed_revision_delta,
            "eq",
            changed_frames,
            "each changed frame must advance store revision exactly once",
        ),
        _hard_contract(
            "param_edit.changed_frame.value_revision_exact",
            changed_value_revision_delta,
            "eq",
            changed_frames,
            "each changed frame must advance value revision exactly once",
        ),
        _hard_contract(
            "param_edit.changed_frame.state_apply_success",
            successful_state_applies,
            "eq",
            changed_frames,
            "all changed-frame state applications must succeed",
        ),
        _hard_contract(
            "param_edit.changed_frame.single_key_bound",
            max_changed_keys,
            "le",
            1,
            "a single-key edit must report at most one changed key",
        ),
        _hard_contract(
            "param_edit.changed_frame.single_row_bound",
            max_changed_row_identities,
            "le",
            1,
            "sparse refresh must replace at most one row identity",
        ),
        _hard_contract(
            "param_edit.changed_frame.sparse_values_exact",
            sparse_value_matches,
            "eq",
            changed_frames,
            "sparse model refresh must expose every edited value",
        ),
        _hard_contract(
            "param_edit.changed_frame.model_reuse_exact",
            model_reuse_frames,
            "eq",
            changed_frames,
            "unchanged structure lookup must reuse the refreshed model",
        ),
        _hard_contract(
            "param_edit.changed_frame.visible_rows_exact",
            visible_rows,
            "eq",
            scenario.rows,
            "unfiltered value overlay must retain every row",
        ),
        _hard_contract(
            "param_edit.undo.state_exact",
            bool(undo_changed and undo_matches and undo_model_matches),
            "eq",
            True,
            "Undo must restore both store value and sparse table model",
        ),
        _hard_contract(
            "param_edit.redo.state_exact",
            bool(redo_changed and redo_matches and redo_model_matches),
            "eq",
            True,
            "Redo must restore both store value and sparse table model",
        ),
        _hard_contract(
            "param_edit.undo_redo.revision_exact",
            total_revision_delta,
            "eq",
            changed_frames + 2,
            "successful Undo and Redo must each advance revision once",
        ),
        _hard_contract(
            "param_edit.table_model.single_build",
            total_model_builds,
            "eq",
            1,
            "setup structure model must be reused through edit, Undo, and Redo",
        ),
        _hard_contract(
            "param_edit.final_value.exact",
            (redo_state is not None and float(redo_state.ui_value) == float(final_value)),
            "eq",
            True,
            "Redo must leave the deterministic final value visible",
        ),
    )
    return BenchmarkOutput(
        value=output_value,
        metrics=metrics,
        contracts=contracts,
    )


def _distribution_metric(name: str, values: list[float]) -> Metric:
    return Metric(
        name=name,
        kind="distribution",
        unit="ms",
        phase="drag",
        scope=_SCOPE,
        distribution=summarize_distribution(values),
    )


def _counter_metric(name: str, value: int) -> Metric:
    return Metric(
        name=name,
        kind="counter",
        unit="count",
        phase="drag",
        scope=_SCOPE,
        value=int(value),
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


def _hard_contract(
    contract_id: str,
    actual: object,
    comparator: str,
    limit: object,
    reason: str,
) -> ContractResult:
    return evaluate_contract(
        contract_id=contract_id,
        severity="hard",
        actual=actual,
        comparator=comparator,
        limit=limit,
        reason=reason,
    )


__all__ = [
    "case_definitions",
    "ParameterEditScenario",
    "make_parameter_edit_scenario",
    "run_parameter_edit_scenario",
]
