# どこで: `src/grafix/interactive/runtime/draw_window_system.py`。
# 何を: `draw(t)` が返すシーンを描画ウィンドウへ描画するサブシステムを提供する。
# なぜ: `src/grafix/api/runner.py` の `run()` を「配線」に寄せ、描画責務を独立させるため。

"""
描画ウィンドウ（pyglet + ModernGL）に対して、1 フレームの「入力 → scene 実行 → GL 描画 →
書き出し/録画」を束ねるサブシステム。

このモジュールは window / GL / scene のフレーム順を組み立てる。表示済み frame と
provenance の寿命は `PresentedFrameState`、書き出しの FIFO、backpressure、worker
lifecycle、通知は `CaptureQueue` へ委譲する。

読む順番（主要な入口）
----------------------
1. `DrawWindowSystem.__init__()` : window/renderer と各種サブシステムの組み立て
2. `DrawWindowSystem.draw_frame()` : 1 フレーム分の処理（※ flip は呼ばない）
3. `DrawWindowSystem.close()` : teardown（GL コンテキストが生きているうちに release する）

副作用の一覧（把握しておくと読みやすい）
--------------------------------------
- ウィンドウ生成: `create_draw_window()`（pyglet）
- GPU 描画: `DrawRenderer`（ModernGL）
- ファイル書き出し: `CaptureQueue` / 録画 subsystem へ frame を渡す
- 別プロセス: `CaptureQueue` が PNG/G-code worker を所有する
"""

from __future__ import annotations

import logging
from math import isfinite
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pyglet.window import key

from grafix.core.export_format import ExportFormat
from grafix.core.lifecycle import CleanupErrors
from grafix.core.parameters import (
    LoadProvenance,
    ParamStore,
    begin_effect_chain_generation,
)
from grafix.core.layer import LayerStyleDefaults
from grafix.core.pipeline import RealizedLayer
from grafix.export.output_paths import output_path_for_draw
from grafix.core.runtime_config import RuntimeConfig, bind_runtime_config
from grafix.core.render_options import RenderOptions
from grafix.export.capture import CaptureService
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.export.image import default_png_output_path
from grafix.interactive.draw_window import create_draw_window
from grafix.interactive.gl.draw_renderer import DrawRenderer
from grafix.interactive.pyglet_window_lifecycle import (
    activate_pyglet_window_context,
    close_pyglet_window,
)
from grafix.core.scene import SceneItem
from grafix.core.runtime_limits import (
    DEFAULT_RUNTIME_LIMIT_PROFILES,
    RuntimeLimitProfiles,
)
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.midi import MidiSession
from grafix.interactive.transport import TransportClock
from grafix.interactive.diagnostics import (
    DiagnosticAction,
    DiagnosticEvent,
)
from grafix.interactive.runtime.capture_queue import (
    CaptureQueue,
    DEFAULT_CAPTURE_SHUTDOWN_TIMEOUT_S,
)
from grafix.interactive.runtime.export_job_system import CaptureExportSnapshot
from grafix.interactive.runtime.presented_frame import PresentedFrameState
from grafix.interactive.runtime.recording_session import RecordingSession
from grafix.interactive.runtime.scene_runner import SceneRunner
from grafix.core.parameters.style_resolver import StyleResolver
from grafix.core.parameters.source import MidiFrameSnapshot, ParameterLoadMode
from grafix.core.preview_quality import PreviewQuality
from grafix.interactive.runtime.video_recorder import default_video_output_path

_logger = logging.getLogger(__name__)
_MAX_CAPTURE_PUBLISH_RETRIES = 8

if TYPE_CHECKING:
    from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
    from grafix.interactive.runtime.monitor import RuntimeMonitor
    from grafix.interactive.runtime.source_reload import SourceReloadController


