"""
どこで: `src/api/run.py`。公開 API のランナー実装。
何を: pyglet + ModernGL を使い、`draw(t)` が返す Geometry をウィンドウに描画する最小ランナーを提供する。
なぜ: `main.py` を実行して実際に線をプレビューできる経路を用意するため。
"""

from __future__ import annotations

import time
from typing import Callable

import moderngl
import numpy as np
import pyglet

from src.core.geometry import Geometry
from src.core.realize import realize
from src.render.line_mesh import LineMesh
from src.render.shader import Shader
from src.render import utils as render_utils


def _build_line_indices(offsets: np.ndarray) -> np.ndarray:
    """RealizedGeometry.offsets から GL_LINES 用インデックス配列を生成する。"""
    indices: list[int] = []
    for i in range(len(offsets) - 1):
        start = int(offsets[i])
        end = int(offsets[i + 1])
        if end - start < 2:
            continue
        for k in range(start, end - 1):
            indices.append(k)
            indices.append(k + 1)
        if i < len(offsets) - 2:
            indices.append(LineMesh.PRIMITIVE_RESTART_INDEX)
    if not indices:
        return np.zeros((0,), dtype=np.uint32)
    return np.asarray(indices, dtype=np.uint32)


def run(
    draw: Callable[[float], Geometry],
    *,
    background_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
    line_thickness: float = 0.01,
    line_color: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    render_scale: float = 1.0,
    canvas_size: tuple[int, int] = (800, 800),
) -> None:
    """`draw(t)` で生成される Geometry を pyglet ウィンドウへ描画する。

    Parameters
    ----------
    draw : Callable[[float], Geometry]
        フレーム時刻 t を受け取り Geometry を返すコールバック。
    background_color : tuple[float, float, float, float]
        背景色 RGBA。既定は白。
    line_thickness : float
        プレビュー用の線幅（クリップ空間での最終幅）。Layer.thickness 未指定時の基準値。
    line_color : tuple[float, float, float, float]
        線色 RGBA。既定は黒。
    render_scale : float
        キャンバス寸法に掛けるピクセル倍率。高精細プレビュー用。
    canvas_size : tuple[int, int]
        キャンバス寸法（任意単位）。投影行列生成とウィンドウサイズ決定に使用。
    """

    canvas_w, canvas_h = canvas_size
    window = pyglet.window.Window(
        width=int(canvas_w * render_scale),
        height=int(canvas_h * render_scale),
        resizable=True,
        caption="Graft Preview",
    )

    window.switch_to()
    mgl_ctx = moderngl.create_context(require=410)
    program = Shader.create_shader(mgl_ctx)
    mesh = LineMesh(mgl_ctx, program)

    start_time = time.perf_counter()
    closed = False

    def render_frame() -> None:
        t = time.perf_counter() - start_time
        geometry = draw(t)
        realized = realize(geometry)

        indices = _build_line_indices(realized.offsets)
        if indices.size == 0:
            return

        mesh.upload(vertices=realized.coords, indices=indices)

        projection = render_utils.build_projection(float(canvas_w), float(canvas_h))
        program["projection"].write(projection.tobytes())
        program["line_thickness"].value = float(line_thickness)
        program["color"].value = line_color

        mesh.vao.render(mode=mgl_ctx.LINES, vertices=mesh.index_count)

    def on_draw() -> None:
        mgl_ctx.viewport = (0, 0, window.width, window.height)
        mgl_ctx.clear(*background_color)
        render_frame()

    def on_resize(width: int, height: int) -> None:
        mgl_ctx.viewport = (0, 0, width, height)

    def on_close() -> None:
        nonlocal closed
        closed = True
        pyglet.clock.unschedule(tick)
        mesh.release()
        program.release()
        mgl_ctx.release()
        window.close()

    def tick(dt: float) -> None:
        if closed or window.has_exit:
            return
        window.switch_to()
        try:
            on_draw()
        except Exception:
            pyglet.app.exit()
            raise
        window.flip()

    window.push_handlers(on_draw=on_draw, on_close=on_close, on_resize=on_resize)
    pyglet.clock.schedule_interval(tick, 1 / 60.0)
    pyglet.app.run()
