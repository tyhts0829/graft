"""
Purpose:
    spawn worker内で固定snapshotを使ってuser drawを評価し、親へ正規化済み結果を返す。
Use when:
    worker側のsnapshot適用、task拒否、authoring generation、終了処理を変更するとき。
Constraints:
    - entrypointはspawn可能なmodule top-levelに保ち、親のlive ParamStoreへ触れない。
    - parameter snapshotとeffect-order snapshotは同じrevisionの一組として適用する。
    - requested revisionがcurrentでないtaskはTaskStarted前に拒否する。
    - workerはdraw/normalizeまでを担当し、realize、render、publishを所有しない。
Side effects:
    子processでuser codeを実行し、専用Queue endpointを読み書きして終了時に閉じる。
"""

from __future__ import annotations

import multiprocessing as mp
import multiprocessing.queues as mp_queues
import os
import queue
import traceback
from collections.abc import Callable
from typing import Any, cast

from grafix.authoring_loader import load_authoring_definitions_recipe
from grafix.core.authoring_recipe import AuthoringDefinitionsRecipe
from grafix.core.operation_catalog import bind_operation_catalog
from grafix.core.operation_diagnostics import (
    OperationDiagnostic,
    current_operation_diagnostics,
)
from grafix.core.parameters import EffectOrderSnapshot
from grafix.core.parameters.context import parameter_context_from_snapshot
from grafix.core.parameters.snapshot_ops import ParamSnapshot
from grafix.core.preset_catalog import bind_preset_catalog
from grafix.core.preview_quality import preview_quality_context
from grafix.core.runtime_config import RuntimeConfig, bind_runtime_config
from grafix.core.scene import SceneItem, normalize_scene
from grafix.core.value_validation import exact_integer
from grafix.interactive.runtime._mp_draw_protocol import (
    DrawResult,
    _DrawTask,
    _SnapshotAck,
    _SnapshotUpdate,
    _TaskRejected,
    _TaskStarted,
    _WorkerMessage,
    _WorkerReady,
    _non_empty_string,
    decode_draw_task,
    decode_snapshot_update,
)