class DrawWindowSystem:
    """描画（メインウィンドウ）のサブシステム。

    `draw(t)`（ユーザーのコールバック）を `SceneRunner` 経由で評価し、
    得られた `RealizedLayer` 群を `DrawRenderer` で描画する。

    フレーム順として配線するもの
    ----------------------------
    - キー入力による書き出し:
      - `S`: SVG 保存（同期）
      - `P`: PNG 保存（非同期: 共通 export worker）
      - `G`: G-code 保存（非同期: 共通 export worker）
      - `Shift+G`: G-code をレイヤ別に保存（非同期: 共通 export worker）
      - `V`: 動画録画の開始/停止
      - `Space` / `Home` / `Left` / `Right`: 再生、一時停止、reset、frame step
      - `[` / `]`: 再生速度の変更
    - MIDI: 毎フレーム CC を取り込み（未接続時は frozen snapshot を使う）
    - style: ParamStore から背景色/線幅/線色を解決して反映する
    """

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        options: RenderOptions,
        render_scale: float,
        store: ParamStore,
        effective_config: RuntimeConfig,
        parameter_load_provenance: Callable[[], LoadProvenance],
        midi_session: MidiSession | None = None,
        monitor: RuntimeMonitor | None = None,
        fps: float = 60.0,
        n_worker: int = 0,
        evaluation_timeout: float | None = 5.0,
        run_id: str | None = None,
        runtime_limit_profiles: RuntimeLimitProfiles = DEFAULT_RUNTIME_LIMIT_PROFILES,
        source_reload: SourceReloadController | None = None,
        definitions: AuthoringDefinitionsSnapshot | None = None,
        parameter_source: ParameterLoadMode = "code",
        parameter_store_path: Path | None = None,
        seed: int | None = None,
    ) -> None:
        """描画用の window/renderer と各種状態を初期化する。

        初期化で行うこと
        --------------
        - pyglet window 作成 + `DrawRenderer` の初期化（GL コンテキストに紐づく）
        - capture の基準 path と `CaptureQueue` の配線
        - 録画 subsystem の用意
        - `draw(t)` に渡す `t` の基準となる clock の開始
        """

        if not isinstance(runtime_limit_profiles, RuntimeLimitProfiles):
            raise TypeError("runtime_limit_profiles は RuntimeLimitProfiles である必要があります")
        profiles = runtime_limit_profiles
        if not isinstance(options, RenderOptions):
            raise TypeError("options は RenderOptions である必要があります")
        if not isinstance(render_scale, float):
            raise TypeError("render_scale は float である必要があります")
        if not isfinite(render_scale) or render_scale <= 0.0:
            raise ValueError("render_scale は正の有限値である必要があります")
        if type(fps) is not float:
            raise TypeError("fps は canonical float である必要があります")
        if not isfinite(fps):
            raise ValueError("fps は有限値である必要があります")
        frame_rate = fps

        # 描画設定/draw 関数/ParamStore は 1 フレームごとに参照するため保持しておく。
        self._options = options
        self._store = store
        self._midi_session = midi_session
        self._monitor = monitor
        self._source_reload = source_reload
        self._runtime_limit_profiles = profiles
        self._fps = frame_rate
        if not isinstance(effective_config, RuntimeConfig):
            raise TypeError("effective_config は RuntimeConfig である必要があります")
        if not callable(parameter_load_provenance):
            raise TypeError("parameter_load_provenance は callable である必要があります")
        self._effective_config = effective_config
        # Keep/Discard 後も capture 時点の session state を使えるよう、値ではなく
        # owner である ParameterSession への provider を保持する。
        self._parameter_load_provenance = parameter_load_provenance
        self._parameter_source = parameter_source
        self._parameter_store_path = (
            None if parameter_store_path is None else Path(parameter_store_path)
        )
        self._seed = seed
        self._presented_frame = PresentedFrameState(
            store=store,
            provenance_builder=CaptureProvenanceBuilder(
                draw,
                config=self._effective_config,
                parameter_source=self._parameter_source,
                parameter_store_path=self._parameter_store_path,
                parameter_load_provenance=self._parameter_load_provenance,
                seed=seed,
            ),
        )
        self._style = StyleResolver(
            self._store,
            base_background_color_rgb01=options.background_color.rgb01,
            base_global_thickness=options.line_thickness,
            base_global_line_color_rgb01=options.line_color.rgb01,
        )

        self._closed = False
        window = None
        renderer = None
        perf = None
        recording_session = None
        capture_queue = None
        scene_runner = None
        try:
            # 描画用の pyglet window と、その OpenGL context に紐づく renderer。
            window = create_draw_window(options, render_scale=render_scale)
            self.window = window
            diagnostic_center = None if monitor is None else monitor.diagnostic_center
            renderer = DrawRenderer(
                window,
                options,
                runtime_limits=profiles.preview,
                diagnostic_center=diagnostic_center,
            )
            self._renderer = renderer

            self._svg_output_path = output_path_for_draw(
                kind="svg",
                ext="svg",
                draw=draw,
                run_id=run_id,
                canvas_size=options.canvas_size,
                config=self._effective_config,
            )
            self._gcode_output_path = output_path_for_draw(
                kind="gcode",
                ext="gcode",
                draw=draw,
                run_id=run_id,
                canvas_size=options.canvas_size,
                config=self._effective_config,
            )
            self._png_output_path = default_png_output_path(
                draw,
                scale=self._effective_config.png_scale,
                run_id=run_id,
                canvas_size=options.canvas_size,
                config=self._effective_config,
            )
            video_output_path = default_video_output_path(
                draw,
                run_id=run_id,
                ext="mp4",
                config=self._effective_config,
            )
            self._capture_service = CaptureService(max_publish_retries=_MAX_CAPTURE_PUBLISH_RETRIES)

            # preview transport と録画 session は同じ timeline 境界を共有する。
            start_time = time.perf_counter()
            self._clock = TransportClock(start_time=start_time)
            perf = PerfCollector.from_env(
                enabled_by_default=monitor is not None,
                snapshot_callback=(None if monitor is None else monitor.set_profiler),
                defer_frame_finalize=True,
            )
            self._perf = perf
            recording_session = RecordingSession(
                fps=frame_rate,
                capture_service=self._capture_service,
                output_path=video_output_path,
                canvas_size=options.canvas_size,
                transport=self._clock,
                window=window,
                provenance_for_t=lambda t: self._presented_frame.frame_provenance(
                    t=float(t),
                    quality="final",
                ),
                frame_section=lambda: perf.section("video"),
            )
            self._recording_session = recording_session
            self._last_perf_store_revision = int(store.revision)
            self._last_frame_error: str | None = None
            capture_queue = CaptureQueue(
                capture_service=self._capture_service,
                runtime_limits=profiles.final,
                svg_output_path=self._svg_output_path,
                png_output_path=self._png_output_path,
                gcode_output_path=self._gcode_output_path,
                png_scale=self._effective_config.png_scale,
                current_snapshot=lambda: self._presented_frame.export_snapshot,
                capture_current_frame=self.final_capture_frame,
                materialize_snapshot=self._presented_frame.materialize_capture_snapshot,
                shutdown_snapshot=self._shutdown_export_snapshot,
                monitor=monitor,
            )
            self._capture_queue = capture_queue
            window.push_handlers(on_key_press=self._on_key_press)
            scene_runner = SceneRunner(
                draw,
                perf=self._perf,
                n_worker=n_worker,
                evaluation_timeout=evaluation_timeout,
                runtime_limit_profiles=profiles,
                effective_config=self._effective_config,
                definitions=definitions,
                diagnostic_center=(None if monitor is None else monitor.diagnostic_center),
            )
            self._scene_runner = scene_runner
            if source_reload is not None and diagnostic_center is not None:
                diagnostic_center.register_action(
                    "retry",
                    self._retry_source_reload,
                    category="reload",
                )
        except BaseException as error:
            # constructor が return しない場合、runner は部分構築 object を close
            # できない。ここで取得済み resource を全て逆順に試す。CaptureService と
            # TransportClock は close を持たない値である。MIDI ownership は正常構築後
            # にだけ移るため、失敗時は caller が close する。
            cleanup_steps: list[tuple[str, Callable[[], object]]] = []
            if scene_runner is not None:
                cleanup_steps.append(("scene runner", scene_runner.close))
            if capture_queue is not None:
                cleanup_steps.append(
                    (
                        "capture queue",
                        lambda: capture_queue.close(timeout_s=0.0),
                    )
                )
            if recording_session is not None:
                cleanup_steps.append(
                    (
                        "recording session",
                        lambda: recording_session.close(
                            timeout_s=0.0,
                            stop_reason="shutdown",
                        ),
                    )
                )
            if perf is not None:
                cleanup_steps.append(("performance trace", perf.close))
            if renderer is not None and window is not None:

                def release_renderer() -> None:
                    if activate_pyglet_window_context(window):
                        renderer.release()

                cleanup_steps.append(("renderer", release_renderer))
            if window is not None:
                cleanup_steps.append(
                    ("draw window", lambda: close_pyglet_window(window))
                )
            errors = CleanupErrors(
                initial_error=error,
                report_secondary=lambda label: _logger.exception(
                    "DrawWindowSystem initialization cleanup failed: %s",
                    label,
                ),
            )
            for label, cleanup in cleanup_steps:
                errors.attempt(cleanup, label)
            errors.raise_if_any()
            raise

    def _on_key_press(self, symbol: int, modifiers: int) -> None:
        """キーボードショートカットのハンドラ。

        重い処理（PNG/G-code の書き出し）は、イベントコールバック内で実行せず
        フラグを立てて `draw_frame()` 側で処理する（イベント処理を詰まらせないため）。
        """

        if symbol == key.S:
            # 初回 draw 前だけは空 scene を即保存せず、PNG/G-code と同じ bounded
            # intent queue で最初に実際に表示できた frame を待つ。初回 draw 後は
            # keypress 時点の snapshot を使い、この UI thread 内で同期保存する。
            self._capture_queue.request(ExportFormat.SVG)
            return
        if symbol == key.P:
            self._capture_queue.request(ExportFormat.PNG)
            return
        if symbol == key.G:
            # `G`: 全レイヤ一括
            # `Shift+G`: レイヤごとに分割して保存
            self._capture_queue.request(
                ExportFormat.GCODE,
                split_gcode_layers=bool(int(modifiers) & int(key.MOD_SHIFT)),
            )
            return
        if symbol == key.V:
            if not self._recording_session.is_recording:
                self.start_video_recording()
            else:
                self.stop_video_recording()
            return
        if self._recording_session.is_recording:
            # 録画 timeline は fixed-fps で確定するため、途中の preview 操作は受け付けない。
            return
        if symbol == key.SPACE:
            self._clock.toggle()
            return
        if symbol == key.HOME:
            self._clock.reset()
            return
        if symbol == key.LEFT:
            self._clock.step_frame(fps=self._transport_step_fps(), frames=-1)
            return
        if symbol == key.RIGHT:
            self._clock.step_frame(fps=self._transport_step_fps(), frames=1)
            return
        if symbol == key.BRACKETLEFT:
            self._clock.set_speed(max(0.125, self._clock.speed / 2.0))
            return
        if symbol == key.BRACKETRIGHT:
            self._clock.set_speed(min(8.0, self._clock.speed * 2.0))

    def _transport_step_fps(self) -> float:
        """1 frame step に使う fps を返す。無制限実行時は 60 fps とする。"""

        return self._fps if self._fps > 0.0 else 60.0

    @property
    def transport(self) -> TransportClock:
        """Parameter GUI と共有する preview transport を返す。"""

        return self._clock

    @property
    def is_recording(self) -> bool:
        """動画録画中なら True を返す。"""

        return self._recording_session.is_recording

    @property
    def capture_service(self) -> CaptureService:
        """Inspector callback と keyboard capture が共有する service を返す。"""

        return self._capture_service

    @property
    def authoring_definitions(self) -> AuthoringDefinitionsSnapshot:
        """現在採用中の immutable authoring generation を返す。"""

        return self._scene_runner.definitions

    def _new_provenance_builder(
        self,
        draw: Callable[[float], SceneItem],
    ) -> CaptureProvenanceBuilder:
        """同じ interactive session 設定で新しい source 世代を固定する。"""

        return CaptureProvenanceBuilder(
            draw,
            config=self._effective_config,
            parameter_source=self._parameter_source,
            parameter_store_path=self._parameter_store_path,
            parameter_load_provenance=self._parameter_load_provenance,
            seed=self._seed,
        )

    def start_video_recording(self) -> None:
        """録画 session の開始を frame composition から要求する。"""

        self._recording_session.start()

    def stop_video_recording(
        self,
        *,
        timeout_s: float = DEFAULT_CAPTURE_SHUTDOWN_TIMEOUT_S,
        stop_reason: str = "user_stop",
        abort_reason: str | None = None,
    ) -> None:
        """録画 session の completed staging を確定して停止する。"""

        self._recording_session.stop(
            timeout_s=timeout_s,
            stop_reason=stop_reason,
            abort_reason=abort_reason,
        )

    def _framebuffer_size(self) -> tuple[int, int]:
        """現在の framebuffer の実ピクセル寸法を返す。

        環境によっては（HiDPI など）`window.width/height` と framebuffer 実寸が一致しないため、
        可能なら pyglet の `get_framebuffer_size()` を使う。
        """

        width, height = self.window.get_framebuffer_size()
        return int(width), int(height)

    @staticmethod
    def _frame_error_summary(exc: Exception, *, max_chars: int = 180) -> str:
        """GUI に出す user-frame error を 1 行へ短縮する。"""

        # RealizeError のような境界 error は、ユーザーが直せる詳細を __cause__ に持つ。
        # 最深の Exception を選び、ResourceLimitError の推奨値などを GUI に残す。
        detail_error = exc
        seen: set[int] = set()
        while isinstance(detail_error.__cause__, Exception):
            if id(detail_error) in seen:
                break
            seen.add(id(detail_error))
            detail_error = detail_error.__cause__

        lines = [line.strip() for line in str(detail_error).splitlines() if line.strip()]
        detail = lines[-1] if lines else "(no message)"
        # mp-draw の RuntimeError は末尾に元例外の `ValueError: ...` などを含む。
        # その場合は wrapper 名を重ねず、ユーザーに近い原因を表示する。
        head = detail.partition(":")[0]
        if not head.endswith(("Error", "Exception")):
            detail = f"{type(detail_error).__name__}: {detail}"
        detail = " ".join(detail.split())
        limit = max(16, int(max_chars))
        if len(detail) > limit:
            return f"{detail[: limit - 1]}…"
        return detail

    def _report_frame_error(self, exc: Exception) -> None:
        """scene 評価失敗を通知し、同じ失敗の traceback spam を避ける。"""

        summary = self._frame_error_summary(exc)
        # 同じ失敗が毎 frame 続いても console を埋めない。正常 frame を挟んだ場合は
        # _clear_frame_error() により次回の失敗を改めて記録する。
        if self._last_frame_error != summary:
            _logger.exception("Scene evaluation failed; rendering the last successful frame")
        self._last_frame_error = summary
        monitor = self._monitor
        if monitor is not None:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            source: str | None = None
            tb = exc.__traceback__
            while tb is not None:
                source = f"{tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}"
                tb = tb.tb_next
            monitor.set_frame_error(summary, details=details, source=source)

    def _clear_frame_error(self) -> None:
        """新しい scene 評価が成功したときだけ error 状態を解除する。"""

        if self._last_frame_error is None:
            return
        self._last_frame_error = None
        monitor = self._monitor
        if monitor is not None:
            monitor.set_frame_error(None)

    def _publish_reload_failure(
        self,
        *,
        summary: str,
        details: str,
        source: str | None,
    ) -> None:
        """reload failureを共通診断へ出し、GUI無しならlogへ残す。"""

        monitor = self._monitor
        if monitor is None:
            _logger.error("Sketch reload failed: %s\n%s", summary, details)
            return
        actions: list[DiagnosticAction] = [
            DiagnosticAction("retry", "Retry reload"),
            DiagnosticAction("copy", "Copy details"),
        ]
        if source is not None:
            actions.append(DiagnosticAction("open", "Open source"))
        monitor.publish_diagnostic(
            DiagnosticEvent(
                category="reload",
                severity="error",
                summary=f"Sketch reload failed: {summary}",
                details=details,
                source=source,
                actions=tuple(actions),
                dedupe_key=f"source-reload:{summary}:{source}",
            )
        )

    def _poll_source_reload(self, *, force: bool = False) -> bool:
        """source更新を検査し、registry/draw/workerを同じframe境界で交換する。"""

        controller = self._source_reload
        if controller is None:
            return False
        with bind_runtime_config(self._effective_config):
            result = controller.poll(force=force, retain_rollback=True)
        if result.status == "unchanged":
            return False
        if result.status == "failed":
            self._publish_reload_failure(
                summary=result.summary or "unknown reload error",
                details=result.details or result.summary or "unknown reload error",
                source=result.source,
            )
            return False

        try:
            replacement_provenance = self._new_provenance_builder(result.draw)
            self._scene_runner.replace_draw(
                result.draw,
                definitions=result.definitions,
            )
        except Exception as exc:
            rollback_details = ""
            try:
                controller.rollback_generation(result.generation)
            except Exception as rollback_error:
                rollback_details = "\n\nRegistry rollback also failed:\n" + "".join(
                    traceback.format_exception(
                        type(rollback_error),
                        rollback_error,
                        rollback_error.__traceback__,
                    )
                )
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self._publish_reload_failure(
                summary=f"{type(exc).__name__}: {exc}",
                details=details + rollback_details,
                source=result.source,
            )
            return False

        controller.accept_generation(result.generation)
        # 次の「実際に成功した」evaluationだけを新source世代のcanonical
        # effect topologyとする。MP result待ちや失敗frameでは確定しない。
        begin_effect_chain_generation(self._store)
        self._presented_frame.replace_provenance_builder(replacement_provenance)
        monitor = self._monitor
        if monitor is not None:
            monitor.diagnostic_center.clear(category="reload")
        return True

    def _retry_source_reload(self, event: DiagnosticEvent) -> None:
        """DiagnosticCenterのRetry actionから同じsourceを明示再評価する。"""

        if self._poll_source_reload(force=True):
            monitor = self._monitor
            if monitor is not None:
                monitor.diagnostic_center.dismiss(event)

    def _evaluate_scene(
        self,
        t: float,
        *,
        cc_snapshot: MidiFrameSnapshot | None,
        defaults: LayerStyleDefaults,
        recording: bool,
        quality: PreviewQuality,
    ) -> tuple[RealizedLayer, ...]:
        """user scene を評価し、失敗時は直近成功 frame を返す。

        例外境界は意図的に `SceneRunner.run()` だけを囲む。GL context 操作や
        `render_layer()`、録画、export の障害はここで user-code error と誤認して
        握りつぶさず、呼び出し側へ伝播させる。
        """

        try:
            realized_layers = self._scene_runner.run(
                t,
                store=self._store,
                cc_snapshot=cc_snapshot,
                defaults=defaults,
                recording=recording,
                quality=quality,
                transport_epoch=int(self._clock.epoch),
            )
        except Exception as exc:
            self._report_frame_error(exc)
            return self._presented_frame.layers

        # manifest/export の `t` は、現在の transport 時刻ではなく、
        # この run で実際に realize された出力と結び付ける。
        # mp-draw で success と後続 error が同時に drain された場合は、
        # error 表示を残したまま、次の run が実際に返した success の
        # `t` だけを取り込む。
        realized_t = self._scene_runner.last_realized_t
        presented_layers = self._presented_frame.accept_evaluation(
            realized_layers,
            realized_t=realized_t,
        )
        # mp-draw の result 未到着時は前回 scene の再利用で正常 return する。
        # それを user draw の回復とは数えず、新しい成功結果まで表示を残す。
        if self._scene_runner.last_evaluation_succeeded is True:
            if realized_t is None:
                raise RuntimeError("successful scene evaluation did not publish last_realized_t")
            self._clear_frame_error()
        return presented_layers

    def final_capture_frame(self) -> CaptureExportSnapshot:
        """現在時刻をfinal品質で再評価し、artifact用snapshotを返す。"""

        recording = self._recording_session.is_recording
        t = self._recording_session.frame_time()
        style = self._style.resolve()
        layers = self._evaluate_scene(
            t,
            cc_snapshot=self._midi_frame_snapshot(),
            defaults=LayerStyleDefaults(
                color=style.global_line_color_rgb01,
                thickness=style.global_thickness,
            ),
            recording=recording,
            quality="final",
        )
        if self._scene_runner.last_evaluation_succeeded is not True:
            raise RuntimeError("Final capture evaluation did not produce a fresh frame")
        return CaptureExportSnapshot(
            layers=tuple(layers),
            canvas_size=self._options.canvas_size,
            background_color_rgb01=style.bg_color_rgb01,
            t=float(self._presented_frame.t),
            provenance=self._presented_frame.frame_provenance(
                t=float(self._presented_frame.t),
                quality="final",
            ),
            gcode_params=self._effective_config.gcode,
        )

    def _midi_frame_snapshot(self) -> MidiFrameSnapshot | None:
        """現在frameのMIDI値とlive/frozen由来を一つのsnapshotへ固定する。"""

        session = self._midi_session
        return None if session is None else session.frame_snapshot()

    def record_window_present(self, name: str, elapsed_ns: int) -> None:
        """pyglet ``Window.draw()`` 完了後の draw+flip 時間を記録する。"""

        perf = self._perf
        perf.record_duration(str(name), int(elapsed_ns))
        if str(name) == "preview_draw_flip":
            perf.record_event(
                "preview_presented",
                frame_id=self._presented_frame.frame_id,
                revision=self._presented_frame.snapshot_revision,
            )
            perf.record_event(
                "preview_style_presented",
                frame_id=self._presented_frame.frame_id,
                revision=int(self._store.revision),
            )

    def record_parameter_revision_created(
        self,
        revision: int,
        timestamp_ns: int,
        domain: str,
    ) -> None:
        """Inspector edit の起点を次の preview frame より前に記録する。"""

        normalized_revision = int(revision)
        normalized_domain = str(domain)
        if normalized_domain not in {"geometry", "style"}:
            raise ValueError(f"unknown parameter revision domain: {domain!r}")
        self._perf.record_event(
            (
                "parameter_style_revision_created"
                if normalized_domain == "style"
                else "parameter_revision_created"
            ),
            revision=normalized_revision,
            timestamp_ns=int(timestamp_ns),
        )
        self._last_perf_store_revision = normalized_revision

    def record_full_loop(self, elapsed_ns: int) -> None:
        """preview/Inspector を含む 1 multi-window tick を記録する。"""

        perf = self._perf
        perf.record_duration("full_loop", int(elapsed_ns))
        perf.finish_frame(deadline_elapsed_ns=int(elapsed_ns))

    def record_scheduler_jitter(self, elapsed_ns: int) -> None:
        """pyglet scheduler の目標 tick 間隔からの絶対ずれを記録する。"""

        self._perf.record_duration("scheduler_jitter", int(elapsed_ns))

    def draw_frame(self) -> None:
        """1 フレーム分の描画を行う（`flip()` は呼ばない）。"""

        perf = self._perf
        with perf.frame():
            current_store_revision = int(self._store.revision)
            if current_store_revision != int(self._last_perf_store_revision):
                perf.record_event(
                    "store_revision_changed",
                    revision=current_store_revision,
                )
                self._last_perf_store_revision = current_store_revision
            # --- 0) フレーム冒頭での軽い housekeeping ---
            # source watchはstatだけを毎frame確認し、変更時だけtransactional loadする。
            # recording generation 内では code provenance を一つに保つため、停止後の
            # 最初の frame 境界まで swap を遅延する。
            if not self._recording_session.is_recording:
                self._poll_source_reload()
            # 非同期 PNG/G-code export の通知回収だけを行う。重い backend は worker 内で走る。
            self._capture_queue.poll()

            cc_snapshot = self._midi_frame_snapshot()

            # 注: 呼び出し側（pyglet.window.Window.draw）が事前に self.window.switch_to() 済みである前提。
            # その前提が崩れると、別 window のコンテキストへ描いてしまう可能性がある。
            #
            # --- 1) framebuffer と Style の確定 ---
            fb_w, fb_h = self._framebuffer_size()
            style = self._style.resolve()
            self._renderer.begin_frame(
                fb_w,
                fb_h,
                background_color=style.bg_color_rgb01,
            )

            # --- 2) 時刻 t の算出 ---
            #
            # draw(t) は “開始時刻からの経過秒” を受け取る。
            # これを使ってユーザー側でアニメーション等を表現できる。
            recording = self._recording_session.is_recording
            t = self._recording_session.frame_time()
            monitor = self._monitor

            # --- 3) Geometry の param 解決 + 描画 ---
            #
            effective_defaults = LayerStyleDefaults(
                color=style.global_line_color_rgb01,
                thickness=style.global_thickness,
            )
            quality: PreviewQuality = (
                "final" if recording or self._capture_queue.has_unbound_intents else "draft"
            )
            profiles = self._runtime_limit_profiles
            self._renderer.apply_runtime_limits(profiles.for_quality(quality))
            self._evaluate_scene(
                t,
                cc_snapshot=cc_snapshot,
                defaults=effective_defaults,
                recording=recording,
                quality=quality,
            )
            perf.record_event(
                "scene_ready",
                frame_id=self._scene_runner.last_realized_frame_id,
                revision=self._scene_runner.last_realized_snapshot_revision,
            )
            frame = self._presented_frame.prepare(
                fresh=bool(self._scene_runner.last_output_updated),
                snapshot_revision=self._scene_runner.last_realized_snapshot_revision,
                frame_id=self._scene_runner.last_realized_frame_id,
            )
            perf.record_preview_result(
                requested_revision=int(self._store.revision),
                presented_revision=frame.snapshot_revision,
                fresh=frame.fresh,
            )
            if monitor is not None:
                waiting = bool(self._scene_runner.is_waiting_for_fresh_result)
                monitor.set_transport(
                    # monitor は実際に表示する frame の時刻を示す。toolbar の
                    # clock 時刻との差は waiting/target として明示する。
                    t=frame.t,
                    requested_t=float(t),
                    waiting=waiting,
                    speed=(1.0 if recording else float(self._clock.speed)),
                    recording=bool(recording),
                )
            frame_vertices = 0
            frame_lines = 0
            mesh_was_uploaded = False
            for layer_index, item in enumerate(frame.layers):
                if frame.snapshot_revision is None:
                    raise RuntimeError("realized scene output did not publish a snapshot revision")
                uploads_before = int(self._renderer.mesh_upload_count)
                with perf.section("render_layer"):
                    stats = self._renderer.render_layer(
                        realized=item.realized,
                        cache_key=item.cache_key,
                        color=item.color,
                        thickness=item.thickness,
                        scene_serial=frame.scene_serial,
                        snapshot_revision=frame.snapshot_revision,
                        dynamic_slot=layer_index,
                    )
                uploads_after = int(self._renderer.mesh_upload_count)
                mesh_was_uploaded = mesh_was_uploaded or (uploads_after > uploads_before)
                frame_vertices += int(stats.draw_vertices)
                frame_lines += int(stats.draw_lines)
            self._renderer.finish_dynamic_frame(len(frame.layers))
            perf.record_event(
                "mesh_uploaded" if mesh_was_uploaded else "mesh_ready",
                frame_id=frame.frame_id,
                revision=frame.snapshot_revision,
            )
            perf.record_event(
                "draw_submitted",
                frame_id=frame.frame_id,
                revision=frame.snapshot_revision,
            )

            if monitor is not None:
                monitor.set_draw_counts(vertices=int(frame_vertices), lines=int(frame_lines))

            publication = self._presented_frame.publish(
                frame,
                quality=quality,
                canvas_size=self._options.canvas_size,
                background_color_rgb01=style.bg_color_rgb01,
                gcode_params=self._effective_config.gcode,
                capture_pending=self._capture_queue.has_pending_intents,
                recording_needs_provenance=(
                    recording and self._recording_session.needs_first_provenance
                ),
            )

            if recording:
                self._recording_session.record_presented_frame(
                    fresh=frame.fresh,
                    read_frame_rgb24=lambda: self._renderer.read_frame_rgb24(fb_w, fb_h),
                    provenance=publication.provenance,
                    error=self._last_frame_error,
                )

            if publication.capture_snapshot is not None:
                self._capture_queue.bind_presented_frame(publication.capture_snapshot)

            if perf.enabled and perf.gpu_finish:
                with perf.section("gpu_finish"):
                    self._renderer.finish()

    def _shutdown_export_snapshot(self) -> CaptureExportSnapshot:
        """close 直前の未結合 request に使う、最後の表示 frame を返す。"""

        style = self._style.resolve()
        snapshot = self._presented_frame.capture_snapshot_for_shutdown(
            canvas_size=self._options.canvas_size,
            background_color_rgb01=style.bg_color_rgb01,
            gcode_params=self._effective_config.gcode,
        )
        if snapshot is None:
            return self.final_capture_frame()
        return snapshot

    def close(
        self,
        *,
        timeout_s: float = DEFAULT_CAPTURE_SHUTDOWN_TIMEOUT_S,
    ) -> None:
        """accepted capture を確定し、GPU / window 資源を全て解放する。"""

        if self._closed:
            return
        timeout = float(timeout_s)
        if not isfinite(timeout) or timeout < 0.0:
            raise ValueError("timeout_s は有限の 0 以上である必要があります")
        capture_deadline = time.monotonic() + timeout

        def capture_time_remaining() -> float:
            return max(0.0, capture_deadline - time.monotonic())

        self._closed = True
        errors = CleanupErrors(
            report_secondary=lambda label: _logger.exception(
                "Cleanup step failed after an earlier error: %s",
                label,
            )
        )

        # --- 録画 ---
        # completed video temp を最初に artifact+manifest transaction へ確定する。
        # export backlog より後にすると、強制終了時に録画を失い得る。
        errors.attempt(
            lambda: self._recording_session.close(
                timeout_s=capture_time_remaining(),
                stop_reason="shutdown",
            ),
            "stop video recording",
        )

        # --- SVG/PNG/G-code capture ---
        errors.attempt(
            lambda: self._capture_queue.close(timeout_s=capture_time_remaining()),
            "close capture queue",
        )

        # --- MIDI ---
        # session は controller と frozen state を一体で所有する。
        midi = self._midi_session
        self._midi_session = None
        if midi is not None:
            errors.attempt(midi.close, "close MIDI session")

        # --- mp-draw worker / scene 実行器 ---
        errors.attempt(self._scene_runner.close, "close scene runner")
        errors.attempt(self._perf.close, "close performance trace")

        # renderer が保持している GPU resource は、所有 context の有効化に成功した
        # 場合だけ明示解放する。失敗時は別 context 上で raw GL delete を行わず、
        # window/context destroy に任せて後続 cleanup を継続する。
        context_active = False
        try:
            context_active = activate_pyglet_window_context(self.window)
        except BaseException as error:
            errors.record(error, "activate draw GL context")
        if context_active:
            errors.attempt(self._renderer.release, "release renderer")
        errors.attempt(
            lambda: close_pyglet_window(self.window),
            "close draw window",
        )
        errors.raise_if_any()
