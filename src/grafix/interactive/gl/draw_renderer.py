# どこで: `src/grafix/interactive/gl/draw_renderer.py`。
# 何を: ライブ描画用の ModernGL レンダラーをカプセル化する。
# なぜ: コンテキスト生成・シェーダ設定・メッシュ転送を `run` から分離し、責務を明確にするため。

from __future__ import annotations

from collections import OrderedDict

import moderngl
import numpy as np
from pyglet.window import Window

from grafix.core.realized_geometry import RealizedGeometry
from grafix.interactive.gl import utils as render_utils
from grafix.interactive.gl.line_mesh import LineMesh
from grafix.interactive.render_settings import RenderSettings
from grafix.interactive.gl.shader import Shader


class DrawRenderer:
    """リアルタイム描画を担うシンプルなレンダラー。"""

    def __init__(self, window: Window, settings: RenderSettings) -> None:
        window.switch_to()
        self.ctx = moderngl.create_context(require=410)
        self.program = Shader.create_shader(self.ctx)
        # 動的更新用（キャッシュに乗らないケース）に 1 つだけ使い回す。
        self._scratch_mesh = LineMesh(self.ctx, self.program)
        # 静的ジオメトリ用の GPU メッシュキャッシュ（LRU）。
        self._mesh_cache: OrderedDict[str, LineMesh] = OrderedDict()
        # 初見を即キャッシュすると「毎フレーム別 id」ケースで逆効果になりうるため、
        # 2 回目以降にキャッシュへ昇格させる。
        self._mesh_candidates: OrderedDict[str, None] = OrderedDict()
        self._mesh_cache_max_items = 256
        self._mesh_candidates_max_items = 512
        self._canvas_w, self._canvas_h = settings.canvas_size
        # 射影行列はキャンバス寸法にのみ依存するため初期化時に一度設定する。
        projection = render_utils.build_projection(
            float(self._canvas_w),
            float(self._canvas_h),
        )
        self.program["projection"].write(projection.tobytes())

    def viewport(self, width: int, height: int) -> None:
        """ビューポートをウィンドウサイズに合わせて更新する。"""
        self.ctx.viewport = (0, 0, int(width), int(height))

    def clear(self, color: tuple[float, float, float]) -> None:
        """背景色でクリアする。"""
        self.ctx.clear(*color, 1.0)

    def render_layer(
        self,
        realized: RealizedGeometry,
        indices: np.ndarray,
        *,
        geometry_id: str,
        color: tuple[float, float, float],
        thickness: float,
    ) -> None:
        """RealizedGeometry をライン描画する。"""
        mesh = self.prepare_layer_mesh(realized, indices, geometry_id=geometry_id)
        if mesh is None:
            return
        self.draw_prepared_mesh(mesh, color=color, thickness=thickness)

    def prepare_layer_mesh(
        self,
        realized: RealizedGeometry,
        indices: np.ndarray,
        *,
        geometry_id: str,
    ) -> LineMesh | None:
        """upload（必要なら）を行い、描画に使う LineMesh を返す。"""
        if indices.size == 0:
            return None

        mesh = self._mesh_cache.get(geometry_id)
        if mesh is not None:
            self._mesh_cache.move_to_end(geometry_id)
        else:
            if geometry_id in self._mesh_candidates:
                # 2 回目以降の登場なのでキャッシュへ昇格し、以後の upload をスキップする。
                self._mesh_candidates.pop(geometry_id, None)
                reserve = max(int(realized.coords.nbytes), int(indices.nbytes), 4096)
                mesh = LineMesh(self.ctx, self.program, initial_reserve=reserve)
                mesh.upload(vertices=realized.coords, indices=indices)
                self._mesh_cache[geometry_id] = mesh
                while len(self._mesh_cache) > int(self._mesh_cache_max_items):
                    _, evicted = self._mesh_cache.popitem(last=False)
                    evicted.release()
            else:
                # 初見は候補として覚えておき、描画は scratch に upload して行う。
                self._mesh_candidates[geometry_id] = None
                self._mesh_candidates.move_to_end(geometry_id)
                while len(self._mesh_candidates) > int(self._mesh_candidates_max_items):
                    self._mesh_candidates.popitem(last=False)
                mesh = self._scratch_mesh
                mesh.upload(vertices=realized.coords, indices=indices)

        return mesh

    def draw_prepared_mesh(
        self,
        mesh: LineMesh,
        *,
        color: tuple[float, float, float],
        thickness: float,
    ) -> None:
        """LineMesh を draw call で描画する。"""
        self.program["line_thickness"].value = float(thickness)
        self.program["color"].value = (*color, 1.0)

        # ボトルネックになりやすい: 多レイヤー/多 draw call 時はここ（ドライバ/GL 呼び出し）が支配しやすい。
        mesh.vao.render(mode=self.ctx.LINE_STRIP, vertices=mesh.index_count)

    def release(self) -> None:
        """GPU リソースを解放する。"""
        self._scratch_mesh.release()
        for mesh in self._mesh_cache.values():
            mesh.release()
        self._mesh_cache.clear()
        self._mesh_candidates.clear()
        self.program.release()
        self.ctx.release()

    def finish(self) -> None:
        """GPU の完了を待つ（計測用）。"""
        self.ctx.finish()
