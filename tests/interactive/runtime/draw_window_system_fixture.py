"""DrawWindowSystem を実初期化する headless test factory。"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from grafix.api.render import RenderOptions
from grafix.core.capture_manifest import RecordingManifest
from grafix.core.parameters import ParamStore
from grafix.core.pipeline import RealizedLayer
from grafix.runtime_config_loader import runtime_config
from grafix.core.runtime_limits import RuntimeLimits
from grafix.core.scene import SceneItem
from grafix.interactive.gl.index_buffer import LineIndexStats
from grafix.interactive.runtime import capture_queue as capture_queue_module
from grafix.interactive.runtime import draw_window_system as draw_window_module
from grafix.interactive.runtime import recording_session as recording_session_module
from grafix.interactive.runtime.draw_window_system import DrawWindowSystem
from grafix.interactive.runtime.export_job_system import (
    ExportJobResult,
    ExportQueueStatus,
    FrameExportSnapshot,
)
from grafix.interactive.runtime.monitor import RuntimeMonitor
from grafix.interactive.runtime.recording_system import StagedVideoCapture


def _draw(_t: float) -> SceneItem:
    return []


class FakeWindow:
    """DrawWindowSystem が所有する draw window の必須 contract。"""

    def __init__(self, *, width: int = 800, height: int = 800) -> None:
        self.width = int(width)
        self.height = int(height)
        self.visible = True
        self.handlers: dict[str, object] = {}

    def push_handlers(self, **handlers: object) -> None:
        self.handlers.update(handlers)

    def switch_to(self) -> None:
        return None

    def close(self) -> None:
        return None

    def clear(self) -> None:
        return None

    def get_size(self) -> tuple[int, int]:
        return self.width, self.height

    def get_framebuffer_size(self) -> tuple[int, int]:
        return self.width, self.height

    def set_minimum_size(self, _width: int, _height: int) -> None:
        return None

    def set_maximum_size(self, _width: int, _height: int) -> None:
        return None


class FakeRenderer:
    """DrawRenderer の必須描画/lifecycle contract。"""

    def __init__(self) -> None:
        self.mesh_upload_count = 0

    def apply_runtime_limits(self, _limits: RuntimeLimits) -> None:
        return None

    def begin_frame(
        self,
        _width: int,
        _height: int,
        *,
        background_color: tuple[float, float, float],
    ) -> None:
        del background_color
        return None

    def read_frame_rgb24(self, _width: int, _height: int) -> bytes:
        return b""

    def render_layer(self, *_args: object, **_kwargs: object) -> LineIndexStats:
        return LineIndexStats(draw_vertices=0, draw_lines=0)

    def finish_dynamic_frame(self, _slot_count: int) -> None:
        return None

    def finish(self) -> None:
        return None

    def release(self) -> None:
        return None


class FakeExportJobs:
    """ExportJobSystem の必須 admission/lifecycle contract。"""

    def __init__(self) -> None:
        self._next_job_id = 1
        self.has_work = False

    def ensure_can_submit(self, _snapshot: FrameExportSnapshot) -> None:
        return None

    @property
    def queue_status(self) -> ExportQueueStatus:
        return ExportQueueStatus(
            request_count=0,
            request_limit=1,
            retained_bytes=0,
            byte_limit=1,
        )

    def submit(self, **_kwargs: object) -> Any:
        job = SimpleNamespace(job_id=self._next_job_id)
        self._next_job_id += 1
        return job

    def poll(self) -> list[ExportJobResult]:
        return []

    def cancel(self, _job_id: int | None = None) -> bool:
        return False

    def close(self) -> None:
        return None


class FakeRecording:
    """VideoRecordingSystem の必須 capture/lifecycle contract。"""

    def __init__(self) -> None:
        self.is_recording = False
        self._t = 0.0
        self._path: Path | None = None

    def start(
        self,
        *,
        output_path: Path,
        framebuffer_size: tuple[int, int],
        t0: float,
        **_kwargs: object,
    ) -> None:
        del framebuffer_size
        self._path = Path(output_path)
        self._t = float(t0)
        self.is_recording = True

    def t(self) -> float:
        return float(self._t)

    def write_frame(self, _frame_rgb24: bytes) -> None:
        return None

    def pause_frame(self, _message: str) -> None:
        return None

    def stop(
        self,
        *,
        timeout_s: float,
        stop_reason: str,
        abort_reason: str | None,
    ) -> StagedVideoCapture | None:
        del timeout_s
        self.is_recording = False
        output_path = self._path
        if output_path is None:
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        staging_path = output_path.with_name(f".{output_path.name}.recording")
        staging_path.write_bytes(b"")
        return StagedVideoCapture(
            staging_path=staging_path,
            output_path=output_path,
            framebuffer_size=(800, 800),
            recording=RecordingManifest(
                fps=60.0,
                frame_count=0,
                stop_reason=stop_reason,
                abort_reason=abort_reason,
            ),
        )


class FakeSceneRunner:
    """SceneRunner の必須 evaluation/lifecycle contract。"""

    def __init__(self) -> None:
        self.last_evaluation_succeeded: bool | None = None
        self.last_evaluation_t: float | None = None
        self.last_realized_t: float | None = None
        self.last_realized_snapshot_revision: int | None = None
        self.last_realized_frame_id: int | None = None
        self.last_output_updated = False
        self.is_waiting_for_fresh_result = False

    def run(self, *args: object, **kwargs: object) -> list[RealizedLayer]:
        t = float(cast(Any, args[0]))
        store = kwargs["store"]
        assert isinstance(store, ParamStore)
        self.last_evaluation_succeeded = True
        self.last_evaluation_t = t
        self.last_realized_t = t
        self.last_realized_snapshot_revision = int(store.revision)
        self.last_realized_frame_id = None
        self.last_output_updated = True
        self.is_waiting_for_fresh_result = False
        return []

    def replace_draw(
        self,
        _draw_callback: object,
        *,
        definitions: object | None = None,
    ) -> None:
        del definitions
        return None

    def close(self) -> None:
        return None


def make_draw_window_system(
    *,
    store: ParamStore | None = None,
    monitor: RuntimeMonitor | None = None,
) -> DrawWindowSystem:
    """外部 resource constructorだけを fake にし、実 ``__init__`` を通す。"""

    target_store = ParamStore() if store is None else store
    window = FakeWindow()
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                draw_window_module,
                "create_draw_window",
                return_value=window,
            )
        )
        stack.enter_context(
            patch.object(
                draw_window_module,
                "DrawRenderer",
                side_effect=lambda *_args, **_kwargs: FakeRenderer(),
            )
        )
        stack.enter_context(
            patch.object(
                capture_queue_module,
                "ExportJobSystem",
                side_effect=lambda *_args, **_kwargs: FakeExportJobs(),
            )
        )
        stack.enter_context(
            patch.object(
                recording_session_module,
                "VideoRecordingSystem",
                side_effect=lambda *_args, **_kwargs: FakeRecording(),
            )
        )
        stack.enter_context(
            patch.object(
                draw_window_module,
                "SceneRunner",
                side_effect=lambda *_args, **_kwargs: FakeSceneRunner(),
            )
        )
        return DrawWindowSystem(
            _draw,
            options=RenderOptions(),
            render_scale=1.0,
            store=target_store,
            monitor=monitor,
            effective_config=runtime_config(),
            parameter_load_provenance=lambda: "primary",
        )
