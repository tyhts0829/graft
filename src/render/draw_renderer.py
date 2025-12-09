# どこで: `src/render/draw_renderer.py`。
# 何を: ライブ描画用の ModernGL レンダラーをカプセル化する。
# なぜ: コンテキスト生成・シェーダ設定・メッシュ転送を `run` から分離し、責務を明確にするため。

from __future__ import annotations

import moderngl
import numpy as np
from pyglet.window import Window

from src.core.realize import RealizedGeometry
from src.render.line_mesh import LineMesh
from src.render.render_settings import RenderSettings
from src.render.shader import Shader
from src.render import utils as render_utils


class DrawRenderer:
    """リアルタイム描画を担うシンプルなレンダラー。"""

    def __init__(self, window: Window, settings: RenderSettings) -> None:
        window.switch_to()
        self.ctx = moderngl.create_context(require=410)
        self.program = Shader.create_shader(self.ctx)
        self.mesh = LineMesh(self.ctx, self.program)
        self._canvas_w, self._canvas_h = settings.canvas_size

    def viewport(self, width: int, height: int) -> None:
        """ビューポートをウィンドウサイズに合わせて更新する。"""
        self.ctx.viewport = (0, 0, int(width), int(height))

    def clear(self, color: tuple[float, float, float, float]) -> None:
        """背景色でクリアする。"""
        self.ctx.clear(*color)

    def render_layer(
        self,
        realized: RealizedGeometry,
        indices: np.ndarray,
        *,
        color: tuple[float, float, float],
        thickness: float,
    ) -> None:
        """RealizedGeometry をライン描画する。"""
        if indices.size == 0:
            return

        self.mesh.upload(vertices=realized.coords, indices=indices)

        projection = render_utils.build_projection(
            float(self._canvas_w),
            float(self._canvas_h),
        )
        self.program["projection"].write(projection.tobytes())
        self.program["line_thickness"].value = float(thickness)
        self.program["color"].value = (*color, 1.0)

        self.mesh.vao.render(mode=self.ctx.LINES, vertices=self.mesh.index_count)

    def release(self) -> None:
        """GPU リソースを解放する。"""
        self.mesh.release()
        self.program.release()
        self.ctx.release()
