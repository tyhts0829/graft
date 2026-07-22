"""MpDraw の実 worker process lifecycle と process round-trip 契約。"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from grafix.api import G
from grafix.core.authoring_loader import load_config_authoring_definitions
from grafix.core.geometry import Geometry
from grafix.core.layer import Layer
from grafix.core.operation_catalog import bind_operation_catalog
from grafix.core.operation_diagnostics import emit_operation_diagnostic
from grafix.core.parameters import (
    MidiFrameSnapshot,
    ParamStore,
    parameter_context,
)
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.preview_quality import current_preview_quality
from grafix.core.runtime_config import current_runtime_config
from grafix.interactive.runtime.mp_draw import (
    DrawResult,
    MpDraw,
    MpDrawWorkerError,
)
from grafix.runtime_config_loader import runtime_config

pytestmark = pytest.mark.integration


_WAIT_TIMEOUT_S = 8.0


_EFFECTIVE_CONFIG = runtime_config()


def _mp_draw(draw: Any, **kwargs: Any) -> MpDraw:
    return MpDraw(draw, effective_config=_EFFECTIVE_CONFIG, **kwargs)


def _empty_draw(_t: float) -> Geometry:
    return Geometry.create(op="concat")


def _runtime_config_probe_draw(_t: float) -> Layer:
    config = current_runtime_config()
    return Layer(
        geometry=Geometry.create(op="concat"),
        site_id="runtime-config-probe",
        name=str(config.output_dir),
    )


def _config_recipe_probe_draw(_t: float) -> Geometry:
    return G.mp_config_recipe_shape()  # type: ignore[attr-defined]


def _system_exit_draw(_t: float) -> Geometry:
    raise SystemExit(3)


def _os_exit_draw(_t: float) -> Geometry:
    os._exit(7)


def _draw_that_fails_at_one(t: float) -> Geometry:
    if float(t) >= 1.0:
        raise ValueError("intentional frame failure")
    return Geometry.create(op="concat")


def _draw_that_hangs_at_one(t: float) -> Geometry:
    if float(t) == 1.0:
        time.sleep(60.0)
    return Geometry.create(op="concat")


def _midi_parameter_draw(_t: float) -> Geometry:
    return G.circle(radius=0.25, key="midi-roundtrip")


def _quality_diagnostic_draw(_t: float) -> Geometry:
    quality = current_preview_quality()
    emit_operation_diagnostic(
        op="quality",
        original_value=quality,
        effective_value=quality,
        reason="quality roundtrip",
        severity="info",
    )
    return Geometry.create(op="concat")


def _wait_for_result(mp_draw: MpDraw) -> DrawResult:
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        result = mp_draw.poll_latest()
        if result is not None:
            return result
        time.sleep(0.01)
    pytest.fail("mp-draw result timeout")


def _wait_for_worker_error(mp_draw: MpDraw) -> MpDrawWorkerError:
    deadline = time.monotonic() + _WAIT_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            mp_draw.poll_latest()
        except MpDrawWorkerError as exc:
            return exc
        time.sleep(0.01)
    pytest.fail("mp-draw worker death timeout")


@pytest.mark.parametrize("n_worker", [1, 2])
def test_workers_report_ready_and_normal_close_leaves_no_children(
    n_worker: int,
) -> None:
    """1 worker と複数 worker が同じ lifecycle 契約を満たす。"""

    mp_draw = _mp_draw(_empty_draw, n_worker=n_worker)
    procs = list(mp_draw._procs)
    worker_pids = {int(proc.pid) for proc in procs if proc.pid is not None}

    assert mp_draw.ready_worker_pids == worker_pids

    mp_draw.submit(
        t=0.125,
        snapshot_revision=0,
        snapshot={},
        effect_order_snapshot={},
        epoch=0,
        quality="draft",
    )
    result = _wait_for_result(mp_draw)
    assert result.error is None
    assert result.t == pytest.approx(0.125)
    assert len(result.layers) == 1

    mp_draw.close()
    mp_draw.close()

    with pytest.raises(RuntimeError) as exc_info:
        mp_draw.poll_latest()
    assert not isinstance(exc_info.value, MpDrawWorkerError)

    assert all(not proc.is_alive() for proc in procs)
    assert all(proc.exitcode == 0 for proc in procs)
    active_pids = {proc.pid for proc in mp.active_children()}
    assert worker_pids.isdisjoint(active_pids)


def test_spawn_worker_draw_uses_explicit_runtime_config() -> None:
    worker_config = replace(
        _EFFECTIVE_CONFIG,
        output_dir=_EFFECTIVE_CONFIG.output_dir / "spawn-worker-config",
    )
    mp_draw = MpDraw(
        _runtime_config_probe_draw,
        n_worker=1,
        effective_config=worker_config,
    )
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        result = _wait_for_result(mp_draw)
    finally:
        mp_draw.close()

    assert result.error is None
    assert result.layers[0].name == str(worker_config.output_dir)


def test_spawn_worker_uses_parent_config_authoring_recipe(
    tmp_path: Path,
) -> None:
    config_root = tmp_path / "config-authoring"
    config_root.mkdir()
    source_path = config_root / "shape.py"

    def source(value: float) -> str:
        return (
            "import numpy as np\n"
            "from grafix import primitive\n"
            "@primitive(meta={'value': {'kind': 'float'}})\n"
            f"def mp_config_recipe_shape(value={value}):\n"
            "    coords = np.asarray([[value, 0.0, 0.0]], dtype=np.float32)\n"
            "    return coords, np.asarray([0, 1], dtype=np.int32)\n"
        )

    source_path.write_text(source(8.0), encoding="utf-8")
    config = replace(
        _EFFECTIVE_CONFIG,
        preset_module_dirs=(config_root,),
    )
    definitions = load_config_authoring_definitions(config)
    parent_entry = definitions.operations.resolve(
        "primitive",
        "mp_config_recipe_shape",
    )
    with bind_operation_catalog(definitions.operations):
        parent_geometry = _config_recipe_probe_draw(0.0)
    source_path.write_text(source(80.0), encoding="utf-8")

    mp_draw = MpDraw(
        _config_recipe_probe_draw,
        n_worker=1,
        effective_config=config,
        definitions=definitions,
    )
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        result = _wait_for_result(mp_draw)
    finally:
        mp_draw.close()

    assert result.error is None
    worker_geometry = result.layers[0].geometry
    assert worker_geometry.operation == parent_entry.ref
    assert worker_geometry.operation == parent_geometry.operation
    assert worker_geometry.id == parent_geometry.id
    assert dict(worker_geometry.args)["value"] == 8.0


def test_hung_evaluation_restarts_worker_and_recovers_without_child_leak() -> None:
    mp_draw = _mp_draw(
        _draw_that_hangs_at_one,
        n_worker=1,
        evaluation_timeout=0.1,
    )
    old_procs = list(mp_draw._procs)
    all_worker_pids = {int(proc.pid) for proc in old_procs if proc.pid is not None}

    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        successful = _wait_for_result(mp_draw)
        assert successful.error is None
        assert successful.generation == 0

        mp_draw.submit(
            t=1.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        call_durations: list[float] = []
        deadline = time.monotonic() + _WAIT_TIMEOUT_S
        while mp_draw.generation == 0 and time.monotonic() < deadline:
            started_at = time.monotonic()
            assert mp_draw.poll_latest() is None
            call_durations.append(time.monotonic() - started_at)
            time.sleep(0.01)

        assert mp_draw.generation == 1
        assert mp_draw.restart_count == 1
        assert mp_draw.last_restart_reason is not None
        assert "evaluation timeout" in mp_draw.last_restart_reason
        # timeout/restart の呼び出しは ready 待ちをせず、UI loop を有界に保つ。
        assert max(call_durations) < 0.75
        # restart 中も preview fallback は直近の成功 frame を保持する。
        assert mp_draw.latest_successful_result() is successful

        assert all(not proc.is_alive() for proc in old_procs)
        new_pids = {int(proc.pid) for proc in mp_draw._procs if proc.pid is not None}
        all_worker_pids.update(new_pids)
        assert new_pids.isdisjoint({int(proc.pid) for proc in old_procs if proc.pid is not None})

        # 新世代は snapshot ACK をまだ持たないため、task 同梱 snapshot だけで
        # 新しい revision を評価できなければならない。
        mp_draw.submit(
            t=0.25,
            snapshot_revision=7,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        recovered = _wait_for_result(mp_draw)
        assert recovered.error is None
        assert recovered.t == pytest.approx(0.25)
        assert recovered.generation == 1
        assert recovered.snapshot_revision == 7
        assert recovered.worker_pid in new_pids
    finally:
        mp_draw.close()

    active_pids = {int(proc.pid) for proc in mp.active_children() if proc.pid is not None}
    assert all_worker_pids.isdisjoint(active_pids)


def test_error_result_keeps_last_successful_layers_for_preview() -> None:
    mp_draw = _mp_draw(_draw_that_fails_at_one, n_worker=2)
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        successful = _wait_for_result(mp_draw)
        assert successful.error is None
        assert successful.t == pytest.approx(0.0)
        assert mp_draw.latest_layers() is successful.layers

        mp_draw.submit(
            t=1.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="draft",
        )
        failed = _wait_for_result(mp_draw)
        assert failed.error is not None
        assert failed.t == pytest.approx(1.0)
        assert "intentional frame failure" in failed.error

        # poll_latest() は失敗を通知するが、preview 用 scene まで空にしない。
        assert mp_draw.latest_layers() is successful.layers
    finally:
        mp_draw.close()


def test_worker_roundtrip_preserves_frozen_midi_value_source() -> None:
    store = ParamStore()
    with parameter_context(store):
        _midi_parameter_draw(0.0)
    snapshot = store_snapshot(store)
    radius_key = next(key for key in snapshot if key.op == "circle" and key.arg == "radius")
    radius_meta = store.get_meta(radius_key)
    assert radius_meta is not None
    ok, error = update_state_from_ui(
        store,
        radius_key,
        0.25,
        meta=radius_meta,
        override=False,
        cc_key=7,
    )
    assert ok and error is None

    mp_draw = _mp_draw(_midi_parameter_draw, n_worker=1)
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=store.revision,
            snapshot=store_snapshot(store),
            effect_order_snapshot={},
            cc_snapshot=MidiFrameSnapshot.from_mapping(
                {7: 0.5},
                source="midi_frozen",
            ),
            epoch=0,
            quality="draft",
        )
        result = _wait_for_result(mp_draw)
    finally:
        mp_draw.close()

    radius_record = next(record for record in result.records if record.key == radius_key)
    assert radius_record.source == "midi_frozen"
    assert radius_record.effective == pytest.approx(100.0)
    assert result.snapshot_revision == store.revision


def test_worker_result_carries_explicit_epoch() -> None:
    mp_draw = _mp_draw(_empty_draw, n_worker=2)
    try:
        mp_draw.submit(
            t=4.25,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=7,
            quality="draft",
        )
        result = _wait_for_result(mp_draw)
        assert mp_draw.current_epoch == 7
        assert result.epoch == 7
        assert result.t == pytest.approx(4.25)
    finally:
        mp_draw.close()


@pytest.mark.parametrize(
    ("draw", "expected_exitcode"),
    [
        pytest.param(_system_exit_draw, 3, id="SystemExit"),
        pytest.param(_os_exit_draw, 7, id="os._exit"),
    ],
)
def test_fatal_draw_exit_fails_fast_with_worker_identity(
    draw: Callable[[float], Geometry], expected_exitcode: int
) -> None:
    mp_draw = _mp_draw(draw, n_worker=2)
    workers = {(proc.name, proc.pid) for proc in mp_draw._procs}

    try:
        try:
            mp_draw.submit(
                t=0.0,
                snapshot_revision=0,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
        except MpDrawWorkerError as exc:
            error = exc
        else:
            error = _wait_for_worker_error(mp_draw)
    finally:
        mp_draw.close()

    assert (error.worker, error.pid) in workers
    assert error.exitcode == expected_exitcode
    assert f"exitcode={expected_exitcode}" in str(error)


def test_poll_detects_single_worker_death_without_fallback() -> None:
    mp_draw = _mp_draw(_empty_draw, n_worker=2)
    dead = mp_draw._procs[0]
    dead.terminate()
    dead.join(timeout=_WAIT_TIMEOUT_S)

    try:
        with pytest.raises(MpDrawWorkerError) as exc_info:
            mp_draw.poll_latest()
    finally:
        mp_draw.close()

    assert exc_info.value.worker == dead.name
    assert exc_info.value.pid == dead.pid
    assert exc_info.value.exitcode == dead.exitcode


def test_submit_detects_all_worker_death_without_fallback() -> None:
    mp_draw = _mp_draw(_empty_draw, n_worker=2)
    procs = list(mp_draw._procs)
    for proc in procs:
        proc.terminate()
    for proc in procs:
        proc.join(timeout=_WAIT_TIMEOUT_S)

    try:
        with pytest.raises(MpDrawWorkerError) as exc_info:
            mp_draw.submit(
                t=0.0,
                snapshot_revision=0,
                snapshot={},
                effect_order_snapshot={},
                epoch=0,
                quality="draft",
            )
    finally:
        mp_draw.close()

    identities = {(proc.name, proc.pid, proc.exitcode) for proc in procs}
    error = exc_info.value
    assert (error.worker, error.pid, error.exitcode) in identities


def test_mp_draw_quality_roundtrips_into_worker_context() -> None:
    mp_draw = _mp_draw(_quality_diagnostic_draw, n_worker=1)
    try:
        mp_draw.submit(
            t=0.0,
            snapshot_revision=0,
            snapshot={},
            effect_order_snapshot={},
            epoch=0,
            quality="final",
        )
        result = _wait_for_result(mp_draw)
        assert result.error is None
        assert result.diagnostics[0].effective_value == "final"
        assert result.worker_lag_ms is not None
        assert result.worker_lag_ms >= 0.0
    finally:
        mp_draw.close()
