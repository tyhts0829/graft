"""Fake-GL renderer benchmark case provider。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import numpy as np

from grafix.api.render import RenderOptions
from grafix.core.evaluation_context import (
    EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
    EvaluationContext,
    EvaluationFingerprint,
)
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.geometry import Geometry
from grafix.core.layer import LayerStyleDefaults
from grafix.core.pipeline import realize_scene
from grafix.core.realize import GeometryCacheKey, RealizeSession
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.runtime_limits import RuntimeLimits
from grafix.core.runtime_config import RuntimeConfig
from grafix.devtools.benchmarks.definition import CaseDefinition, define_case
from grafix.devtools.benchmarks.metrics import (
    cache_metrics,
    counter_metric,
    gauge_metric,
    summarize_nanoseconds,
)
from grafix.devtools.benchmarks.schema import BenchmarkOutput, evaluate_contract
from grafix.interactive.gl import draw_renderer as renderer_module
from grafix.interactive.gl.draw_renderer import DrawRenderer
from grafix.interactive.gl.index_buffer import (
    LineIndexStats,
    build_line_indices_and_stats,
)

_RENDERER_SOURCE_FILE = Path(__file__)


def case_definitions() -> tuple[CaseDefinition, ...]:
    """Static/animated/multi-layer renderer cases を返す。"""

    return (
        define_case(
            "system.animated_soak",
            "RealizeSession animated soak",
            category="system",
            suite="system",
            fixture="animated_soak",
            parameters={"workload": "animated_soak", "frames": 48, "sides": 48},
            tags=("system-diagnostic",),
            selectable_suites=("system",),
            setup=setup_animated_soak,
            workload=workload_animated_soak,
            support_source_files=(_RENDERER_SOURCE_FILE,),
        ),
        define_case(
            "pipeline.draw_realize_indices.small",
            "draw → realize → indices",
            category="pipeline",
            suite="pipeline",
            fixture="grid_24",
            parameters={"grid_size": 24},
            tags=("end-to-end", "cpu"),
            selectable_suites=("pipeline",),
            setup=setup_draw_realize_indices,
            workload=workload_draw_realize_indices,
        ),
        define_case(
            "interactive.renderer.static_100k",
            "renderer static topology cache",
            category="interactive",
            suite="interactive",
            fixture="100k_two_point_lines",
            parameters={"polylines": 100_000, "frames": 8},
            tags=("renderer", "static", "fake-gl"),
            selectable_suites=("interactive",),
            setup=setup_renderer,
            workload=workload_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
            self_sampling=True,
        ),
        define_case(
            "interactive.renderer.animated_coords_static_offsets_100k",
            "renderer animated coordinates / static offsets",
            category="interactive",
            suite="interactive",
            fixture="100k_two_point_lines",
            parameters={"polylines": 100_000, "frames": 12, "topology": "static"},
            tags=("renderer", "animated-coordinates", "static-topology", "fake-gl"),
            selectable_suites=("interactive",),
            setup=setup_animated_renderer,
            workload=workload_animated_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
        ),
        define_case(
            "interactive.renderer.animated_topology_100k",
            "renderer animated topology",
            category="interactive",
            suite="interactive",
            fixture="100k_two_point_lines",
            parameters={"polylines": 100_000, "frames": 12, "topology": "animated"},
            tags=("renderer", "animated-topology", "fake-gl"),
            selectable_suites=("interactive",),
            setup=setup_animated_renderer,
            workload=workload_animated_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
        ),
        define_case(
            "interactive.renderer.static_1m",
            "renderer static topology cache / 1M lines",
            category="interactive",
            suite="interactive",
            fixture="1m_two_point_lines",
            parameters={"polylines": 1_000_000, "frames": 3},
            tags=("renderer", "static", "fake-gl", "large"),
            selectable_suites=("soak",),
            setup=setup_renderer,
            workload=workload_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
            self_sampling=True,
        ),
        define_case(
            "interactive.renderer.animated_coords_static_offsets_1m",
            "renderer animated coordinates / static offsets / 1M lines",
            category="interactive",
            suite="interactive",
            fixture="1m_two_point_lines",
            parameters={"polylines": 1_000_000, "frames": 3, "topology": "static"},
            tags=("renderer", "animated-coordinates", "static-topology", "fake-gl", "large"),
            selectable_suites=("soak",),
            setup=setup_animated_renderer,
            workload=workload_animated_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
        ),
        define_case(
            "interactive.renderer.animated_topology_1m",
            "renderer animated topology / 1M lines",
            category="interactive",
            suite="interactive",
            fixture="1m_two_point_lines",
            parameters={"polylines": 1_000_000, "frames": 3, "topology": "animated"},
            tags=("renderer", "animated-topology", "fake-gl", "large"),
            selectable_suites=("soak",),
            setup=setup_animated_renderer,
            workload=workload_animated_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
        ),
        *_multilayer_renderer_definitions(),
    )


def setup_animated_soak(parameters: dict[str, Any], _seed: int) -> object:
    """Animated soak の fixed parameters を materialize する。"""

    return dict(parameters)


def _multilayer_renderer_definitions() -> list[CaseDefinition]:
    """1/8/100 animated layer と changing-topology control を返す。"""

    definitions = [
        define_case(
            f"interactive.renderer.multilayer.stable_offsets.layers_{layers}",
            f"renderer multi-layer stable offsets / {layers} layers",
            category="interactive",
            suite="interactive",
            fixture="animated_multilayer_lines",
            parameters={
                "layers": layers,
                "frames": 12,
                "polylines": 128,
                "stable_topology": True,
            },
            tags=(
                "renderer",
                "multi-layer",
                "animated-coordinates",
                "static-topology",
                "fake-gl",
            ),
            selectable_suites=suites,
            setup=setup_multilayer_renderer,
            workload=workload_multilayer_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
            self_sampling=True,
        )
        for layers, suites in (
            (1, ("interactive",)),
            (8, ("interactive",)),
            (100, ("soak",)),
        )
    ]
    definitions.append(
        define_case(
            "interactive.renderer.multilayer.changing_topology.layers_8",
            "renderer multi-layer changing topology / 8 layers",
            category="interactive",
            suite="interactive",
            fixture="animated_multilayer_lines",
            parameters={
                "layers": 8,
                "frames": 12,
                "polylines": 128,
                "stable_topology": False,
            },
            tags=(
                "renderer",
                "multi-layer",
                "animated-coordinates",
                "animated-topology",
                "fake-gl",
                "control",
            ),
            selectable_suites=("interactive",),
            setup=setup_multilayer_renderer,
            workload=workload_multilayer_renderer,
            support_source_files=(_RENDERER_SOURCE_FILE,),
            self_sampling=True,
        )
    )
    return definitions


def _semantic_frame_values(value: object) -> list[dict[str, np.ndarray]]:
    """renderer の typed frame tuple を checksum 用 JSON list へ変換する。"""

    frames = cast(tuple[tuple[np.ndarray, np.ndarray], ...], value)
    return [{"vertices": vertices, "indices": indices} for vertices, indices in frames]


def setup_renderer(parameters: dict[str, Any], _seed: int) -> object:
    return (
        renderer_geometry(polylines=int(parameters["polylines"])),
        int(parameters["frames"]),
    )


def workload_renderer(state: object) -> BenchmarkOutput:
    geometry, frames = cast(tuple[RealizedGeometry, int], state)
    payload = renderer_cache_workload(
        geometry,
        frames=frames,
        include_semantic_frames=True,
    )
    semantic_frames = _semantic_frame_values(payload.pop("_semantic_frames"))
    output = cast(dict[str, Any], payload["output"])
    metrics = (
        *(
            counter_metric(
                name,
                int(output[name]),
                unit="count",
                phase="measure",
                scope="renderer",
            )
            for name in (
                "n_vertices",
                "n_lines",
                "frames",
                "index_count",
                "index_builds",
                "uploads",
            )
        ),
        *(
            counter_metric(
                name,
                int(output[name]),
                unit="bytes",
                phase="measure",
                scope="renderer",
            )
            for name in (
                "bytes",
                "full_vertex_upload_bytes",
                "full_index_upload_bytes",
                "vertex_only_upload_bytes",
            )
        ),
        gauge_metric(
            "steady_median_ms",
            float(output["steady_median_ms"]),
            unit="ms",
            phase="measure",
            scope="renderer",
        ),
        gauge_metric(
            "steady_p95_ms",
            float(output["steady_p95_ms"]),
            unit="ms",
            phase="measure",
            scope="renderer",
        ),
        *cache_metrics(
            cast(dict[str, Any], payload["cache"]),
            name="cache",
            phase="measure",
            scope="renderer",
        ),
    )
    return BenchmarkOutput(value=semantic_frames, metrics=metrics)


def setup_animated_renderer(parameters: dict[str, Any], _seed: int) -> object:
    base = renderer_geometry(polylines=int(parameters["polylines"]))
    geometries: list[RealizedGeometry] = []
    static_topology = str(parameters["topology"]) == "static"
    for frame in range(int(parameters["frames"])):
        coords = base.coords.copy()
        coords[:, 1] = np.float32(frame) * np.float32(0.001)
        offsets = (
            base.offsets
            if static_topology
            else changing_renderer_offsets(base.offsets, frame=frame)
        )
        geometries.append(RealizedGeometry(coords=coords, offsets=offsets))
    return tuple(geometries)


def workload_animated_renderer(state: object) -> BenchmarkOutput:
    geometries = cast(tuple[RealizedGeometry, ...], state)
    BenchmarkFakeMesh.instances.clear()
    renderer = fake_renderer()
    original_build = renderer_module.build_line_indices_and_stats
    index_builds = 0
    semantic_frames: list[dict[str, np.ndarray]] = []

    def counted_build(offsets: np.ndarray) -> Any:
        nonlocal index_builds
        index_builds += 1
        return original_build(offsets)

    with (
        patch.object(
            renderer_module,
            "LineMesh",
            BenchmarkFakeMesh,
        ),
        patch.object(
            renderer_module,
            "build_line_indices_and_stats",
            counted_build,
        ),
    ):
        for frame, geometry in enumerate(geometries):
            mesh, _stats = renderer.prepare_layer_mesh(
                geometry,
                cache_key=renderer_cache_key(
                    "renderer-animated",
                    frame + 1,
                ),
                scene_serial=frame + 1,
                snapshot_revision=frame + 1,
            )
            if mesh is None:
                raise RuntimeError("renderer benchmark returned an empty mesh")
            benchmark_mesh = cast(
                BenchmarkFakeMesh,
                mesh,
            )
            if benchmark_mesh.last_vertices is None or benchmark_mesh.last_indices is None:
                raise RuntimeError("renderer benchmark mesh upload state is missing")
            semantic_frames.append(
                {
                    "vertices": benchmark_mesh.last_vertices,
                    "indices": benchmark_mesh.last_indices,
                }
            )

    meshes = BenchmarkFakeMesh.instances
    output = {
        "frames": len(geometries),
        "n_lines": int(geometries[0].offsets.size - 1),
        "index_builds": index_builds,
        "full_uploads": sum(mesh.upload_count for mesh in meshes),
        "vertex_only_uploads": sum(mesh.vertex_upload_count for mesh in meshes),
        "full_vertex_upload_bytes": sum(mesh.full_vertex_upload_bytes for mesh in meshes),
        "full_index_upload_bytes": sum(mesh.full_index_upload_bytes for mesh in meshes),
        "vertex_only_upload_bytes": sum(mesh.vertex_only_upload_bytes for mesh in meshes),
        "candidate_entries": len(renderer._mesh_candidates),
    }
    metrics = (
        *(
            counter_metric(
                name,
                int(output[name]),
                unit="count",
                phase="measure",
                scope="renderer",
            )
            for name in (
                "frames",
                "n_lines",
                "index_builds",
                "full_uploads",
                "vertex_only_uploads",
                "candidate_entries",
            )
        ),
        *(
            counter_metric(
                name,
                int(output[name]),
                unit="bytes",
                phase="measure",
                scope="renderer",
            )
            for name in (
                "full_vertex_upload_bytes",
                "full_index_upload_bytes",
                "vertex_only_upload_bytes",
            )
        ),
    )
    return BenchmarkOutput(value=semantic_frames, metrics=metrics)


def setup_multilayer_renderer(
    parameters: dict[str, Any],
    _seed: int,
) -> object:
    return dict(parameters)


def workload_multilayer_renderer(state: object) -> BenchmarkOutput:
    parameters = cast(dict[str, Any], state)
    layers = int(parameters["layers"])
    frames = int(parameters["frames"])
    stable_topology = bool(parameters["stable_topology"])
    payload = renderer_multilayer_dynamic_workload(
        layers=layers,
        frames=frames,
        polylines=int(parameters["polylines"]),
        stable_topology=stable_topology,
        include_semantic_frames=True,
    )
    semantic_frames = _semantic_frame_values(payload.pop("_semantic_frames"))
    output = cast(dict[str, Any], payload["output"])
    expected_rebuilds = layers if stable_topology else layers * frames
    expected_vertex_updates = layers * (frames - 1) if stable_topology else 0
    contracts = (
        evaluate_contract(
            contract_id="renderer.multilayer.index_builds",
            severity="hard",
            actual=int(output["index_builds"]),
            comparator="eq",
            limit=expected_rebuilds,
            reason="stable topology は layer ごとの warmup 後に再構築しない",
        ),
        evaluate_contract(
            contract_id="renderer.multilayer.vertex_only_updates",
            severity="hard",
            actual=int(output["vertex_only_uploads"]),
            comparator="eq",
            limit=expected_vertex_updates,
            reason="stable topology の後続 frame は VBO だけを更新する",
        ),
        evaluate_contract(
            contract_id="renderer.multilayer.dynamic_entry_bound",
            severity="hard",
            actual=int(output["dynamic_entries"]),
            comparator="le",
            limit=int(output["dynamic_entry_limit"]),
            reason="animated mesh pool の GL object 数を entry 上限内に保つ",
        ),
        evaluate_contract(
            contract_id="renderer.multilayer.dynamic_byte_bound",
            severity="hard",
            actual=int(output["dynamic_bytes"]),
            comparator="le",
            limit=int(output["dynamic_byte_limit"]),
            reason="animated mesh pool を byte 上限内に保つ",
        ),
    )
    cache = cast(dict[str, Any], payload["cache"])
    metrics = (
        *(
            counter_metric(
                name,
                int(output[name]),
                unit="count",
                phase="measure",
                scope="renderer",
            )
            for name in (
                "layers",
                "frames",
                "polylines_per_layer",
                "index_builds",
                "full_uploads",
                "vertex_only_uploads",
                "dynamic_entries",
                "dynamic_entry_limit",
                "candidate_entries",
                "candidate_entry_limit",
            )
        ),
        gauge_metric(
            "stable_topology",
            bool(output["stable_topology"]),
            unit="boolean",
            phase="measure",
            scope="renderer",
        ),
        counter_metric(
            "dynamic_bytes",
            int(output["dynamic_bytes"]),
            unit="bytes",
            phase="measure",
            scope="renderer",
        ),
        counter_metric(
            "dynamic_byte_limit",
            int(output["dynamic_byte_limit"]),
            unit="bytes",
            phase="measure",
            scope="renderer",
        ),
        *cache_metrics(
            cache,
            name="cache",
            phase="measure",
            scope="renderer",
        ),
    )
    return BenchmarkOutput(
        value=semantic_frames,
        metrics=metrics,
        contracts=contracts,
    )


@dataclass(frozen=True, slots=True)
class _DrawRealizeIndicesState:
    """measurement 外で固定した pipeline evaluation generation。"""

    grid_size: int
    config: RuntimeConfig
    context: EvaluationContext


def setup_draw_realize_indices(
    parameters: dict[str, Any],
    _seed: int,
) -> _DrawRealizeIndicesState:
    """pure config discovery と immutable context 構築を setup で一度だけ行う。"""

    from grafix.core.operation_catalog import current_operation_catalog
    from grafix.runtime_config_loader import runtime_config

    config = runtime_config()
    return _DrawRealizeIndicesState(
        grid_size=int(parameters["grid_size"]),
        config=config,
        context=EvaluationContext(
            catalog=current_operation_catalog(),
            quality="final",
            config=EvaluationConfig(font_dirs=config.font_dirs),
        ),
    )


def workload_draw_realize_indices(state: object) -> BenchmarkOutput:
    from grafix.core.layer import LayerStyleDefaults
    from grafix.core.pipeline import realize_scene
    from grafix.core.realize import RealizeSession
    from grafix.interactive.gl.index_buffer import build_line_indices_and_stats

    if type(state) is not _DrawRealizeIndicesState:
        raise TypeError("draw/realize benchmark state is invalid")
    size = state.grid_size

    def draw(_t: float) -> Geometry:
        base = Geometry.create(
            "grid",
            params={"activate": True, "nx": size, "ny": size, "scale": 100.0},
        )
        return Geometry.create(
            "rotate",
            inputs=(base,),
            params={"activate": True, "rotation": (0.0, 0.0, 17.0)},
        )

    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    with RealizeSession(context=state.context) as session:
        layers = realize_scene(
            draw,
            0.0,
            defaults,
            config=state.config,
            session=session,
        )
        cache = session.stats()
    realized = layers[0].realized
    indices, draw_stats = build_line_indices_and_stats(realized.offsets)
    metrics = (
        *(
            counter_metric(
                name,
                value,
                unit="count",
                phase="measure",
                scope="renderer",
            )
            for name, value in (
                ("layers", len(layers)),
                ("n_vertices", int(realized.coords.shape[0])),
                ("n_lines", int(realized.offsets.size - 1)),
                ("index_count", int(indices.size)),
                ("draw_vertices", int(draw_stats.draw_vertices)),
                ("draw_lines", int(draw_stats.draw_lines)),
            )
        ),
        counter_metric(
            "geometry_bytes",
            int(realized.byte_size),
            unit="bytes",
            phase="measure",
            scope="renderer",
        ),
        counter_metric(
            "index_bytes",
            int(indices.nbytes),
            unit="bytes",
            phase="measure",
            scope="renderer",
        ),
        *cache_metrics(
            {
                "hits": cache.hits,
                "misses": cache.misses,
                "evictions": cache.evictions,
                "entries": cache.entries,
                "bytes": cache.bytes,
            },
            name="cache",
            phase="measure",
            scope="renderer",
        ),
    )
    return BenchmarkOutput(
        value={
            "coords": realized.coords,
            "offsets": realized.offsets,
            "indices": indices,
        },
        metrics=metrics,
    )


@dataclass(slots=True)
class _FakeBuffer:
    size: int


class BenchmarkFakeMesh:
    """GL を使わず upload 回数と予約 byte 数だけを再現する mesh。"""

    instances: list[BenchmarkFakeMesh] = []

    def __init__(
        self,
        ctx: object,
        program: object,
        initial_reserve: int = 4_096,
    ) -> None:
        del ctx, program
        self.vbo = _FakeBuffer(size=int(initial_reserve))
        self.ibo = _FakeBuffer(size=int(initial_reserve))
        self.upload_count = 0
        self.vertex_upload_count = 0
        self.full_vertex_upload_bytes = 0
        self.full_index_upload_bytes = 0
        self.vertex_only_upload_bytes = 0
        self.last_vertices: np.ndarray | None = None
        self.last_indices: np.ndarray | None = None
        self.released = False
        self.instances.append(self)

    def upload(self, vertices: np.ndarray, indices: np.ndarray) -> None:
        vertices_f32 = np.ascontiguousarray(vertices, dtype=np.float32)
        indices_u32 = np.ascontiguousarray(indices, dtype=np.uint32)
        self.upload_count += 1
        self.full_vertex_upload_bytes += int(vertices_f32.nbytes)
        self.full_index_upload_bytes += int(indices_u32.nbytes)
        self.last_vertices = vertices_f32
        self.last_indices = indices_u32
        self.vbo.size = max(self.vbo.size, int(vertices_f32.nbytes))
        self.ibo.size = max(self.ibo.size, int(indices_u32.nbytes))

    def upload_vertices(self, vertices: np.ndarray) -> None:
        """scratch topology 再利用時の VBO-only upload を再現する。"""

        vertices_f32 = np.ascontiguousarray(vertices, dtype=np.float32)
        self.vertex_upload_count += 1
        self.vertex_only_upload_bytes += int(vertices_f32.nbytes)
        self.last_vertices = vertices_f32
        self.vbo.size = max(self.vbo.size, int(vertices_f32.nbytes))

    def release(self) -> None:
        self.released = True


class _BenchmarkFakeUniform:
    def __init__(self) -> None:
        self.value: object | None = None

    def write(self, _value: bytes) -> None:
        return None


class _BenchmarkFakeProgram(dict[str, _BenchmarkFakeUniform]):
    def release(self) -> None:
        return None


class _BenchmarkFakeWindow:
    def switch_to(self) -> None:
        return None


def _draw_geometry(*, frame: int, sides: int) -> Geometry:
    base = Geometry.create(
        "polygon",
        params={"activate": True, "n_sides": int(sides), "scale": 20.0},
    )
    angle = float(int(frame)) * 0.125
    return Geometry.create(
        "rotate",
        inputs=(base,),
        params={
            "activate": True,
            "rotation": (0.0, 0.0, angle),
            "auto_center": False,
            "pivot": (0.0, 0.0, 0.0),
        },
    )


def animated_soak(*, frames: int, sides: int) -> dict[str, Any]:
    estimated_bytes = (int(sides) + 1) * 3 * np.dtype(np.float32).itemsize + 2 * np.dtype(
        np.int32
    ).itemsize
    cache_limit = max(1_024, 2 * int(estimated_bytes) + 64)
    last: RealizedGeometry | None = None
    with RealizeSession(runtime_limits=RuntimeLimits(cpu_cache_bytes=cache_limit)) as session:
        for frame in range(max(1, int(frames))):
            last = session.realize(_draw_geometry(frame=frame, sides=int(sides)))
        stats = session.stats()

    assert last is not None
    return {
        "output": {
            **_describe_realized(last),
            "frames": max(1, int(frames)),
            "unique_geometry_ids": max(1, int(frames)),
            "static_base_hits": max(0, int(frames) - 1),
        },
        "cache": {
            "hits": stats.hits,
            "misses": stats.misses,
            "evictions": stats.evictions,
            "entries": stats.entries,
            "bytes": stats.bytes,
            "budget_bytes": cache_limit,
        },
    }


def draw_realize_indices(*, grid_size: int) -> dict[str, Any]:
    from grafix.core.operation_catalog import current_operation_catalog
    from grafix.runtime_config_loader import runtime_config

    size = max(1, int(grid_size))

    def draw(_t: float) -> Geometry:
        base = Geometry.create(
            "grid",
            params={"activate": True, "nx": size, "ny": size, "scale": 100.0},
        )
        return Geometry.create(
            "rotate",
            inputs=(base,),
            params={"activate": True, "rotation": (0.0, 0.0, 17.0)},
        )

    defaults = LayerStyleDefaults(color=(0.0, 0.0, 0.0), thickness=0.01)
    config = runtime_config()
    context = EvaluationContext(
        catalog=current_operation_catalog(),
        quality="final",
        config=EvaluationConfig(font_dirs=config.font_dirs),
    )
    with RealizeSession(context=context) as session:
        layers = realize_scene(
            draw,
            0.0,
            defaults,
            config=config,
            session=session,
        )
        cache = session.stats()
    realized = layers[0].realized
    indices, draw_stats = build_line_indices_and_stats(realized.offsets)
    return {
        "output": {
            **_describe_realized(realized),
            "layers": len(layers),
            "index_count": int(indices.size),
            "index_bytes": int(indices.nbytes),
            "draw_vertices": draw_stats.draw_vertices,
            "draw_lines": draw_stats.draw_lines,
        },
        "cache": {
            "hits": cache.hits,
            "misses": cache.misses,
            "evictions": cache.evictions,
            "entries": cache.entries,
            "bytes": cache.bytes,
        },
    }


def renderer_cache_key(namespace: str, revision: int) -> GeometryCacheKey:
    """renderer benchmark 用の deterministic typed cache key を返す。"""

    return GeometryCacheKey(
        geometry_id=namespace,
        evaluation=EvaluationFingerprint(f"{revision:064x}"),
        external_dependencies=EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
    )


def renderer_geometry(*, polylines: int) -> RealizedGeometry:
    line_count = max(1, int(polylines))
    coords = np.zeros((line_count * 2, 3), dtype=np.float32)
    coords[:, 0] = np.arange(line_count * 2, dtype=np.float32)
    offsets = np.arange(0, line_count * 2 + 1, 2, dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def changing_renderer_offsets(offsets: np.ndarray, *, frame: int) -> np.ndarray:
    """空 polyline 境界を切り替え、描画結果を保ったまま topology を変える。"""

    if frame % 2 == 0:
        return offsets.copy()
    return np.insert(offsets, 1, offsets[1])


def fake_renderer() -> DrawRenderer:
    """GL resource constructorだけをfakeにし、rendererを正式初期化する。"""

    context = object()
    program = _BenchmarkFakeProgram(
        {
            "viewport_size": _BenchmarkFakeUniform(),
            "line_width_px": _BenchmarkFakeUniform(),
            "color": _BenchmarkFakeUniform(),
            "projection": _BenchmarkFakeUniform(),
        }
    )
    with (
        patch.object(
            renderer_module.moderngl,
            "create_context",
            return_value=context,
        ),
        patch.object(
            renderer_module.Shader,
            "create_shader",
            return_value=program,
        ),
        patch.object(renderer_module, "LineMesh", BenchmarkFakeMesh),
    ):
        return DrawRenderer(
            cast(Any, _BenchmarkFakeWindow()),
            RenderOptions(),
        )


def renderer_cache_workload(
    geometry: RealizedGeometry,
    *,
    frames: int,
    include_semantic_frames: bool = False,
) -> dict[str, Any]:
    """fake mesh で candidate→昇格→steady cache hit を計測する。"""

    frame_count = max(3, int(frames))
    BenchmarkFakeMesh.instances.clear()
    renderer = fake_renderer()
    cache_key = renderer_cache_key("renderer-benchmark", 1)
    original_build = renderer_module.build_line_indices_and_stats
    index_builds = 0
    cache_hits = 0
    cache_misses = 0

    def counted_build(
        offsets: np.ndarray,
    ) -> tuple[np.ndarray, LineIndexStats]:
        nonlocal index_builds
        index_builds += 1
        return original_build(offsets)

    steady_samples: list[int] = []
    semantic_frames: list[tuple[np.ndarray, np.ndarray]] = []
    stats = None
    with (
        patch.object(renderer_module, "LineMesh", BenchmarkFakeMesh),
        patch.object(
            renderer_module,
            "build_line_indices_and_stats",
            counted_build,
        ),
    ):
        for frame in range(frame_count):
            cached_before = cache_key in renderer._mesh_cache
            started = time.perf_counter_ns()
            mesh, stats = renderer.prepare_layer_mesh(
                geometry,
                cache_key=cache_key,
                scene_serial=frame + 1,
                snapshot_revision=1,
            )
            elapsed = time.perf_counter_ns() - started
            if mesh is None:
                raise RuntimeError("renderer benchmark が空 mesh を返した")
            benchmark_mesh = cast(BenchmarkFakeMesh, mesh)
            if include_semantic_frames:
                if benchmark_mesh.last_vertices is None or benchmark_mesh.last_indices is None:
                    raise RuntimeError("renderer benchmark mesh upload state is missing")
                semantic_frames.append(
                    (
                        benchmark_mesh.last_vertices,
                        benchmark_mesh.last_indices,
                    )
                )
            cache_hits += int(cached_before)
            cache_misses += int(not cached_before)
            if frame >= 2:
                steady_samples.append(elapsed)

    if stats is None:
        raise RuntimeError("renderer benchmark の stats が未生成")
    uploads = sum(mesh.upload_count for mesh in BenchmarkFakeMesh.instances)
    full_vertex_upload_bytes = sum(
        mesh.full_vertex_upload_bytes for mesh in BenchmarkFakeMesh.instances
    )
    full_index_upload_bytes = sum(
        mesh.full_index_upload_bytes for mesh in BenchmarkFakeMesh.instances
    )
    vertex_only_upload_bytes = sum(
        mesh.vertex_only_upload_bytes for mesh in BenchmarkFakeMesh.instances
    )
    steady = summarize_nanoseconds(steady_samples)
    result: dict[str, Any] = {
        "output": {
            **_describe_realized(geometry),
            "frames": frame_count,
            "index_count": stats.draw_vertices + max(0, stats.draw_lines - 1),
            "index_builds": index_builds,
            "uploads": uploads,
            "full_vertex_upload_bytes": full_vertex_upload_bytes,
            "full_index_upload_bytes": full_index_upload_bytes,
            "vertex_only_upload_bytes": vertex_only_upload_bytes,
            "steady_median_ms": steady["median_ms"],
            "steady_p95_ms": steady["p95_ms"],
        },
        "cache": {
            "hits": cache_hits,
            "misses": cache_misses,
            "evictions": 0,
            "entries": len(renderer._mesh_cache),
            "candidate_entries": len(renderer._mesh_candidates),
            "bytes": int(renderer._mesh_cache_bytes),
            "budget_bytes": int(renderer._mesh_cache_max_bytes),
        },
    }
    if include_semantic_frames:
        result["_semantic_frames"] = tuple(semantic_frames)
    return result


def renderer_multilayer_dynamic_workload(
    *,
    layers: int,
    frames: int,
    polylines: int,
    stable_topology: bool,
    include_semantic_frames: bool = False,
) -> dict[str, Any]:
    """複数 animated layer の slot 別 topology 再利用を fake GL で測る。"""

    layer_count = max(1, int(layers))
    frame_count = max(2, int(frames))
    base = renderer_geometry(polylines=max(1, int(polylines)))
    BenchmarkFakeMesh.instances.clear()
    renderer = fake_renderer()
    original_build = renderer_module.build_line_indices_and_stats
    index_builds = 0
    semantic_frames: list[tuple[np.ndarray, np.ndarray]] = []

    def counted_build(
        offsets: np.ndarray,
    ) -> tuple[np.ndarray, LineIndexStats]:
        nonlocal index_builds
        index_builds += 1
        return original_build(offsets)

    with (
        patch.object(renderer_module, "LineMesh", BenchmarkFakeMesh),
        patch.object(
            renderer_module,
            "build_line_indices_and_stats",
            counted_build,
        ),
    ):
        for frame in range(frame_count):
            for layer_index in range(layer_count):
                coords = base.coords.copy()
                coords[:, 1] = np.float32(frame * layer_count + layer_index) * np.float32(0.001)
                offsets = (
                    base.offsets
                    if stable_topology
                    else changing_renderer_offsets(base.offsets, frame=frame)
                )
                geometry = RealizedGeometry(coords=coords, offsets=offsets)
                mesh, _stats = renderer.prepare_layer_mesh(
                    geometry,
                    cache_key=renderer_cache_key(
                        "renderer-multilayer",
                        frame * layer_count + layer_index + 1,
                    ),
                    scene_serial=frame + 1,
                    snapshot_revision=frame + 1,
                    dynamic_slot=layer_index,
                )
                if mesh is None:
                    raise RuntimeError("multi-layer renderer benchmark returned an empty mesh")
                benchmark_mesh = cast(BenchmarkFakeMesh, mesh)
                if include_semantic_frames:
                    if benchmark_mesh.last_vertices is None or benchmark_mesh.last_indices is None:
                        raise RuntimeError("multi-layer renderer upload state is missing")
                    semantic_frames.append(
                        (
                            benchmark_mesh.last_vertices.copy(),
                            benchmark_mesh.last_indices.copy(),
                        )
                    )

    meshes = BenchmarkFakeMesh.instances
    output: dict[str, Any] = {
        "output": {
            "layers": layer_count,
            "frames": frame_count,
            "polylines_per_layer": max(1, int(polylines)),
            "stable_topology": bool(stable_topology),
            "index_builds": index_builds,
            "full_uploads": sum(mesh.upload_count for mesh in meshes),
            "vertex_only_uploads": sum(mesh.vertex_upload_count for mesh in meshes),
            "dynamic_entries": len(renderer._dynamic_meshes),
            "dynamic_bytes": int(renderer._dynamic_mesh_bytes),
            "dynamic_entry_limit": int(renderer._dynamic_mesh_max_entries),
            "dynamic_byte_limit": int(renderer._dynamic_mesh_max_bytes),
            "candidate_entries": len(renderer._mesh_candidates),
            "candidate_entry_limit": int(renderer._mesh_candidates_max_entries),
        },
        "cache": {
            "hits": max(0, layer_count * frame_count - index_builds),
            "misses": index_builds,
            "evictions": 0,
            "entries": len(renderer._dynamic_meshes),
            "bytes": int(renderer._dynamic_mesh_bytes),
        },
    }
    if include_semantic_frames:
        output["_semantic_frames"] = tuple(semantic_frames)
    return output


def workload_animated_soak(state: object) -> BenchmarkOutput:
    values = cast(dict[str, Any], state)
    frames = int(values["frames"])
    sides = int(values["sides"])
    estimated_bytes = (sides + 1) * 3 * np.dtype(np.float32).itemsize + 8
    cache_limit = max(1024, 2 * estimated_bytes + 64)
    last: RealizedGeometry | None = None
    with RealizeSession(runtime_limits=RuntimeLimits(cpu_cache_bytes=cache_limit)) as session:
        for frame in range(frames):
            last = session.realize(_draw_geometry(frame=frame, sides=sides))
        stats = session.stats()
    if last is None:
        raise RuntimeError("animated soak returned no geometry")
    return BenchmarkOutput(
        value=last,
        metrics=(
            counter_metric("frames", frames, unit="count", phase="measure", scope="system"),
            counter_metric("cache.hits", stats.hits, unit="count", phase="measure", scope="system"),
            counter_metric(
                "cache.misses", stats.misses, unit="count", phase="measure", scope="system"
            ),
            counter_metric(
                "cache.evictions", stats.evictions, unit="count", phase="measure", scope="system"
            ),
            counter_metric(
                "cache.entries", stats.entries, unit="count", phase="measure", scope="system"
            ),
            counter_metric(
                "cache.bytes", stats.bytes, unit="bytes", phase="measure", scope="system"
            ),
            counter_metric(
                "cache.budget_bytes", cache_limit, unit="bytes", phase="measure", scope="system"
            ),
        ),
    )


def _describe_realized(geometry: RealizedGeometry) -> dict[str, int]:
    return {
        "n_vertices": int(geometry.coords.shape[0]),
        "n_lines": max(0, int(geometry.offsets.size) - 1),
        "bytes": int(geometry.coords.nbytes + geometry.offsets.nbytes),
    }


__all__ = [
    "case_definitions",
    "BenchmarkFakeMesh",
    "animated_soak",
    "draw_realize_indices",
    "fake_renderer",
    "renderer_cache_key",
    "renderer_cache_workload",
    "renderer_geometry",
    "renderer_multilayer_dynamic_workload",
]
