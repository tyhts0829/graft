"""
Purpose:
    重いPNG/G-code captureをboundedな長寿命workerへ委譲し、親側で公開を確定する。
Use when:
    export FIFO、memory admission、worker timeout/restart、staging commitを変更するとき。
Constraints:
    - accepted jobは置換せずFIFOで処理し、countとaggregate snapshot bytesを越えた入力は拒否する。
    - workerはprivate stagingへのencodeだけを行い、artifact familyとmanifestのpublishは親が行う。
    - provenance必須のimmutable snapshotをprocess境界の単位とする。
    - timeout、死亡、cancel時はworker/Queueを回収し、対応stagingと保持snapshotを解放する。
    - 同じimmutable snapshotの共有を保持量の二重計上で破らない。
Side effects:
    spawn processとQueueを所有し、staging作成、encode、filesystem publishを行う。
"""

from __future__ import annotations

import multiprocessing as mp
import multiprocessing.process as mp_process
import multiprocessing.queues as mp_queues
import os
import queue
import time
import traceback
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from grafix.core.capture_provenance import CaptureProvenance
from grafix.core.export_format import ExportFormat
from grafix.core.gcode_params import GCodeParams
from grafix.core.lifecycle import CleanupErrors
from grafix.core.pipeline import RealizedLayer
from grafix.core.runtime_limits import DEFAULT_FINAL_RUNTIME_LIMITS, RuntimeLimits
from grafix.core.value_validation import (
    exact_integer,
    exact_string_choice,
    finite_real,
    positive_integer_pair,
    rgb01_tuple,
)
from grafix.export.capture import CaptureService
from grafix.export.capture_staging import (
    CaptureStaging,
    capture_staging_work_path,
    cleanup_capture_staging,
    validate_capture_staged_outputs,
)

_WORKER_JOIN_TIMEOUT_S = 0.5
_PARENT_TIMEOUT_GRACE_S = 0.25
_COMPLETED_RESULT_LIMIT = 64
# 1つのin-flight snapshotは、親のimmutable geometry、multiprocessing Queueの
# serialization buffer、workerでunpickleしたgeometryの最大3世代を同時に持ち得る。
# pending jobにも同じ係数を保守的に課し、queue全体がprocessをまたいでbudget内に
# 収まる契約にする（Python objectの小さなoverheadは推定対象外）。
_SNAPSHOT_PROCESS_COPY_FACTOR = 3

# 親 process と spawn worker はそれぞれ独立した service instance を持つ。
_CAPTURE_SERVICE = CaptureService()


class ExportQueueFullError(RuntimeError):
    """明示 capture が件数または aggregate byte budget で拒否された。"""

    def __init__(
        self,
        *,
        reason: str,
        request_count: int,
        request_limit: int,
        retained_bytes: int,
        requested_bytes: int,
        byte_limit: int,
    ) -> None:
        self.reason = exact_string_choice(
            reason,
            name="reason",
            choices=("count", "bytes"),
        )
        self.request_count = exact_integer(
            request_count,
            name="request_count",
            minimum=0,
        )
        self.request_limit = exact_integer(
            request_limit,
            name="request_limit",
            minimum=0,
        )
        self.retained_bytes = exact_integer(
            retained_bytes,
            name="retained_bytes",
            minimum=0,
        )
        self.requested_bytes = exact_integer(
            requested_bytes,
            name="requested_bytes",
            minimum=0,
        )
        self.byte_limit = exact_integer(
            byte_limit,
            name="byte_limit",
            minimum=0,
        )
        projected = self.retained_bytes + self.requested_bytes
        detail = (
            "capture queue が満杯のため rejected: "
            f"reason={self.reason}, requests={self.request_count}/{self.request_limit}, "
            f"estimated-retained={self.retained_bytes / (1024 * 1024):.1f} MiB, "
            f"estimated-request={self.requested_bytes / (1024 * 1024):.1f} MiB, "
            f"projected={projected / (1024 * 1024):.1f} MiB, "
            f"limit={self.byte_limit / (1024 * 1024):.1f} MiB"
        )
        super().__init__(detail)


class ExportJobStatus(StrEnum):
    """export job の終端状態。"""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    WORKER_DIED = "worker_died"


