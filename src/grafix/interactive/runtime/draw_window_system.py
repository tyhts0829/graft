# どこで: `src/grafix/interactive/runtime/draw_window_system.py`。
# 何を: `draw(t)` が返すシーンを描画ウィンドウへ描画するサブシステムを提供する。
# なぜ: `src/grafix/api/runner.py` の `run()` を「配線」に寄せ、描画責務を独立させるため。

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pyglet.window import key

from grafix.core.parameters import ParamStore
from grafix.core.parameters.persistence import default_param_store_path
from grafix.core.layer import LayerStyleDefaults
from grafix.core.pipeline import RealizedLayer
from grafix.export.svg import export_svg
from grafix.export.image import (
    default_png_output_path,
    png_output_size,
    rasterize_svg_to_png,
)
from grafix.interactive.draw_window import create_draw_window
from grafix.interactive.gl.draw_renderer import DrawRenderer
from grafix.interactive.gl.index_buffer import build_line_indices_and_stats
from grafix.interactive.render_settings import RenderSettings
from grafix.core.scene import SceneItem
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.midi import MidiController
from grafix.interactive.runtime.frame_clock import RealTimeClock
from grafix.interactive.runtime.recording_system import VideoRecordingSystem
from grafix.interactive.runtime.scene_runner import SceneRunner
from grafix.interactive.runtime.style_resolver import StyleResolver
from grafix.interactive.runtime.video_recorder import default_video_output_path
from grafix.core.runtime_config import output_root_dir

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from grafix.interactive.runtime.monitor import RuntimeMonitor


