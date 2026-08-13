"""
Purpose:
    line描画のVBO/IBO/VAOを一つのGPU allocation ownerとして管理する。
Use when:
    geometry upload、buffer growth/reuse、primitive restart、またはmesh releaseを変更する場合。
Constraints:
    - vertex/index capacityを必要時だけ拡張し、同じcontext内のresourceとして扱う。
    - polyline間は固定restart indexで分離し、入力indexの意味を変えない。
Side effects:
    GPU bufferとvertex arrayを確保・更新・解放する。
"""

from __future__ import annotations

from typing import Any

import numpy as np


class LineMesh:
    """
    GPUに頂点やインデックスなどの描画データを送り込む作業を管理
    """

    PRIMITIVE_RESTART_INDEX = 0xFFFFFFFF
    BUFFER_GROWTH_FACTOR = 2

    def __init__(
        self,
        ctx: Any,
        program: Any,
        # 初期GPUメモリ確保量を抑制（既定: 8MB）。必要に応じて自動拡張。
        initial_reserve: int = 8 * 1024 * 1024,
    ) -> None:
        """
        ctx: GPUへの描画処理を行うためのモダンOpenGL（moderngl）コンテキスト
        program: GPU側で使うシェーダープログラム。
        VBO (Vertex Buffer Object): GPUに送る「頂点データ」を格納するメモリ。
        IBO (Index Buffer Object): GPUに「頂点の順序（描画のための索引）」を送るメモリ。
        VAO (Vertex Array Object): VBOとIBOを関連付けて、描画命令をシンプルに管理する仕組み。
        Primitive Restart Index: 描画時に「ここで一旦区切る」という目印。
        """
        self.ctx = ctx
        self.program = program
        self.initial_reserve = initial_reserve
        # 命名統一: primitive_restart_index に一本化

        # バッファ予約
        self.vbo = ctx.buffer(reserve=initial_reserve, dynamic=True)
        self.ibo = ctx.buffer(reserve=initial_reserve, dynamic=True)
        self.vao = ctx.simple_vertex_array(
            program, self.vbo, "in_vert", index_buffer=self.ibo
        )

        # 描画ステート
        self.index_count: int = 0
        self.ctx.primitive_restart = True  # type: ignore
        self.ctx.primitive_restart_index = self.PRIMITIVE_RESTART_INDEX  # type: ignore

    # ---------- バッファ操作 ----------
    def _ensure_capacity(self, vbo_size: int, ibo_size: int) -> None:
        """データが大きくなったらGPUのバッファを再確保"""
        grow_vbo = int(vbo_size) > int(self.vbo.size)
        grow_ibo = int(ibo_size) > int(self.ibo.size)
        if not grow_vbo and not grow_ibo:
            return

        old_vbo = self.vbo
        old_ibo = self.ibo
        new_vbo = old_vbo
        new_ibo = old_ibo
        try:
            if grow_vbo:
                new_vbo = self.ctx.buffer(
                    reserve=self._grown_capacity(old_vbo.size, vbo_size),
                    dynamic=True,
                )
            if grow_ibo:
                new_ibo = self.ctx.buffer(
                    reserve=self._grown_capacity(old_ibo.size, ibo_size),
                    dynamic=True,
                )
            new_vao = self.ctx.simple_vertex_array(
                self.program,
                new_vbo,
                "in_vert",
                index_buffer=new_ibo,
            )
        except BaseException:
            if new_vbo is not old_vbo:
                new_vbo.release()
            if new_ibo is not old_ibo:
                new_ibo.release()
            raise

        self.vao.release()
        if new_vbo is not old_vbo:
            old_vbo.release()
        if new_ibo is not old_ibo:
            old_ibo.release()
        self.vbo = new_vbo
        self.ibo = new_ibo
        self.vao = new_vao

    def _grown_capacity(self, current: int, required: int) -> int:
        """再確保回数を抑える geometric growth 後の byte 数を返す。"""

        return max(
            int(required),
            int(self.initial_reserve),
            int(current) * self.BUFFER_GROWTH_FACTOR,
        )

    def upload(self, vertices: np.ndarray, indices: np.ndarray) -> None:
        """頂点とインデックスを GPU へ送り込む。"""
        vertices_f32 = np.ascontiguousarray(vertices, dtype=np.float32)
        indices_u32 = np.ascontiguousarray(indices, dtype=np.uint32)
        self._ensure_capacity(vertices_f32.nbytes, indices_u32.nbytes)

        self.vbo.orphan()
        self.vbo.write(vertices_f32)

        self.ibo.orphan()
        self.ibo.write(indices_u32)

        self.index_count = len(indices_u32)

    def upload_vertices(self, vertices: np.ndarray) -> None:
        """IBO を維持したまま頂点だけを GPU へ送り込む。"""

        vertices_f32 = np.ascontiguousarray(vertices, dtype=np.float32)
        self._ensure_capacity(vertices_f32.nbytes, 0)

        self.vbo.orphan()
        self.vbo.write(vertices_f32)

    def release(self) -> None:
        """GPUのメモリを解放する（終了時に使う）"""
        self.vbo.release()
        self.ibo.release()
        self.vao.release()