def _path(value: object, *, name: str) -> Path:
    """暗黙 Path 化を行わず Path instance を返す。"""

    if not isinstance(value, Path):
        raise TypeError(f"{name} は Path である必要があります")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameExportSnapshot:
    """export 対象となる 1 フレーム分の不変データ。"""

    layers: tuple[RealizedLayer, ...]
    canvas_size: tuple[int, int]
    background_color_rgb01: tuple[float, float, float]
    t: float
    provenance: CaptureProvenance | None = None
    gcode_params: GCodeParams | None = None

    def __post_init__(self) -> None:
        if type(self.layers) is not tuple or not all(
            isinstance(layer, RealizedLayer) for layer in self.layers
        ):
            raise TypeError("layers は RealizedLayer の tuple である必要があります")
        canvas_size = positive_integer_pair(self.canvas_size, name="canvas_size")
        background = rgb01_tuple(
            self.background_color_rgb01,
            name="background_color_rgb01",
        )
        capture_t = finite_real(self.t, name="t")
        if self.provenance is not None and not isinstance(
            self.provenance,
            CaptureProvenance,
        ):
            raise TypeError("provenance は CaptureProvenance または None である必要があります")
        if self.gcode_params is not None and not isinstance(
            self.gcode_params,
            GCodeParams,
        ):
            raise TypeError("gcode_params は GCodeParams または None である必要があります")

        object.__setattr__(self, "t", capture_t)
        object.__setattr__(self, "canvas_size", canvas_size)
        object.__setattr__(self, "background_color_rgb01", background)
        if self.provenance is not None and self.provenance.frame.t != capture_t:
            raise ValueError("provenance.frame.t は snapshot.t と一致する必要があります")

    @property
    def retained_bytes(self) -> int:
        """親 process が保持する immutable geometry array の推定 byte 数。"""

        return estimate_snapshot_retained_bytes(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class CaptureExportSnapshot(FrameExportSnapshot):
    """capture/export 境界を越えられる provenance 必須 snapshot。"""

    provenance: CaptureProvenance

    def __post_init__(self) -> None:
        FrameExportSnapshot.__post_init__(self)
        if not isinstance(self.provenance, CaptureProvenance):
            raise TypeError("capture snapshot には CaptureProvenance が必要です")

    @classmethod
    def from_snapshot(
        cls,
        snapshot: FrameExportSnapshot,
    ) -> CaptureExportSnapshot:
        """preview snapshot の provenance invariant を一度だけ検査して昇格する。"""

        provenance = snapshot.provenance
        if provenance is None:
            raise ValueError("capture snapshot の provenance がありません")
        if isinstance(snapshot, cls):
            return snapshot
        return cls(
            layers=snapshot.layers,
            canvas_size=snapshot.canvas_size,
            background_color_rgb01=snapshot.background_color_rgb01,
            t=snapshot.t,
            provenance=provenance,
            gcode_params=snapshot.gcode_params,
        )


def estimate_snapshot_retained_bytes(snapshot: FrameExportSnapshot) -> int:
    """snapshot が参照する geometry array の重複を除いた byte 数を返す。

    ``RealizedGeometry`` は immutable なので、同じ array を複数 layer が共有する場合は
    物理メモリも共有される。配列 identity ごとに一度だけ数え、Python object の小さな
    overhead は含めない。
    """

    seen_arrays: set[int] = set()
    total = 0
    for layer in snapshot.layers:
        realized = layer.realized
        for array in (realized.coords, realized.offsets):
            identity = id(array)
            if identity in seen_arrays:
                continue
            seen_arrays.add(identity)
            total += int(array.nbytes)
    return int(total)


def _estimate_snapshot_process_bytes(snapshot: FrameExportSnapshot) -> int:
    """queue輸送中にprocessをまたいで同時保持し得るbyte数を保守的に返す。"""

    return int(estimate_snapshot_retained_bytes(snapshot)) * int(
        _SNAPSHOT_PROCESS_COPY_FACTOR
    )


@dataclass(frozen=True, slots=True)
class ExportQueueStatus:
    """capture admission の現在値。GUI と拒否診断で同じ契約を共有する。"""

    request_count: int
    request_limit: int
    retained_bytes: int
    byte_limit: int


def _normalize_output_size(
    format: ExportFormat,
    output_size: tuple[int, int] | None,
) -> tuple[int, int] | None:
    """PNG 専用の出力寸法を検査して正規化する。"""

    if format is ExportFormat.PNG:
        if output_size is None:
            raise ValueError("PNG export には output_size が必要です")
    elif output_size is not None:
        raise ValueError("output_size は PNG export にのみ指定できます")
    if output_size is None:
        return None
    return positive_integer_pair(output_size, name="output_size")


def _validate_export_request(
    *,
    format: object,
    snapshot: object,
    split_gcode_layers: object,
    output_size: tuple[int, int] | None,
) -> tuple[
    ExportFormat,
    CaptureExportSnapshot,
    bool,
    tuple[int, int] | None,
]:
    """export request 共通 field の invariant を一箇所で検証する。"""

    if not isinstance(format, ExportFormat):
        raise TypeError("format は ExportFormat である必要があります")
    if not isinstance(snapshot, CaptureExportSnapshot):
        raise TypeError("snapshot は CaptureExportSnapshot である必要があります")
    if type(split_gcode_layers) is not bool:
        raise TypeError("split_gcode_layers は bool である必要があります")
    if split_gcode_layers and format is not ExportFormat.GCODE:
        raise ValueError("split_gcode_layers は G-code export にのみ指定できます")
    if format is ExportFormat.GCODE and snapshot.gcode_params is None:
        raise ValueError("G-code export snapshot には gcode_params が必要です")

    normalized_output_size = _normalize_output_size(
        format,
        output_size,
    )
    return format, snapshot, split_gcode_layers, normalized_output_size


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportJob:
    """worker へ渡す不変 export request。"""

    job_id: int
    format: ExportFormat
    snapshot: CaptureExportSnapshot
    output_path: Path
    timeout_s: float
    staging_dir: Path = field(compare=False, repr=False)
    base_output_path: Path | None = field(default=None, compare=False)
    split_gcode_layers: bool = False
    output_size: tuple[int, int] | None = None
    deadline_monotonic: float | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        _, _, _, normalized_output_size = _validate_export_request(
            format=self.format,
            snapshot=self.snapshot,
            split_gcode_layers=self.split_gcode_layers,
            output_size=self.output_size,
        )
        job_id = exact_integer(self.job_id, name="job_id", minimum=1)
        output_path = _path(self.output_path, name="output_path")
        base_output_path = (
            output_path
            if self.base_output_path is None
            else _path(self.base_output_path, name="base_output_path")
        )
        staging_dir = _path(self.staging_dir, name="staging_dir")
        timeout_s = finite_real(
            self.timeout_s,
            name="timeout_s",
            minimum=0.0,
            minimum_inclusive=False,
        )
        deadline = (
            None
            if self.deadline_monotonic is None
            else finite_real(
                self.deadline_monotonic,
                name="deadline_monotonic",
            )
        )
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "output_path", output_path)
        object.__setattr__(self, "base_output_path", base_output_path)
        object.__setattr__(self, "timeout_s", timeout_s)
        object.__setattr__(self, "staging_dir", staging_dir)
        object.__setattr__(self, "output_size", normalized_output_size)
        object.__setattr__(self, "deadline_monotonic", deadline)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExportJobResult:
    """export job の終端結果。"""

    job_id: int
    format: ExportFormat
    status: ExportJobStatus
    output_path: Path
    split_gcode_layers: bool = False
    paths: tuple[Path, ...] = ()
    error: str | None = None
    worker_pid: int | None = None
    worker_exitcode: int | None = None
    manifest_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.format, ExportFormat):
            raise TypeError("format は ExportFormat である必要があります")
        if type(self.split_gcode_layers) is not bool:
            raise TypeError("split_gcode_layers は bool である必要があります")
        if (
            self.split_gcode_layers
            and self.format is not ExportFormat.GCODE
        ):
            raise ValueError(
                "split_gcode_layers は G-code export にのみ指定できます"
            )
        if not isinstance(self.status, ExportJobStatus):
            raise TypeError("status は ExportJobStatus である必要があります")
        job_id = exact_integer(self.job_id, name="job_id", minimum=1)
        output_path = _path(self.output_path, name="output_path")
        if type(self.paths) is not tuple or not all(
            isinstance(path, Path) for path in self.paths
        ):
            raise TypeError("paths は Path の tuple である必要があります")
        if self.error is not None and type(self.error) is not str:
            raise TypeError("error は str または None である必要があります")
        worker_pid = (
            None
            if self.worker_pid is None
            else exact_integer(self.worker_pid, name="worker_pid", minimum=1)
        )
        worker_exitcode = (
            None
            if self.worker_exitcode is None
            else exact_integer(self.worker_exitcode, name="worker_exitcode")
        )
        manifest_path = (
            None
            if self.manifest_path is None
            else _path(self.manifest_path, name="manifest_path")
        )
        object.__setattr__(self, "job_id", job_id)
        object.__setattr__(self, "output_path", output_path)
        object.__setattr__(self, "worker_pid", worker_pid)
        object.__setattr__(self, "worker_exitcode", worker_exitcode)
        object.__setattr__(self, "manifest_path", manifest_path)