class DrawWindowSystem:
    """描画（メインウィンドウ）のサブシステム。"""

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        settings: RenderSettings,
        defaults: LayerStyleDefaults,
        store: ParamStore,
        midi_controller: MidiController | None = None,
        frozen_cc_snapshot: dict[int, float] | None = None,
        monitor: RuntimeMonitor | None = None,
        fps: float = 60.0,
        n_worker: int = 0,
    ) -> None:
        """描画用の window/renderer を初期化する。"""

        # 設定/既定スタイル/draw 関数/ParamStore は 1 フレームごとに参照するため保持しておく。
        self._settings = settings
        self._store = store
        self._midi_controller = midi_controller
        self._frozen_cc_snapshot: dict[int, float] = (
            dict(frozen_cc_snapshot) if frozen_cc_snapshot is not None else {}
        )
        self._monitor = monitor

        self._style = StyleResolver(
            self._store,
            base_background_color_rgb01=settings.background_color,
            base_global_thickness=float(defaults.thickness),
            base_global_line_color_rgb01=defaults.color,
        )

        # 描画用の pyglet window を作成し、その window の OpenGL コンテキストに紐づく renderer を作る。
        self.window = create_draw_window(settings)
        self._renderer = DrawRenderer(self.window, settings)

        script_stem = default_param_store_path(draw).stem
        self._svg_output_path = output_root_dir() / "svg" / f"{script_stem}.svg"
        self._png_output_path = default_png_output_path(draw)
        video_output_path = default_video_output_path(draw, ext="mp4")
        self._recording = VideoRecordingSystem(output_path=video_output_path, fps=float(fps))
        self._last_realized_layers: list[RealizedLayer] = []
        self._pending_png_save = False
        self.window.push_handlers(on_key_press=self._on_key_press)

        # draw(t) に渡す t の基準時刻。
        start_time = time.perf_counter()
        self._clock = RealTimeClock(start_time=start_time)
        self._perf = PerfCollector.from_env()
        self._scene_runner = SceneRunner(draw, perf=self._perf, n_worker=int(n_worker))

    def _on_key_press(self, symbol: int, _modifiers: int) -> None:
        if symbol == key.S:
            path = self.save_svg()
            print(f"Saved SVG: {path}")
            return
        if symbol == key.P:
            self._pending_png_save = True
            return
        if symbol == key.V:
            if not self._recording.is_recording:
                self.start_video_recording()
            else:
                self.stop_video_recording()

    def save_svg(self) -> Path:
        """最後に描画したフレームを SVG として保存し、保存先パスを返す。"""
        return export_svg(
            self._last_realized_layers,
            self._svg_output_path,
            canvas_size=self._settings.canvas_size,
        )

    def start_video_recording(self) -> None:
        """動画録画を開始する。"""

        fb_w, fb_h = self._framebuffer_size()
        self._recording.start(framebuffer_size=(int(fb_w), int(fb_h)), t0=self._clock.t())

    def stop_video_recording(self) -> None:
        """動画録画を終了する。"""

        self._recording.stop()

    def _framebuffer_size(self) -> tuple[int, int]:
        getter = getattr(self.window, "get_framebuffer_size", None)
        if callable(getter):
            w, h = getter()
            return int(w), int(h)
        return int(self.window.width), int(self.window.height)

    def draw_frame(self) -> None:
        """1 フレーム分の描画を行う（`flip()` は呼ばない）。"""

        perf = self._perf
        with perf.frame():
            midi = self._midi_controller
            if midi is not None:
                midi.poll_pending()
                cc_snapshot = midi.snapshot()
            else:
                cc_snapshot = self._frozen_cc_snapshot

            # 注: 呼び出し側（pyglet.window.Window.draw）が事前に self.window.switch_to() 済みである前提。
            # その前提が崩れると、別 window のコンテキストへ描いてしまう可能性がある。
            #
            # さらに、録画の read などで framebuffer binding が揺れるケースに備え、
            # 毎フレーム「screen」を明示的に bind してから描画を始める。
            self._renderer.ctx.screen.use()

            # --- 1) ビューポート更新 ---
            #
            # ウィンドウの論理解像度（width/height）はフレームごとに参照し、
            # 現在のサイズに合わせて OpenGL の viewport を更新する。
            # （resizable=False でも、内部事情や将来の変更に備えて毎フレーム更新している）
            fb_w, fb_h = self._framebuffer_size()
            self._renderer.viewport(fb_w, fb_h)

            # --- 2) Style（背景色 / グローバル線幅 / グローバル線色）の確定 ---
            style = self._style.resolve()

            # --- 3) 背景クリア ---
            #
            # まず背景色でクリアしてから、このフレームのシーンを描く。
            self._renderer.clear(style.bg_color_rgb01)

            # --- 4) 時刻 t の算出 ---
            #
            # draw(t) は “開始時刻からの経過秒” を受け取る。
            # これを使ってユーザー側でアニメーション等を表現できる。
            recording = self._recording.is_recording
            t = self._recording.t() if recording else self._clock.t()

            # --- 5) Geometry の param 解決 + 描画 ---
            #
            effective_defaults = LayerStyleDefaults(
                color=style.global_line_color_rgb01,
                thickness=style.global_thickness,
            )
            realized_layers = self._scene_runner.run(
                t,
                store=self._store,
                cc_snapshot=cc_snapshot,
                defaults=effective_defaults,
                recording=recording,
            )
            self._last_realized_layers = realized_layers
            frame_vertices = 0
            frame_lines = 0
            for item in realized_layers:
                with perf.section("indices"):
                    indices, stats = build_line_indices_and_stats(item.realized.offsets)
                frame_vertices += int(stats.draw_vertices)
                frame_lines += int(stats.draw_lines)
                with perf.section("render_layer"):
                    self._renderer.render_layer(
                        realized=item.realized,
                        indices=indices,
                        geometry_id=item.layer.geometry.id,
                        color=item.color,
                        thickness=item.thickness,
                    )

            monitor = self._monitor
            if monitor is not None:
                monitor.set_draw_counts(vertices=int(frame_vertices), lines=int(frame_lines))

            if recording:
                with perf.section("video"):
                    self._recording.write_frame(self._renderer.ctx.screen)

            if self._pending_png_save:
                self._pending_png_save = False
                try:
                    svg_path = self.save_svg()
                    png_path = rasterize_svg_to_png(
                        svg_path,
                        self._png_output_path,
                        output_size=png_output_size(self._settings.canvas_size),
                        background_color_rgb01=style.bg_color_rgb01,
                    )
                    print(f"Saved PNG: {png_path}")
                except Exception as e:
                    _logger.exception("Failed to save PNG")
                    print(f"Failed to save PNG: {e}")

            if perf.enabled and perf.gpu_finish:
                with perf.section("gpu_finish"):
                    self._renderer.finish()

    def close(self) -> None:
        """GPU / window 資源を解放する。"""

        if self._recording.is_recording:
            try:
                self.stop_video_recording()
            except Exception:
                _logger.exception("Failed to stop video recording")

        midi = self._midi_controller
        self._midi_controller = None
        if midi is not None:
            try:
                midi.save()
            except Exception:
                _logger.exception("Failed to save MIDI CC snapshot: %s", midi.path)
            finally:
                midi.close()

        self._scene_runner.close()

        # renderer が保持している GPU リソースを破棄してから window を閉じる。
        self._renderer.release()
        self.window.close()