def _draw_worker_main(
    task_q: mp_queues.Queue[_DrawTask | None],
    control_q: mp_queues.Queue[_SnapshotUpdate],
    result_q: mp_queues.Queue[_WorkerMessage],
    draw: Callable[[float], SceneItem],
    generation: int,
    effective_config: RuntimeConfig,
    authoring_recipe: AuthoringDefinitionsRecipe,
) -> None:
    """worker プロセスのエントリポイント。

    `task_q` から `_DrawTask` を受け取り、`draw(t)` を実行して `DrawResult` を `result_q`
    に返す。`task_q` に `None` が入ってきたら終了する。

    Notes
    -----
    worker は別プロセスなので、親プロセスの ParamStore には触れない。
    `parameter_context_from_snapshot()` で snapshot を固定し、観測結果だけを返す。
    """

    current = mp.current_process()
    worker = _non_empty_string(current.name, name="worker")
    pid = os.getpid()
    worker_generation = exact_integer(
        generation,
        name="generation",
        minimum=0,
    )
    # 親が capture した exact source recipe から immutable snapshot を再構築する。
    # config directory は worker 側で再走査しない。
    # ReloadedDraw は呼び出し中に、source bytes から再構築したより狭い candidate
    # catalog を内側へ束縛する。
    worker_definitions = load_authoring_definitions_recipe(authoring_recipe)
    result_q.put(_WorkerReady(worker=worker, pid=pid, generation=worker_generation))

    snapshot: ParamSnapshot | None = None
    effect_order_snapshot: EffectOrderSnapshot | None = None
    snapshot_revision: int | None = None

    def apply_snapshot(update: _SnapshotUpdate) -> None:
        """新しい snapshot だけを適用し、処理結果を必ず ack する。"""

        nonlocal snapshot, effect_order_snapshot, snapshot_revision
        if update.generation != worker_generation:
            return
        requested = update.revision
        if snapshot_revision is None or requested > snapshot_revision:
            snapshot = update.snapshot
            effect_order_snapshot = update.effect_order_snapshot
            snapshot_revision = requested
            status = "applied"
        elif requested == snapshot_revision:
            status = "current"
        else:
            status = "stale"
        assert snapshot_revision is not None
        result_q.put(
            _SnapshotAck(
                worker=worker,
                pid=pid,
                requested_revision=requested,
                applied_revision=snapshot_revision,
                status=status,
                generation=worker_generation,
            )
        )

    def drain_snapshot_updates() -> None:
        while True:
            try:
                update = decode_snapshot_update(control_q.get_nowait())
            except queue.Empty:
                return
            apply_snapshot(update)

    try:
        while True:
            drain_snapshot_updates()
            try:
                task = decode_draw_task(task_q.get(timeout=0.01))
            except queue.Empty:
                continue
            if task is None:
                return
            if task.generation != worker_generation:
                continue
            drain_snapshot_updates()
            requested_revision = task.snapshot_revision
            if task.snapshot is not None:
                # task と snapshot を同じ work item に束ねることで、slider drag 中に
                # control ACK が 1 revision 遅れても、この task の評価を開始できる。
                assert task.effect_order_snapshot is not None
                apply_snapshot(
                    _SnapshotUpdate(
                        revision=requested_revision,
                        snapshot=task.snapshot,
                        effect_order_snapshot=task.effect_order_snapshot,
                        generation=worker_generation,
                    )
                )
            if snapshot_revision != requested_revision:
                reason = (
                    "unknown"
                    if snapshot_revision is None or requested_revision > snapshot_revision
                    else "stale"
                )
                result_q.put(
                    _TaskRejected(
                        frame_id=task.frame_id,
                        worker=worker,
                        pid=pid,
                        requested_revision=requested_revision,
                        applied_revision=snapshot_revision,
                        reason=reason,
                        generation=worker_generation,
                    )
                )
                continue
            assert snapshot is not None
            assert effect_order_snapshot is not None
            evaluation_snapshot = snapshot
            evaluation_effect_order_snapshot = effect_order_snapshot
            result_q.put(
                _TaskStarted(
                    frame_id=task.frame_id,
                    worker=worker,
                    pid=pid,
                    generation=worker_generation,
                )
            )
            frame_operation_diagnostics: tuple[OperationDiagnostic, ...] = ()
            try:
                # snapshot を固定したコンテキスト内で draw を実行することで、
                # GUI の状態（ParamStore）と独立に「このフレームで解決すべき値」を決定できる。
                with (
                    bind_operation_catalog(worker_definitions.operations),
                    bind_preset_catalog(worker_definitions.presets),
                    bind_runtime_config(effective_config),
                    preview_quality_context(task.quality),
                ):
                    with parameter_context_from_snapshot(
                        evaluation_snapshot,
                        cc_snapshot=task.cc_snapshot,
                        effect_order_snapshot=evaluation_effect_order_snapshot,
                    ) as frame_params:
                        try:
                            scene = draw(task.t)
                            layers = normalize_scene(scene)
                        finally:
                            frame_operation_diagnostics = current_operation_diagnostics()
                result_q.put(
                    DrawResult(
                        frame_id=task.frame_id,
                        layers=tuple(layers),
                        # frame_params は worker 内で作ったバッファなので、値だけをコピーして返す。
                        records=tuple(frame_params.records),
                        labels=tuple(frame_params.labels),
                        effect_chains=tuple(frame_params.effect_chains),
                        error=None,
                        t=task.t,
                        epoch=task.epoch,
                        generation=worker_generation,
                        worker_pid=pid,
                        diagnostics=frame_operation_diagnostics,
                        snapshot_revision=requested_revision,
                    )
                )
            except Exception:
                # 通常の draw 例外は失敗結果として返す。SystemExit 等で process 自体が
                # 終了した場合は、親側の health check が MpDrawWorkerError として検知する。
                result_q.put(
                    DrawResult(
                        frame_id=task.frame_id,
                        layers=(),
                        records=(),
                        labels=(),
                        effect_chains=(),
                        error=traceback.format_exc(),
                        t=task.t,
                        epoch=task.epoch,
                        generation=worker_generation,
                        worker_pid=pid,
                        diagnostics=frame_operation_diagnostics,
                        snapshot_revision=requested_revision,
                    )
                )
    finally:
        # Queue はプロセスごとに feeder thread を持ち得る。正常終了と SystemExit の
        # どちらでも、このプロセスが所有する endpoint を閉じて flush を待つ。
        for raw_queue in (task_q, control_q, result_q):
            worker_queue = cast(mp_queues.Queue[Any], raw_queue)
            try:
                worker_queue.close()
                worker_queue.join_thread()
            except (OSError, ValueError):
                pass

__all__: list[str] = []