@dataclass(frozen=True, slots=True)
class _WorkerReady:
    pid: int


_WorkerMessage = _WorkerReady | ExportJobResult
ExportBackend = Callable[[ExportJob], Sequence[Path]]


def _commit_staged_outputs(
    job: ExportJob,
    staged_paths: Sequence[Path],
    *,
    capture_service: CaptureService | None = None,
) -> tuple[tuple[Path, ...], Path]:
    """成果物と manifest を上書きなしの一世代として親側で公開する。"""

    service = _CAPTURE_SERVICE if capture_service is None else capture_service
    base_output_path = job.base_output_path
    if base_output_path is None:
        raise RuntimeError("validated ExportJob に base_output_path がありません")
    published = service.publish_staged_with_retry(
        job.snapshot,
        base_output_path,
        validate_capture_staged_outputs(job.staging_dir, staged_paths),
        initial_path=job.output_path,
        format=job.format,
        split_gcode_layers=job.split_gcode_layers,
        output_size=job.output_size,
    )
    return published.artifact_paths, published.manifest_path


def _finalize_backend_result(
    job: ExportJob,
    result: ExportJobResult,
    *,
    capture_service: CaptureService | None = None,
) -> ExportJobResult:
    """worker result を親側で commit し、staging を必ず掃除する。"""

    try:
        if result.status is not ExportJobStatus.SUCCESS:
            return result
        committed_paths, manifest_path = _commit_staged_outputs(
            job,
            result.paths,
            capture_service=capture_service,
        )
        return replace(
            result,
            paths=committed_paths,
            manifest_path=manifest_path,
        )
    except Exception:
        return ExportJobResult(
            job_id=result.job_id,
            format=result.format,
            status=ExportJobStatus.ERROR,
            output_path=result.output_path,
            split_gcode_layers=result.split_gcode_layers,
            error="parent-side export commit failed:\n" + traceback.format_exc(),
            worker_pid=result.worker_pid,
            worker_exitcode=result.worker_exitcode,
        )
    finally:
        cleanup_capture_staging(job.staging_dir)


