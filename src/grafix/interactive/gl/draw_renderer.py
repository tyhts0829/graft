"""
Purpose:
    ModernGL context、framebuffer、line mesh、GPU cacheを所有するpreview rendererを提供する。
Use when:
    GL描画、viewport/readback、GPU cache、またはrenderer resource lifetimeを変更する場合。
Constraints:
    - CPU realizationと同じ`GeometryCacheKey`でGPU entryを区別する。
    - GL resourceは所有contextが生存中に解放し、runtimeへraw contextを公開しない。
Side effects:
    GPU resourceを確保・更新・描画・解放する。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

import moderngl
import numpy as np

from grafix.core.parameters.style import line_width_for_short_side
from grafix.core.realize import GeometryCacheKey
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.render_options import RenderOptions
from grafix.core.runtime_limits import DEFAULT_PREVIEW_RUNTIME_LIMITS, RuntimeLimits
from grafix.interactive.gl import utils as render_utils
from grafix.interactive.gl.index_buffer import LineIndexStats, build_line_indices_and_stats
from grafix.interactive.gl.line_mesh import LineMesh
from grafix.interactive.gl.shader import Shader
from grafix.interactive.diagnostics import DiagnosticCenter, DiagnosticEvent

if TYPE_CHECKING:
    from pyglet.window import Window


_CACHED_MESH_INITIAL_RESERVE = 4096
_CACHED_MESH_BUFFER_GROWTH_FACTOR = LineMesh.BUFFER_GROWTH_FACTOR
_MESH_CANDIDATE_ENTRY_BUDGET_BYTES = 256
_MESH_CANDIDATE_MAX_ENTRIES = 4096
_MESH_CACHE_MAX_ENTRIES = 4096
_DYNAMIC_MESH_MAX_ENTRIES = 256
_DYNAMIC_MESH_CACHE_DIVISOR = 4


def _gpu_mesh_cache_budgets(total_bytes: int) -> tuple[int, int]:
    """設定上限を static 3/4 と dynamic 1/4 へ重複なく分割する。"""

    total = max(0, int(total_bytes))
    dynamic = total // _DYNAMIC_MESH_CACHE_DIVISOR
    return total - dynamic, dynamic


def _mesh_candidate_entry_limit(byte_budget: int) -> int:
    """candidate 用 byte 枠を、metadata の明示的な件数上限へ変換する。"""

    return min(
        _MESH_CANDIDATE_MAX_ENTRIES,
        max(0, int(byte_budget)) // _MESH_CANDIDATE_ENTRY_BUDGET_BYTES,
    )


def _mesh_entry_limit(byte_budget: int, *, maximum: int) -> int:
    """最小 VBO/IBO reserve を基準に tiny mesh の件数上限を返す。"""

    minimum_mesh_bytes = 2 * _CACHED_MESH_INITIAL_RESERVE
    return min(
        int(maximum),
        max(0, int(byte_budget)) // minimum_mesh_bytes,
    )


def _new_mesh_buffer_capacity(required: int) -> int:
    """新規 cache mesh が最初の upload 後に確保する buffer byte 数を返す。"""

    required_bytes = int(required)
    if required_bytes <= _CACHED_MESH_INITIAL_RESERVE:
        return _CACHED_MESH_INITIAL_RESERVE
    return max(
        required_bytes,
        _CACHED_MESH_INITIAL_RESERVE * _CACHED_MESH_BUFFER_GROWTH_FACTOR,
    )


def _same_offsets(left: np.ndarray, right: np.ndarray) -> bool:
    """immutable offsets が同じ topology を表すか返す。"""

    return left is right or (left.shape == right.shape and np.array_equal(left, right))


def _aspect_fit_viewport(
    framebuffer_size: tuple[int, int],
    canvas_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """canvas の縦横比を保って framebuffer 中央へ収める viewport を返す。"""

    framebuffer_w = max(1, int(framebuffer_size[0]))
    framebuffer_h = max(1, int(framebuffer_size[1]))
    canvas_w = max(1, int(canvas_size[0]))
    canvas_h = max(1, int(canvas_size[1]))

    # float の aspect 比較ではなく交差積で判定し、
    # 同一 aspect で 1 px の不要な bar が出るのを防ぐ。
    if framebuffer_w * canvas_h >= framebuffer_h * canvas_w:
        viewport_h = framebuffer_h
        viewport_w = max(
            1,
            min(
                framebuffer_w,
                int(round(framebuffer_h * canvas_w / canvas_h)),
            ),
        )
    else:
        viewport_w = framebuffer_w
        viewport_h = max(
            1,
            min(
                framebuffer_h,
                int(round(framebuffer_w * canvas_h / canvas_w)),
            ),
        )

    viewport_x = (framebuffer_w - viewport_w) // 2
    viewport_y = (framebuffer_h - viewport_h) // 2
    return viewport_x, viewport_y, viewport_w, viewport_h


class DrawRenderer:
    """リアルタイム描画を担うシンプルなレンダラー。"""

    def __init__(
        self,
        window: Window,
        options: RenderOptions,
        *,
        runtime_limits: RuntimeLimits = DEFAULT_PREVIEW_RUNTIME_LIMITS,
        diagnostic_center: DiagnosticCenter | None = None,
    ) -> None:
        window.switch_to()
        self._ctx = moderngl.create_context(require=410)
        self.program = Shader.create_shader(self._ctx)
        # 動的更新用（キャッシュに乗らないケース）に 1 つだけ使い回す。
        self._scratch_mesh = LineMesh(self._ctx, self.program)
        # 静的ジオメトリ用の GPU メッシュキャッシュ（byte-budget LRU）。
        self._mesh_cache: OrderedDict[GeometryCacheKey, _MeshCacheEntry] = OrderedDict()
        # 初見を即キャッシュすると「毎フレーム別 id」ケースで逆効果になりうるため、
        # 2 回目以降にキャッシュへ昇格させる。
        self._mesh_candidates: OrderedDict[GeometryCacheKey, _MeshAdmission] = OrderedDict()
        # animated layer ごとに VBO は更新しつつ、安定した IBO/topology を
        # 再利用する bounded pool。static full mesh cache とは責務を分ける。
        self._dynamic_meshes: OrderedDict[int, _DynamicMeshEntry] = OrderedDict()
        # scratch IBO に現在入っている topology。RealizedGeometry は外部配列を
        # immutable snapshot へコピーするため、identity だけでなく内容一致でも再利用する。
        self._scratch_topology: _ScratchTopology | None = None
        self._mesh_cache_bytes = 0
        self._dynamic_mesh_bytes = 0
        self._dynamic_slot_count = 0
        self._mesh_upload_count = 0
        if not isinstance(runtime_limits, RuntimeLimits):
            raise TypeError("runtime_limits は RuntimeLimits である必要があります")
        self._diagnostic_center = diagnostic_center
        (
            self._mesh_cache_max_bytes,
            self._dynamic_mesh_max_bytes,
        ) = _gpu_mesh_cache_budgets(runtime_limits.gpu_cache_bytes)
        self._mesh_cache_max_entries = _mesh_entry_limit(
            self._mesh_cache_max_bytes,
            maximum=_MESH_CACHE_MAX_ENTRIES,
        )
        self._mesh_candidates_max_entries = _mesh_candidate_entry_limit(
            runtime_limits.gpu_candidate_cache_bytes
        )
        self._dynamic_mesh_max_entries = _mesh_entry_limit(
            self._dynamic_mesh_max_bytes,
            maximum=_DYNAMIC_MESH_MAX_ENTRIES,
        )
        self._canvas_w, self._canvas_h = options.canvas_size
        self._framebuffer_size = (1, 1)
        self._viewport = (0, 0, 1, 1)
        self._viewport_size = (1, 1)
        # uniform は program/context に属し、frame をまたいで保持される。
        # viewport 変更時だけ線幅の換算値が変わり得るため invalidation する。
        self._line_width_uniform = self.program["line_width_px"]
        self._color_uniform = self.program["color"]
        self._last_draw_style: tuple[float, tuple[float, float, float]] | None = None
        self.program["viewport_size"].value = (1.0, 1.0)
        # 射影行列はキャンバス寸法にのみ依存するため初期化時に一度設定する。
        projection = render_utils.build_projection(
            float(self._canvas_w),
            float(self._canvas_h),
        )
        self.program["projection"].write(projection.tobytes())

    @property
    def mesh_cache_max_bytes(self) -> int:
        """GPU mesh cache の byte 上限を返す。"""

        return int(self._mesh_cache_max_bytes)

    @property
    def mesh_candidate_cache_max_entries(self) -> int:
        """mesh admission candidate の件数上限を返す。"""

        return int(self._mesh_candidates_max_entries)

    @property
    def mesh_cache_max_entries(self) -> int:
        """full GPU mesh cache の件数上限を返す。"""

        return int(self._mesh_cache_max_entries)

    @property
    def dynamic_mesh_cache_max_entries(self) -> int:
        """animated layer 用 mesh pool の件数上限を返す。"""

        return int(self._dynamic_mesh_max_entries)

    @property
    def mesh_upload_count(self) -> int:
        """renderer lifetime 中の VBO/IBO upload 呼び出し回数を返す。"""

        return int(self._mesh_upload_count)

    def apply_runtime_limits(self, limits: RuntimeLimits) -> None:
        """quality 切替時に GPU cache 上限を適用する。"""

        if not isinstance(limits, RuntimeLimits):
            raise TypeError("limits は RuntimeLimits である必要があります")
        (
            self._mesh_cache_max_bytes,
            self._dynamic_mesh_max_bytes,
        ) = _gpu_mesh_cache_budgets(limits.gpu_cache_bytes)
        self._mesh_cache_max_entries = _mesh_entry_limit(
            self._mesh_cache_max_bytes,
            maximum=_MESH_CACHE_MAX_ENTRIES,
        )
        self._mesh_candidates_max_entries = _mesh_candidate_entry_limit(
            limits.gpu_candidate_cache_bytes
        )
        self._dynamic_mesh_max_entries = _mesh_entry_limit(
            self._dynamic_mesh_max_bytes,
            maximum=_DYNAMIC_MESH_MAX_ENTRIES,
        )
        self._evict_meshes_to_budget()
        self._evict_candidates_to_budget()
        self._evict_dynamic_meshes_to_budget()

    def _publish_gpu_cache_limit(
        self,
        *,
        requested: int,
        limit: int,
        reason: str,
        unit: str = "bytes",
    ) -> None:
        center = self._diagnostic_center
        if center is None:
            return
        requested_label = f"requested_{unit}"
        limit_label = f"limit_{unit}"
        center.publish(
            DiagnosticEvent(
                category="resource",
                severity="warning",
                summary=f"GPU cache limit reached: {reason}",
                details=(f"{requested_label}={int(requested)}\n{limit_label}={int(limit)}"),
                dedupe_key=(f"gpu-cache:{reason}:{unit}:{int(requested)}:{int(limit)}"),
            )
        )

    def viewport(self, width: int, height: int) -> None:
        """canvas 比率を保った viewport を framebuffer 中央へ設定する。"""

        framebuffer_size = (max(1, int(width)), max(1, int(height)))
        viewport = _aspect_fit_viewport(
            framebuffer_size,
            (int(self._canvas_w), int(self._canvas_h)),
        )
        size = (int(viewport[2]), int(viewport[3]))
        self._framebuffer_size = framebuffer_size
        self._viewport = viewport
        self._ctx.viewport = viewport
        if size != self._viewport_size:
            self._viewport_size = size
            self._last_draw_style = None
            self.program["viewport_size"].value = (float(size[0]), float(size[1]))

    def begin_frame(
        self,
        width: int,
        height: int,
        *,
        background_color: tuple[float, float, float],
    ) -> None:
        """default framebuffer を bind し、viewport と背景を確定する。"""

        self._ctx.screen.use()
        self.viewport(width, height)
        self.clear(background_color)

    def read_frame_rgb24(self, width: int, height: int) -> bytes:
        """default framebuffer 全体を tightly packed RGB24 として読む。"""

        framebuffer_w = max(1, int(width))
        framebuffer_h = max(1, int(height))
        return bytes(
            self._ctx.screen.read(
                viewport=(0, 0, framebuffer_w, framebuffer_h),
                components=3,
                alignment=1,
            )
        )

    def clear(self, color: tuple[float, float, float]) -> None:
        """letterbox/pillarbox 領域を含む framebuffer 全体を背景色でクリアする。"""

        framebuffer_w, framebuffer_h = self._framebuffer_size
        self._ctx.clear(
            *color,
            1.0,
            viewport=(0, 0, int(framebuffer_w), int(framebuffer_h)),
        )
        # backend によって clear(viewport=...) が GL viewport を変えても、
        # 後続の mesh draw は aspect-fit 領域に限定する。
        self._ctx.viewport = self._viewport

    def render_layer(
        self,
        realized: RealizedGeometry,
        *,
        cache_key: GeometryCacheKey,
        color: tuple[float, float, float],
        thickness: float,
        scene_serial: int,
        snapshot_revision: int,
        dynamic_slot: int | None = None,
    ) -> LineIndexStats:
        """RealizedGeometry をライン描画する。"""
        mesh, stats = self.prepare_layer_mesh(
            realized,
            cache_key=cache_key,
            scene_serial=scene_serial,
            snapshot_revision=snapshot_revision,
            dynamic_slot=dynamic_slot,
        )
        if mesh is None:
            return stats
        self.draw_prepared_mesh(mesh, color=color, thickness=thickness)
        return stats

    def prepare_layer_mesh(
        self,
        realized: RealizedGeometry,
        *,
        cache_key: GeometryCacheKey,
        scene_serial: int,
        snapshot_revision: int,
        dynamic_slot: int | None = None,
    ) -> tuple[LineMesh | None, LineIndexStats]:
        """upload（必要なら）を行い、描画に使う LineMesh を返す。"""
        slot = None if dynamic_slot is None else int(dynamic_slot)
        if slot is not None and slot < 0:
            raise ValueError("dynamic_slot は 0 以上である必要があります")
        entry = self._mesh_cache.get(cache_key)
        if entry is not None:
            self._mesh_cache.move_to_end(cache_key)
            if slot is not None:
                self._release_dynamic_mesh(slot)
            return entry.mesh, entry.stats

        admission = self._mesh_admission(
            scene_serial=scene_serial,
            snapshot_revision=snapshot_revision,
        )
        previous_admission = self._mesh_candidates.get(cache_key)
        was_candidate = previous_admission is not None
        should_promote = (
            previous_admission is not None
            and admission.scene_serial > previous_admission.scene_serial
            and admission.snapshot_revision == previous_admission.snapshot_revision
        )
        if should_promote:
            del self._mesh_candidates[cache_key]
        elif previous_admission is not None:
            # 同一 result の再表示は admission hit と数えない。parameter revision が
            # 変わった場合も候補を現在値へ進め、安定した次の fresh scene を待つ。
            if admission.scene_serial >= previous_admission.scene_serial:
                self._mesh_candidates[cache_key] = admission
                self._mesh_candidates.move_to_end(cache_key)

        dynamic_entry = None if slot is None else self._dynamic_meshes.get(slot)
        scratch_topology = self._scratch_topology
        if dynamic_entry is not None and _same_offsets(dynamic_entry.offsets, realized.offsets):
            indices = dynamic_entry.indices
            stats = dynamic_entry.stats
        elif scratch_topology is not None and _same_offsets(
            scratch_topology.offsets, realized.offsets
        ):
            indices = scratch_topology.indices
            stats = scratch_topology.stats
        else:
            indices, stats = build_line_indices_and_stats(realized.offsets)

        if indices.size == 0:
            if slot is not None:
                self._release_dynamic_mesh(slot)
            if scratch_topology is None or not _same_offsets(
                scratch_topology.offsets, realized.offsets
            ):
                self._scratch_topology = _ScratchTopology(
                    offsets=realized.offsets,
                    indices=indices,
                    stats=stats,
                )
            return None, stats

        if should_promote:
            mesh = self._promote_mesh(
                realized=realized,
                cache_key=cache_key,
                indices=indices,
                stats=stats,
            )
            if mesh is not None:
                self._release_dynamic_meshes_for_cache_key(cache_key)
                return mesh, stats

        if slot is not None:
            mesh = self._prepare_dynamic_mesh(
                slot=slot,
                cache_key=cache_key,
                realized=realized,
                indices=indices,
                stats=stats,
            )
            if mesh is not None:
                if not was_candidate:
                    self._remember_mesh_candidate(
                        cache_key,
                        vertices_nbytes=int(realized.coords.nbytes),
                        indices_nbytes=int(indices.nbytes),
                        admission=admission,
                    )
                return mesh, stats

        scratch_topology = self._scratch_topology
        scratch_topology_hit = scratch_topology is not None and _same_offsets(
            scratch_topology.offsets, realized.offsets
        )
        if scratch_topology_hit:
            self._scratch_mesh.upload_vertices(realized.coords)
        else:
            self._scratch_mesh.upload(vertices=realized.coords, indices=indices)
            self._scratch_topology = _ScratchTopology(
                offsets=realized.offsets,
                indices=indices,
                stats=stats,
            )
        self._record_mesh_upload()

        if not was_candidate:
            self._remember_mesh_candidate(
                cache_key,
                vertices_nbytes=int(realized.coords.nbytes),
                indices_nbytes=int(indices.nbytes),
                admission=admission,
            )
        return self._scratch_mesh, stats

    @staticmethod
    def _mesh_admission(
        *,
        scene_serial: int,
        snapshot_revision: int,
    ) -> _MeshAdmission:
        """fresh scene の cache admission 情報を正規化する。"""

        serial = int(scene_serial)
        revision = int(snapshot_revision)
        if serial < 0:
            raise ValueError("scene_serial は 0 以上である必要があります")
        if revision < 0:
            raise ValueError("snapshot_revision は 0 以上である必要があります")
        return _MeshAdmission(
            scene_serial=serial,
            snapshot_revision=revision,
        )

    def _promote_mesh(
        self,
        *,
        realized: RealizedGeometry,
        cache_key: GeometryCacheKey,
        indices: np.ndarray,
        stats: LineIndexStats,
    ) -> LineMesh | None:
        """2 回目の geometry を専用 GPU mesh へ昇格する。"""

        estimated_bytes = self._new_mesh_byte_size(
            vertices_nbytes=int(realized.coords.nbytes),
            indices_nbytes=int(indices.nbytes),
        )
        if estimated_bytes > self._mesh_cache_max_bytes:
            self._publish_gpu_cache_limit(
                requested=estimated_bytes,
                limit=self._mesh_cache_max_bytes,
                reason="mesh was not cached",
            )
            return None

        # VBO/IBO は別々に必要量まで成長させる。両方を大きい側の
        # サイズで予約すると、頂点だけ巨大な geometry で budget を浪費する。
        mesh = LineMesh(
            self._ctx,
            self.program,
            initial_reserve=_CACHED_MESH_INITIAL_RESERVE,
        )
        mesh.upload(vertices=realized.coords, indices=indices)
        self._record_mesh_upload()
        byte_size = int(mesh.vbo.size + mesh.ibo.size)
        if byte_size <= self._mesh_cache_max_bytes:
            self._mesh_cache[cache_key] = _MeshCacheEntry(
                mesh=mesh,
                stats=stats,
                byte_size=byte_size,
            )
            self._mesh_cache_bytes += byte_size
            self._evict_meshes_to_budget()
            return mesh

        self._publish_gpu_cache_limit(
            requested=byte_size,
            limit=self._mesh_cache_max_bytes,
            reason="mesh was not cached",
        )
        mesh.release()
        return None

    def _remember_mesh_candidate(
        self,
        cache_key: GeometryCacheKey,
        *,
        vertices_nbytes: int,
        indices_nbytes: int,
        admission: _MeshAdmission,
    ) -> None:
        """初見 key だけを、件数上限付き admission set へ記録する。"""

        estimated_bytes = self._new_mesh_byte_size(
            vertices_nbytes=vertices_nbytes,
            indices_nbytes=indices_nbytes,
        )
        if estimated_bytes > self._mesh_cache_max_bytes:
            self._publish_gpu_cache_limit(
                requested=estimated_bytes,
                limit=self._mesh_cache_max_bytes,
                reason="mesh was not cached",
            )
            return
        if self._mesh_candidates_max_entries <= 0:
            return
        self._mesh_candidates[cache_key] = admission
        self._evict_candidates_to_budget()

    @staticmethod
    def _new_mesh_byte_size(*, vertices_nbytes: int, indices_nbytes: int) -> int:
        """新規専用 mesh の初回 upload 後の GPU 確保量を見積もる。"""

        return _new_mesh_buffer_capacity(vertices_nbytes) + _new_mesh_buffer_capacity(
            indices_nbytes
        )

    def _prepare_dynamic_mesh(
        self,
        *,
        slot: int,
        cache_key: GeometryCacheKey,
        realized: RealizedGeometry,
        indices: np.ndarray,
        stats: LineIndexStats,
    ) -> LineMesh | None:
        """layer slot の VBO を更新し、安定 topology の IBO を再利用する。"""

        if self._dynamic_mesh_max_entries <= 0 or self._dynamic_mesh_max_bytes <= 0:
            return None

        entry = self._dynamic_meshes.get(slot)
        previous_bytes = 0 if entry is None else int(entry.byte_size)
        if entry is None:
            estimated = self._new_mesh_byte_size(
                vertices_nbytes=int(realized.coords.nbytes),
                indices_nbytes=int(indices.nbytes),
            )
            if estimated > self._dynamic_mesh_max_bytes:
                return None
            mesh = LineMesh(
                self._ctx,
                self.program,
                initial_reserve=_CACHED_MESH_INITIAL_RESERVE,
            )
            mesh.upload(vertices=realized.coords, indices=indices)
            self._record_mesh_upload()
        else:
            mesh = entry.mesh
            if entry.coords is realized.coords and entry.offsets is realized.offsets:
                if entry.cache_key != cache_key:
                    self._dynamic_meshes[slot] = _DynamicMeshEntry(
                        mesh=entry.mesh,
                        cache_key=cache_key,
                        coords=entry.coords,
                        offsets=entry.offsets,
                        indices=entry.indices,
                        stats=entry.stats,
                        byte_size=entry.byte_size,
                    )
                self._dynamic_meshes.move_to_end(slot)
                return mesh
            if _same_offsets(entry.offsets, realized.offsets):
                mesh.upload_vertices(realized.coords)
            else:
                mesh.upload(vertices=realized.coords, indices=indices)
            self._record_mesh_upload()

        byte_size = int(mesh.vbo.size + mesh.ibo.size)
        if byte_size > self._dynamic_mesh_max_bytes:
            if entry is not None:
                self._dynamic_meshes.pop(slot, None)
                self._dynamic_mesh_bytes -= previous_bytes
            mesh.release()
            return None

        self._dynamic_meshes[slot] = _DynamicMeshEntry(
            mesh=mesh,
            cache_key=cache_key,
            coords=realized.coords,
            offsets=realized.offsets,
            indices=indices,
            stats=stats,
            byte_size=byte_size,
        )
        self._dynamic_meshes.move_to_end(slot)
        self._dynamic_mesh_bytes += byte_size - previous_bytes
        self._evict_dynamic_meshes_to_budget()
        retained = self._dynamic_meshes.get(slot)
        return None if retained is None else retained.mesh

    def _record_mesh_upload(self) -> None:
        self._mesh_upload_count += 1

    def _release_dynamic_mesh(self, slot: int) -> None:
        """slot の transient mesh を解放し、static 昇格との重複保持を防ぐ。"""

        entry = self._dynamic_meshes.pop(int(slot), None)
        if entry is None:
            return
        self._dynamic_mesh_bytes -= int(entry.byte_size)
        entry.mesh.release()

    def _release_dynamic_meshes_for_cache_key(
        self,
        cache_key: GeometryCacheKey,
    ) -> None:
        """static 昇格した geometry と重複する全 transient mesh を解放する。"""

        slots = tuple(
            slot for slot, entry in self._dynamic_meshes.items() if entry.cache_key == cache_key
        )
        for slot in slots:
            self._release_dynamic_mesh(slot)

    def finish_dynamic_frame(self, slot_count: int) -> None:
        """layer数縮小時に末尾のdynamic slotを解放する。"""

        count = max(0, int(slot_count))
        previous = int(self._dynamic_slot_count)
        if count < previous:
            for slot in tuple(self._dynamic_meshes):
                if slot >= count:
                    self._release_dynamic_mesh(slot)
        self._dynamic_slot_count = count

    def _evict_meshes_to_budget(self) -> None:
        requested_bytes = self._mesh_cache_bytes
        evicted = 0
        while self._mesh_cache and (
            self._mesh_cache_bytes > self._mesh_cache_max_bytes
            or len(self._mesh_cache) > self._mesh_cache_max_entries
        ):
            _, entry = self._mesh_cache.popitem(last=False)
            self._mesh_cache_bytes -= entry.byte_size
            entry.mesh.release()
            evicted += 1
        if evicted:
            self._publish_gpu_cache_limit(
                requested=requested_bytes,
                limit=self._mesh_cache_max_bytes,
                reason=f"evicted {evicted} mesh entrie(s)",
            )

    def _evict_candidates_to_budget(self) -> None:
        while (
            len(self._mesh_candidates) > self._mesh_candidates_max_entries and self._mesh_candidates
        ):
            self._mesh_candidates.popitem(last=False)

    def _evict_dynamic_meshes_to_budget(self) -> None:
        """animated layer pool を byte / entry の両上限へ収める。"""

        while self._dynamic_meshes and (
            self._dynamic_mesh_bytes > self._dynamic_mesh_max_bytes
            or len(self._dynamic_meshes) > self._dynamic_mesh_max_entries
        ):
            _, entry = self._dynamic_meshes.popitem(last=False)
            self._dynamic_mesh_bytes -= int(entry.byte_size)
            entry.mesh.release()

    def draw_prepared_mesh(
        self,
        mesh: LineMesh,
        *,
        color: tuple[float, float, float],
        thickness: float,
    ) -> None:
        """LineMesh を draw call で描画する。"""
        previous_style = self._last_draw_style
        if previous_style is None or thickness != previous_style[0] or color != previous_style[1]:
            normalized_thickness = float(thickness)
            self._line_width_uniform.value = line_width_for_short_side(
                normalized_thickness,
                (float(self._viewport_size[0]), float(self._viewport_size[1])),
            )
            self._color_uniform.value = (*color, 1.0)
            self._last_draw_style = (normalized_thickness, color)

        # ボトルネックになりやすい: 多レイヤー/多 draw call 時はここ（ドライバ/GL 呼び出し）が支配しやすい。
        mesh.vao.render(mode=self._ctx.LINE_STRIP, vertices=mesh.index_count)

    def release(self) -> None:
        """GPU リソースを解放する。"""
        self._scratch_topology = None
        self._scratch_mesh.release()
        for cache_entry in self._mesh_cache.values():
            cache_entry.mesh.release()
        for dynamic_entry in self._dynamic_meshes.values():
            dynamic_entry.mesh.release()
        self._mesh_cache.clear()
        self._mesh_candidates.clear()
        self._dynamic_meshes.clear()
        self._mesh_cache_bytes = 0
        self._dynamic_mesh_bytes = 0
        self._dynamic_slot_count = 0
        self._last_draw_style = None
        self.program.release()
        self._ctx.release()

    def finish(self) -> None:
        """GPU の完了を待つ（計測用）。"""
        self._ctx.finish()


@dataclass(frozen=True, slots=True)
class _ScratchTopology:
    offsets: np.ndarray
    indices: np.ndarray
    stats: LineIndexStats


@dataclass(frozen=True, slots=True)
class _MeshCacheEntry:
    mesh: LineMesh
    stats: LineIndexStats
    byte_size: int


@dataclass(frozen=True, slots=True)
class _DynamicMeshEntry:
    mesh: LineMesh
    cache_key: GeometryCacheKey
    coords: np.ndarray
    offsets: np.ndarray
    indices: np.ndarray
    stats: LineIndexStats
    byte_size: int


@dataclass(frozen=True, slots=True)
class _MeshAdmission:
    scene_serial: int
    snapshot_revision: int
