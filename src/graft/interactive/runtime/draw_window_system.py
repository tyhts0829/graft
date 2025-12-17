# どこで: `src/graft/interactive/runtime/draw_window_system.py`。
# 何を: `draw(t)` が返すシーンを描画ウィンドウへ描画するサブシステムを提供する。
# なぜ: `src/graft/api/run.py` の `run()` を「配線」に寄せ、描画責務を独立させるため。

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from pyglet.window import key

from graft.interactive.draw_window import create_draw_window
from graft.core.parameters import ParamStore, parameter_context
from graft.core.parameters.persistence import default_param_store_path
from graft.core.parameters.style import (
    coerce_rgb255,
    ensure_style_entries,
    rgb01_to_rgb255,
    rgb255_to_rgb01,
    style_key,
)
from graft.interactive.gl.draw_renderer import DrawRenderer
from graft.core.pipeline import RealizedLayer, realize_scene
from graft.interactive.gl.index_buffer import build_line_indices
from graft.core.layer import LayerStyleDefaults
from graft.export.svg import export_svg
from graft.interactive.render_settings import RenderSettings
from graft.core.scene import SceneItem
from graft.interactive.runtime.perf import PerfCollector


class DrawWindowSystem:
    """描画（メインウィンドウ）のサブシステム。"""

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        settings: RenderSettings,
        defaults: LayerStyleDefaults,
        store: ParamStore,
    ) -> None:
        """描画用の window/renderer を初期化する。"""

        # 設定/既定スタイル/draw 関数/ParamStore は 1 フレームごとに参照するため保持しておく。
        self._settings = settings
        self._defaults = defaults
        self._draw = draw
        self._store = store

        ensure_style_entries(
            self._store,
            background_color_rgb01=settings.background_color,
            global_thickness=defaults.thickness,
            global_line_color_rgb01=defaults.color,
        )

        self._style_key_background = style_key("background_color")
        self._style_key_thickness = style_key("global_thickness")
        self._style_key_line_color = style_key("global_line_color")
        self._style_base_background = rgb01_to_rgb255(settings.background_color)
        self._style_base_thickness = float(defaults.thickness)
        self._style_base_line_color = rgb01_to_rgb255(defaults.color)

        # 描画用の pyglet window を作成し、その window の OpenGL コンテキストに紐づく renderer を作る。
        self.window = create_draw_window(settings)
        self._renderer = DrawRenderer(self.window, settings)

        script_stem = default_param_store_path(self._draw).stem
        self._svg_output_path = Path("data") / "output" / "svg" / f"{script_stem}.svg"
        self._last_realized_layers: list[RealizedLayer] = []
        self.window.push_handlers(on_key_press=self._on_key_press)

        # draw(t) に渡す t の基準時刻。
        self._start_time = time.perf_counter()
        self._perf = PerfCollector.from_env()

    def _on_key_press(self, symbol: int, _modifiers: int) -> None:
        if symbol == key.S:
            path = self.save_svg()
            print(f"Saved SVG: {path}")

    def save_svg(self) -> Path:
        """最後に描画したフレームを SVG として保存し、保存先パスを返す。"""
        return export_svg(
            self._last_realized_layers,
            self._svg_output_path,
            canvas_size=self._settings.canvas_size,
        )

    def draw_frame(self) -> None:
        """1 フレーム分の描画を行う（`flip()` は呼ばない）。"""

        perf = self._perf
        with perf.frame():
            # 注: 呼び出し側（MultiWindowLoop）が事前に self.window.switch_to() 済みである前提。
            # その前提が崩れると、別 window のコンテキストへ描いてしまう可能性がある。

            # --- 1) ビューポート更新 ---
            #
            # ウィンドウの論理解像度（width/height）はフレームごとに参照し、
            # 現在のサイズに合わせて OpenGL の viewport を更新する。
            # （resizable=False でも、内部事情や将来の変更に備えて毎フレーム更新している）
            self._renderer.viewport(self.window.width, self.window.height)

            # --- 2) Style（背景色 / グローバル線幅 / グローバル線色）の確定 ---
            #
            # Style は ParamStore の “特殊キー” に入っており、GUI から編集できる。
            # ここでは「override=True なら ui_value を採用」「override=False ならベース値へ戻す」という
            # 単純な規則で、そのフレームで使う style を決める。
            #
            # 注: Style は Geometry の param 解決（resolve_params）とは別系統として扱う。
            # - style を resolve_params 経由にすると量子化などが絡みやすい
            # - 背景クリアは draw(t) の外（フレームの冒頭）で確定したい
            # という理由で、ここでは store を直接参照している。

            # 背景色:
            # - store に state が無い場合（想定外）や override=False の場合は “run 引数のベース値” を使う。
            # - override=True の場合は GUI の ui_value（RGB 0..255）を使う。
            bg_state = self._store.get_state(self._style_key_background)
            bg255 = (
                self._style_base_background
                if bg_state is None or not bg_state.override
                else coerce_rgb255(bg_state.ui_value)
            )
            # renderer.clear は 0..1 float を要求するため、ここで変換する。
            bg_color = rgb255_to_rgb01(bg255)

            # グローバル線色:
            # Layer.color が None のときに、この色が既定色として適用される。
            line_state = self._store.get_state(self._style_key_line_color)
            line255 = (
                self._style_base_line_color
                if line_state is None or not line_state.override
                else coerce_rgb255(line_state.ui_value)
            )
            global_line_color = rgb255_to_rgb01(line255)

            # グローバル線幅:
            # Layer.thickness が None のときに、この値が既定線幅として適用される。
            # 0 以下は仕様違反（resolve_layer_style が例外）なので、GUI 側で正の値にクランプする前提。
            thickness_state = self._store.get_state(self._style_key_thickness)
            global_thickness = (
                float(self._style_base_thickness)
                if thickness_state is None or not thickness_state.override
                else float(thickness_state.ui_value)
            )

            # --- 3) 背景クリア ---
            #
            # まず背景色でクリアしてから、このフレームのシーンを描く。
            self._renderer.clear(bg_color)

            # --- 4) 時刻 t の算出 ---
            #
            # draw(t) は “開始時刻からの経過秒” を受け取る。
            # これを使ってユーザー側でアニメーション等を表現できる。
            t = time.perf_counter() - self._start_time

            # --- 5) Geometry の param 解決 + 描画 ---
            #
            # parameter_context は “このフレームで参照する ParamStore のスナップショット” を固定し、
            # draw(t) の途中で GUI が動いても、このフレームの解決結果がブレないようにする。
            #
            # さらに finally で FrameParamsBuffer を ParamStore にマージすることで、
            # 「このフレームで観測されたパラメータ」を次フレーム以降の GUI に出せるようにする。
            with parameter_context(self._store, cc_snapshot=None):
                # resolve_layer_style が参照する既定スタイルを、このフレームの style で差し替える。
                effective_defaults = LayerStyleDefaults(
                    color=global_line_color,
                    thickness=global_thickness,
                )
                draw_fn = self._draw
                if perf.enabled:
                    def draw_fn_timed(t_arg: float) -> SceneItem:
                        with perf.section("draw"):
                            return self._draw(t_arg)

                    draw_fn = draw_fn_timed
                with perf.section("scene"):
                    realized_layers = realize_scene(draw_fn, t, effective_defaults)
                self._last_realized_layers = realized_layers
                for item in realized_layers:
                    with perf.section("indices"):
                        indices = build_line_indices(item.realized.offsets)
                    with perf.section("render_layer"):
                        self._renderer.render_layer(
                            realized=item.realized,
                            indices=indices,
                            color=item.color,
                            thickness=item.thickness,
                        )

            if perf.enabled and perf.gpu_finish:
                with perf.section("gpu_finish"):
                    self._renderer.finish()

    def close(self) -> None:
        """GPU / window 資源を解放する。"""

        # renderer が保持している GPU リソースを破棄してから window を閉じる。
        self._renderer.release()
        self.window.close()