def _execute_export_job(job: ExportJob) -> tuple[Path, ...]:
    """既定 backend として PNG/G-code job を同期実行する。"""

    work_output_path = capture_staging_work_path(job.staging_dir, job.output_path)
    gcode_params = (
        job.snapshot.gcode_params
        if job.format is ExportFormat.GCODE
        else None
    )
    return _CAPTURE_SERVICE.encode(
        job.snapshot,
        work_output_path,
        format=job.format,
        split_gcode_layers=job.split_gcode_layers,
        output_size=job.output_size,
        timeout_s=job.timeout_s,
        deadline_monotonic=job.deadline_monotonic,
        gcode_params=gcode_params,
    )


def _export_worker_main(
    task_q: mp_queues.Queue[ExportJob | None],
    result_q: mp_queues.Queue[_WorkerMessage],
    backend: ExportBackend,
) -> None:
    """長寿命 worker: job を直列実行し、必ず終端結果へ変換する。"""

    result_q.put(_WorkerReady(pid=os.getpid()))
    try:
        while True:
            job = task_q.get()
            if job is None:
                return
            try:
                paths = validate_capture_staged_outputs(
                    job.staging_dir,
                    backend(job),
                )
                result = ExportJobResult(
                    job_id=job.job_id,
                    format=job.format,
                    status=ExportJobStatus.SUCCESS,
                    output_path=job.output_path,
                    split_gcode_layers=job.split_gcode_layers,
                    paths=paths,
                )
            except TimeoutError:
                result = ExportJobResult(
                    job_id=job.job_id,
                    format=job.format,
                    status=ExportJobStatus.TIMEOUT,
                    output_path=job.output_path,
                    split_gcode_layers=job.split_gcode_layers,
                    error=traceback.format_exc(),
                )
            except Exception:
                result = ExportJobResult(
                    job_id=job.job_id,
                    format=job.format,
                    status=ExportJobStatus.ERROR,
                    output_path=job.output_path,
                    split_gcode_layers=job.split_gcode_layers,
                    error=traceback.format_exc(),
                )
            try:
                result_q.put(result)
            finally:
                # 次のtask_q.get()で待機している間に直前の巨大snapshotをworkerが
                # 保持し続けない。Queue feederが必要なのはsnapshotを含まないresultだけ。
                del result
                del job
    finally:
        for worker_queue in (task_q, result_q):
            try:
                worker_queue.close()
                worker_queue.join_thread()
            except (OSError, ValueError):
                pass


class ExportJobSystem:
    """PNG/G-code を bounded な 1 worker で非同期実行する。

    Notes
    -----
    - worker は最初の submit で spawn し、その後は job 間で再利用する。
    - worker へ渡す in-flight job は 1 件、親の pending FIFO は bounded。
    - 明示した保存操作は置換せず順番に実行し、満杯なら明示的に拒否する。
    - worker death/timeout/cancel 後は Queue ごと交換し、古い job の再実行を防ぐ。
    """

    def __init__(
        self,
        *,
        backend: ExportBackend = _execute_export_job,
        default_timeout_s: float = 30.0,
        runtime_limits: RuntimeLimits = DEFAULT_FINAL_RUNTIME_LIMITS,
        capture_service: CaptureService | None = None,
    ) -> None:
        if not isinstance(runtime_limits, RuntimeLimits):
            raise TypeError("runtime_limits は RuntimeLimits である必要があります")
        timeout_s = finite_real(
            default_timeout_s,
            name="default_timeout_s",
            minimum=0.0,
            minimum_inclusive=False,
        )

        self._ctx = mp.get_context("spawn")
        self._backend = backend
        if capture_service is not None and not isinstance(
            capture_service, CaptureService
        ):
            raise TypeError("capture_service は CaptureService である必要があります")
        self._capture_service = (
            CaptureService() if capture_service is None else capture_service
        )
        self._default_timeout_s = timeout_s
        self._max_pending_jobs = int(runtime_limits.capture_queue_pending_jobs)
        self._max_retained_bytes = int(runtime_limits.capture_queue_bytes)
        self._task_q: mp.Queue[ExportJob | None]
        self._result_q: mp.Queue[_WorkerMessage]
        self._proc: mp_process.BaseProcess | None = None
        self._worker_generation = 0
        self._ready_pid: int | None = None
        self._closed = False
        self._next_job_id = 0
        self._in_flight: ExportJob | None = None
        self._pending: deque[ExportJob] = deque()
        self._completed: deque[ExportJobResult] = deque(maxlen=_COMPLETED_RESULT_LIMIT)
        # 同じ immutable snapshot を pause 中に連続保存する場合、親 process の
        # geometry 参照は共有する。job ごとの refcount だけを増やし、親参照 +
        # Queue serialization + worker copy の保守的な推定bytesを一度だけ数える。
        self._retained_snapshots: dict[
            int, tuple[FrameExportSnapshot, int, int]
        ] = {}
        self._retained_job_ids: set[int] = set()
        self._retained_bytes = 0

        self._create_queues()

    @property
    def in_flight_job(self) -> ExportJob | None:
        """現在 worker へ渡している job を返す。"""

        return self._in_flight

    @property
    def pending_job(self) -> ExportJob | None:
        """次に実行する pending job を返す。"""

        return self._pending[0] if self._pending else None

    @property
    def pending_job_count(self) -> int:
        """親 process で待機中の job 数を返す。"""

        return len(self._pending)

    @property
    def request_count(self) -> int:
        """in-flight と pending を合わせた accepted request 件数。"""

        return (1 if self._in_flight is not None else 0) + len(self._pending)

    @property
    def request_limit(self) -> int:
        """同時に accepted として保持する最大 request 件数。"""

        return self._max_pending_jobs + 1

    @property
    def retained_bytes(self) -> int:
        """accepted snapshotのprocess横断・同時保持byte数の保守的推定。"""

        return int(self._retained_bytes)

    @property
    def max_retained_bytes(self) -> int:
        """aggregate process-wide snapshot byte 推定の上限。"""

        return int(self._max_retained_bytes)

    @property
    def queue_status(self) -> ExportQueueStatus:
        """GUI/診断用の同一 backpressure 状態を返す。"""

        return ExportQueueStatus(
            request_count=self.request_count,
            request_limit=self.request_limit,
            retained_bytes=self.retained_bytes,
            byte_limit=self.max_retained_bytes,
        )

    @property
    def can_submit(self) -> bool:
        """件数上、新しい job を受理できるなら True（byte 判定には snapshot が必要）。"""

        if self._closed:
            return False
        return self.request_count < self.request_limit

    def _incremental_snapshot_bytes(self, snapshot: FrameExportSnapshot) -> int:
        if id(snapshot) in self._retained_snapshots:
            return 0
        return _estimate_snapshot_process_bytes(snapshot)

    def _admission_error(
        self, snapshot: FrameExportSnapshot
    ) -> ExportQueueFullError | None:
        requested_bytes = self._incremental_snapshot_bytes(snapshot)
        def error(reason: str) -> ExportQueueFullError:
            return ExportQueueFullError(
                reason=reason,
                request_count=self.request_count,
                request_limit=self.request_limit,
                retained_bytes=self.retained_bytes,
                requested_bytes=requested_bytes,
                byte_limit=self.max_retained_bytes,
            )

        if self.request_count >= self.request_limit:
            return error("count")
        if self.retained_bytes + requested_bytes > self.max_retained_bytes:
            return error("bytes")
        return None

    def ensure_can_submit(self, snapshot: FrameExportSnapshot) -> None:
        """同じ submit 契約で事前検査し、拒否理由を path 予約前に返す。"""

        if self._closed:
            raise RuntimeError("ExportJobSystem は close 済みです")
        self._service()
        error = self._admission_error(snapshot)
        if error is not None:
            raise error

    def _retain_job(self, job: ExportJob) -> None:
        if job.job_id in self._retained_job_ids:
            return
        snapshot_id = id(job.snapshot)
        current = self._retained_snapshots.get(snapshot_id)
        if current is None:
            byte_size = _estimate_snapshot_process_bytes(job.snapshot)
            self._retained_snapshots[snapshot_id] = (job.snapshot, 1, byte_size)
            self._retained_bytes += byte_size
        else:
            snapshot, refcount, byte_size = current
            self._retained_snapshots[snapshot_id] = (
                snapshot,
                refcount + 1,
                byte_size,
            )
        self._retained_job_ids.add(job.job_id)

    def _release_job(self, job: ExportJob) -> None:
        if job.job_id not in self._retained_job_ids:
            return
        self._retained_job_ids.remove(job.job_id)
        snapshot_id = id(job.snapshot)
        snapshot, refcount, byte_size = self._retained_snapshots[snapshot_id]
        if refcount <= 1:
            del self._retained_snapshots[snapshot_id]
            self._retained_bytes -= byte_size
        else:
            self._retained_snapshots[snapshot_id] = (
                snapshot,
                refcount - 1,
                byte_size,
            )

    @property
    def has_work(self) -> bool:
        """worker 実行中または pending の job が残っているなら True。"""

        return self._in_flight is not None or bool(self._pending)

    def _create_queues(self) -> None:
        task_q: mp.Queue[ExportJob | None] | None = None
        try:
            task_q = self._ctx.Queue(maxsize=1)
            result_q: mp.Queue[_WorkerMessage] = self._ctx.Queue(maxsize=2)
        except BaseException:
            # 2本目のQueue構築失敗時も、1本目のfeeder/thread descriptorを残さない。
            if task_q is not None:
                try:
                    self._close_queue(task_q, cancel=True)
                except BaseException:
                    pass
            raise
        self._task_q = task_q
        self._result_q = result_q

    def _start_worker(self) -> None:
        self._worker_generation += 1
        proc = self._ctx.Process(
            target=_export_worker_main,
            args=(self._task_q, self._result_q, self._backend),
            name=f"grafix-export-{self._worker_generation}",
        )
        proc.start()
        self._proc = proc
        self._ready_pid = None

    @staticmethod
    def _join_process(proc: mp_process.BaseProcess) -> None:
        errors = CleanupErrors()

        def is_alive() -> bool:
            try:
                return bool(proc.is_alive())
            except BaseException as exc:
                errors.record(exc)
                # 生存状態を確認できない場合も、後続の terminate/kill は試す。
                return True

        errors.attempt(
            lambda: proc.join(timeout=_WORKER_JOIN_TIMEOUT_S),
        )
        if is_alive():
            errors.attempt(proc.terminate)
            errors.attempt(
                lambda: proc.join(timeout=_WORKER_JOIN_TIMEOUT_S),
            )
        if is_alive():
            errors.attempt(proc.kill)
            errors.attempt(
                lambda: proc.join(timeout=_WORKER_JOIN_TIMEOUT_S),
            )
        if not is_alive():
            errors.attempt(proc.close)
        errors.raise_if_any()

    @staticmethod
    def _close_queue(worker_queue: mp_queues.Queue[Any], *, cancel: bool) -> None:
        errors = CleanupErrors()
        if cancel:
            errors.attempt(worker_queue.cancel_join_thread)
        errors.attempt(worker_queue.close)
        errors.attempt(worker_queue.join_thread)
        errors.raise_if_any()

    def _close_queues(self, *, cancel_pending: bool) -> None:
        task_q = self._task_q
        result_q = self._result_q
        errors = CleanupErrors()
        errors.attempt(lambda: self._close_queue(task_q, cancel=cancel_pending))
        errors.attempt(lambda: self._close_queue(result_q, cancel=False))
        errors.raise_if_any()

    def _replace_worker(self) -> None:
        errors = CleanupErrors()
        proc = self._proc
        if proc is not None:
            def terminate_live_worker() -> None:
                if proc.is_alive():
                    proc.terminate()

            errors.attempt(terminate_live_worker)
            errors.attempt(lambda: self._join_process(proc))
        self._proc = None
        errors.attempt(
            lambda: self._close_queues(cancel_pending=True),
        )
        errors.attempt(lambda: self._create_queues())
        errors.raise_if_any()

    @staticmethod
    def _terminal_result(
        job: ExportJob,
        status: ExportJobStatus,
        *,
        error: str,
        worker_pid: int | None = None,
        worker_exitcode: int | None = None,
    ) -> ExportJobResult:
        return ExportJobResult(
            job_id=job.job_id,
            format=job.format,
            status=status,
            output_path=job.output_path,
            split_gcode_layers=job.split_gcode_layers,
            error=error,
            worker_pid=worker_pid,
            worker_exitcode=worker_exitcode,
        )

    def _dispatch(self, job: ExportJob) -> None:
        dispatched = replace(
            job,
            deadline_monotonic=time.monotonic() + job.timeout_s,
        )
        try:
            if self._proc is None:
                self._start_worker()
            self._task_q.put_nowait(dispatched)
        except Exception:
            cleanup_capture_staging(dispatched.staging_dir)
            self._completed.append(
                self._terminal_result(
                    job,
                    ExportJobStatus.ERROR,
                    error=traceback.format_exc(),
                )
            )
            self._release_job(job)
            return
        self._in_flight = dispatched

    def _drain_worker_messages(self) -> None:
        while True:
            try:
                message = self._result_q.get_nowait()
            except queue.Empty:
                return
            if isinstance(message, _WorkerReady):
                self._ready_pid = message.pid
                continue
            current = self._in_flight
            if current is None or message.job_id != current.job_id:
                continue
            message = _finalize_backend_result(
                current,
                message,
                capture_service=self._capture_service,
            )
            self._completed.append(message)
            self._in_flight = None
            self._release_job(current)

    def _recover_dead_worker(self) -> None:
        proc = self._proc
        if proc is None or proc.exitcode is None:
            return

        current = self._in_flight
        if current is not None:
            self._completed.append(
                self._terminal_result(
                    current,
                    ExportJobStatus.WORKER_DIED,
                    error=(
                        "export worker が予期せず終了しました: "
                        f"worker={proc.name!r}, pid={proc.pid}, exitcode={proc.exitcode}"
                    ),
                    worker_pid=proc.pid,
                    worker_exitcode=proc.exitcode,
                )
            )
            self._in_flight = None
            self._release_job(current)
        try:
            self._replace_worker()
        finally:
            if current is not None:
                cleanup_capture_staging(current.staging_dir)

    def _expire_timed_out_job(self) -> None:
        current = self._in_flight
        if current is None:
            return
        deadline = current.deadline_monotonic
        if deadline is None:
            return
        # PNG backend の resvg timeout が subprocess を確実に reap して結果を返す猶予を置く。
        # 猶予後も終わらない Python/G-code backend は worker ごと停止する。
        if time.monotonic() < deadline + _PARENT_TIMEOUT_GRACE_S:
            return

        self._completed.append(
            self._terminal_result(
                current,
                ExportJobStatus.TIMEOUT,
                error=f"export timeout: {current.timeout_s:g}s",
            )
        )
        self._in_flight = None
        self._release_job(current)
        try:
            self._replace_worker()
        finally:
            cleanup_capture_staging(current.staging_dir)

    def _service(self) -> None:
        self._drain_worker_messages()
        self._recover_dead_worker()
        self._expire_timed_out_job()
        if self._in_flight is None and self._pending:
            self._dispatch(self._pending.popleft())

    def submit(
        self,
        *,
        format: ExportFormat,
        snapshot: CaptureExportSnapshot,
        output_path: str | Path,
        base_output_path: str | Path | None = None,
        split_gcode_layers: bool = False,
        timeout_s: float | None = None,
        output_size: tuple[int, int] | None = None,
    ) -> ExportJob:
        """job を bounded FIFO へ投入する。満杯なら明示的に拒否する。"""

        if self._closed:
            raise RuntimeError("ExportJobSystem は close 済みです")
        format, snapshot, split_gcode_layers, normalized_output_size = (
            _validate_export_request(
                format=format,
                snapshot=snapshot,
                split_gcode_layers=split_gcode_layers,
                output_size=output_size,
            )
        )
        self._service()

        error = self._admission_error(snapshot)
        if error is not None:
            raise error

        output = Path(output_path)
        base_output = output if base_output_path is None else Path(base_output_path)
        next_job_id = self._next_job_id + 1
        staging = CaptureStaging.create(
            output,
            purpose=f"export-{next_job_id}",
        )
        try:
            job = ExportJob(
                job_id=next_job_id,
                format=format,
                snapshot=snapshot,
                output_path=output,
                base_output_path=base_output,
                timeout_s=self._default_timeout_s if timeout_s is None else timeout_s,
                staging_dir=staging.directory,
                split_gcode_layers=split_gcode_layers,
                output_size=normalized_output_size,
            )
        except BaseException:
            staging.close()
            raise
        self._next_job_id = next_job_id

        self._retain_job(job)

        if self._in_flight is None:
            self._dispatch(job)
            return job
        self._pending.append(job)
        return job

    def poll(self) -> list[ExportJobResult]:
        """新しい終端結果をノンブロッキングで返す。"""

        if not self._closed:
            self._service()
        results = list(self._completed)
        self._completed.clear()
        return results

    def cancel(self, job_id: int | None = None) -> bool:
        """指定 job（None なら全 job）を取消し、終端結果を poll 可能にする。"""

        target_job_id = (
            None
            if job_id is None
            else exact_integer(job_id, name="job_id", minimum=1)
        )
        if self._closed:
            return False
        self._service()
        cancelled = False

        kept: deque[ExportJob] = deque()
        for pending in self._pending:
            if target_job_id is None or pending.job_id == target_job_id:
                self._completed.append(
                    self._terminal_result(
                        pending,
                        ExportJobStatus.CANCELLED,
                        error="cancelled",
                    )
                )
                self._release_job(pending)
                cleanup_capture_staging(pending.staging_dir)
                cancelled = True
            else:
                kept.append(pending)
        self._pending = kept

        current = self._in_flight
        if current is not None and (
            target_job_id is None or current.job_id == target_job_id
        ):
            self._completed.append(
                self._terminal_result(
                    current,
                    ExportJobStatus.CANCELLED,
                    error="cancelled",
                )
            )
            self._in_flight = None
            self._release_job(current)
            cancelled = True
            try:
                self._replace_worker()
            finally:
                cleanup_capture_staging(current.staging_dir)

        if self._in_flight is None and self._pending:
            self._dispatch(self._pending.popleft())
        return cancelled

    def close(self) -> None:
        """実行中・pending job を取消し、worker/Queue を冪等に閉じる。"""

        if self._closed:
            return
        self._closed = True

        current = self._in_flight
        jobs_to_cancel = (() if current is None else (current,)) + tuple(self._pending)
        for job in jobs_to_cancel:
            if job is not None:
                self._completed.append(
                    self._terminal_result(
                        job,
                        ExportJobStatus.CANCELLED,
                        error="ExportJobSystem.close()",
                    )
                )
                self._release_job(job)
        had_in_flight = self._in_flight is not None
        self._in_flight = None
        self._pending.clear()

        errors = CleanupErrors()
        proc = self._proc
        if proc is not None and not had_in_flight:
            try:
                if proc.is_alive():
                    self._task_q.put_nowait(None)
            except queue.Full:
                had_in_flight = True
            except BaseException as exc:
                errors.record(exc)
                had_in_flight = True
        if proc is not None:
            errors.attempt(lambda: self._join_process(proc))
        self._proc = None
        try:
            errors.attempt(lambda: self._close_queues(cancel_pending=had_in_flight))
        finally:
            for job in jobs_to_cancel:
                cleanup_capture_staging(job.staging_dir)

        errors.raise_if_any()


__all__ = [
    "CaptureExportSnapshot",
    "ExportJob",
    "ExportJobResult",
    "ExportJobStatus",
    "ExportJobSystem",
    "ExportQueueStatus",
    "ExportQueueFullError",
    "FrameExportSnapshot",
    "estimate_snapshot_retained_bytes",
]
