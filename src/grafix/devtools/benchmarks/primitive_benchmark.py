"""組み込み primitive の direct-call benchmark fixture と観測処理。"""

from __future__ import annotations

import hashlib
import importlib
import math
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.evaluation_context import (
    EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
    ExternalDependencySnapshot,
    bind_external_dependency,
)
from grafix.core.font_resources import FontResources, ResolvedFontLease
from grafix.core.geometry import normalize_args
from grafix.core.realized_geometry import GeomTuple, RealizedGeometry
from grafix.runtime_config_loader import runtime_config
from grafix.devtools.benchmarks.definition import CaseDefinition, define_case
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    Metric,
    evaluate_contract,
    freeze_json_object,
    materialize_json_object,
)

_FONT_SHA256 = "d930d5d52d15231c283089760f84584272ad5e37e14607ba0d19c798e7a9caec"
_POLYHEDRON_SHA256 = "416bb767cb68fe1e66ca16a1b8476ae9141922dc1720f314cf28f10392556d52"
_TEXT_EXTERNAL_DEPENDENCY_ID = "primitive-benchmark.text"


def case_definitions() -> tuple[CaseDefinition, ...]:
    """全組み込み primitive の direct raw actual-work case を返す。"""

    return tuple(
        define_case(
            case.case_id,
            case.label,
            category="primitive",
            suite="primitives",
            fixture=case.fixture,
            parameters=case.parameters(),
            tags=case.tags,
            selectable_suites=case.selectable_suites,
            setup=setup_primitive_benchmark,
            workload=run_raw_primitive,
            postprocess=observe_primitive_output,
            measurement_context=primitive_measurement_context,
            support_source_files=(Path(__file__),),
        )
        for case in primitive_benchmark_cases()
    )


@dataclass(frozen=True, slots=True)
class PrimitiveBenchmarkCase:
    """runner が ``CaseDefinition`` へ変換する primitive case 記述。"""

    case_id: str
    label: str
    primitive: str
    fixture: str
    arguments: Mapping[str, object]
    tags: tuple[str, ...] = ()
    selectable_suites: tuple[str, ...] = ("primitives",)
    input_fixture: str | None = None
    run_seed_argument: str | None = None
    asset: Mapping[str, object] | None = None
    work: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            freeze_json_object(self.arguments),
        )
        if self.asset is not None:
            object.__setattr__(self, "asset", freeze_json_object(self.asset))
        if self.work is not None:
            object.__setattr__(self, "work", freeze_json_object(self.work))

    def parameters(self) -> dict[str, Any]:
        """process 間で再構築できる JSON-compatible parameters を返す。"""

        parameters: dict[str, Any] = {
            "primitive": self.primitive,
            "arguments": materialize_json_object(freeze_json_object(self.arguments)),
        }
        if self.input_fixture is not None:
            parameters["input_fixture"] = self.input_fixture
        if self.run_seed_argument is not None:
            parameters["run_seed_argument"] = self.run_seed_argument
        if self.asset is not None:
            parameters["asset"] = materialize_json_object(freeze_json_object(self.asset))
        if self.work is not None:
            parameters["work"] = materialize_json_object(freeze_json_object(self.work))
        return parameters


@dataclass(slots=True)
class PrimitiveBenchmarkState:
    """setup 済み raw callable と固定引数。"""

    primitive: str
    raw_function: Callable[..., GeomTuple]
    arguments: dict[str, Any]
    input_points: int | None
    input_sha256: str | None
    asset_sha256: str | None
    asset_bytes: int | None
    work: dict[str, int | float | str | bool]
    font_resources: FontResources | None
    font_lease: ResolvedFontLease | None


def primitive_benchmark_cases() -> tuple[PrimitiveBenchmarkCase, ...]:
    """全22組み込み primitive の actual-work case を返す。"""

    center = [7.0, -11.0, 3.0]
    primary = (
        PrimitiveBenchmarkCase(
            "primitive.arc.segments_512",
            "arc / 512 segments",
            "arc",
            "segments_512",
            {
                "radius": 120.0,
                "start": 17.0,
                "sweep": -317.0,
                "segments": 512,
                "center": center,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.asemic.warm_repeated",
            "asemic / repeated glyphs",
            "asemic",
            "warm_repeated",
            {
                "text": "ABRACADABRA GRAFIX\nVECTOR PLOT ABRACADABRA",
                "n_nodes": 36,
                "candidates": 16,
                "stroke_min": 3,
                "stroke_max": 6,
                "walk_min_steps": 3,
                "walk_max_steps": 6,
                "stroke_style": "bezier",
                "bezier_samples": 16,
                "bezier_tension": 0.4,
                "text_align": "center",
                "letter_spacing_em": 0.05,
                "line_height": 1.3,
                "center": center,
                "scale": 40.0,
            },
            run_seed_argument="seed",
        ),
        PrimitiveBenchmarkCase(
            "primitive.bezier.3d_segments_512",
            "bezier / 3D / 512 segments",
            "bezier",
            "3d_segments_512",
            {
                "p0": [-100.0, 20.0, 3.0],
                "p1": [-30.0, 180.0, -40.0],
                "p2": [80.0, -160.0, 60.0],
                "p3": [150.0, 40.0, -7.0],
                "segments": 512,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.circle.segments_512",
            "circle / 512 segments",
            "circle",
            "segments_512",
            {"radius": 120.0, "segments": 512, "center": center},
        ),
        PrimitiveBenchmarkCase(
            "primitive.ellipse.eccentric_rotated_512",
            "ellipse / eccentric rotated / 512 segments",
            "ellipse",
            "eccentric_rotated_512",
            {
                "radius_x": 120.0,
                "radius_y": 20.0,
                "angle": 37.0,
                "segments": 512,
                "center": center,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.grid.500x500_transformed",
            "grid / 500 x 500 / transformed",
            "grid",
            "500x500_transformed",
            {"nx": 500, "ny": 500, "center": center, "scale": 180.0},
        ),
        PrimitiveBenchmarkCase(
            "primitive.laplace_field_grid.cylinder_dense",
            "laplace field grid / cylinder / dense",
            "laplace_field_grid",
            "cylinder_dense",
            {
                "preset": "cylinder_uniform",
                "u_min": -6.0,
                "u_max": 6.0,
                "v_min": -6.0,
                "v_max": 6.0,
                "n_u": 100,
                "n_v": 100,
                "samples": 1000,
                "center": center,
                "scale": 18.0,
                "rotate": 17.0,
                "clip": False,
                "a": 1.0,
                "U": 1.0,
                "gap": 0.002,
                "draw_boundary": True,
                "boundary_samples": 720,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.laplace_field_grid.mobius_dense_clip",
            "laplace field grid / mobius / dense clip",
            "laplace_field_grid",
            "mobius_dense_clip",
            {
                "preset": "mobius",
                "u_min": -4.0,
                "u_max": 4.0,
                "v_min": -4.0,
                "v_max": 4.0,
                "n_u": 100,
                "n_v": 100,
                "samples": 1000,
                "center": center,
                "scale": 12.0,
                "rotate": -23.0,
                "clip": True,
                "clip_xmin": -60.0,
                "clip_xmax": 60.0,
                "clip_ymin": -60.0,
                "clip_ymax": 60.0,
                "alpha_re": 1.0,
                "alpha_im": 0.15,
                "beta_re": 0.2,
                "beta_im": -0.1,
                "gamma_re": 0.08,
                "gamma_im": 0.03,
                "delta_re": 1.0,
                "delta_im": 0.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.laplace_field_grid.exp_dense",
            "laplace field grid / exponential / dense",
            "laplace_field_grid",
            "exp_dense",
            {
                "preset": "exp",
                "u_min": -3.0,
                "u_max": 3.0,
                "v_min": -3.0,
                "v_max": 3.0,
                "n_u": 100,
                "n_v": 100,
                "samples": 1000,
                "center": center,
                "scale": 10.0,
                "rotate": 11.0,
                "clip": True,
                "clip_xmin": -80.0,
                "clip_xmax": 80.0,
                "clip_ymin": -80.0,
                "clip_ymax": 80.0,
                "k_re": 0.35,
                "k_im": 0.2,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.line.rotated_right_anchor",
            "line / rotated / right anchor",
            "line",
            "rotated_right_anchor",
            {
                "center": center,
                "anchor": "right",
                "length": 200.0,
                "angle": 37.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.lissajous.samples_8000",
            "lissajous / 8,000 samples",
            "lissajous",
            "samples_8000",
            {
                "a": 19,
                "b": 17,
                "phase": 37.0,
                "samples": 8000,
                "turns": 7.25,
                "center": center,
                "scale": 180.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.lsystem.plant_iters_6_jitter",
            "L-system / plant / 6 iterations / jitter",
            "lsystem",
            "plant_iters_6_jitter",
            {
                "kind": "plant",
                "iters": 6,
                "center": center,
                "heading": 90.0,
                "angle": 25.0,
                "step": 6.0,
                "jitter": 0.08,
            },
            run_seed_argument="seed",
            work={
                "expanded_chars": 25_159,
                "draw_commands": 6_048,
                "branch_pushes": 4_095,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.polygon.sides_128_partial",
            "polygon / 128 sides / partial sweep",
            "polygon",
            "sides_128_partial",
            {
                "n_sides": 128,
                "phase": 17.0,
                "sweep": 305.5,
                "center": center,
                "scale": 200.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.polyhedron.truncated_icosidodecahedron",
            "polyhedron / truncated icosidodecahedron",
            "polyhedron",
            "truncated_icosidodecahedron",
            {
                "kind": "truncated_icosidodecahedron",
                "center": center,
                "scale": 80.0,
            },
            asset={"kind": "polyhedron", "sha256": _POLYHEDRON_SHA256},
        ),
        PrimitiveBenchmarkCase(
            "primitive.polyline.tuple_50k_closed",
            "polyline / immutable tuple / 50k points / closed",
            "polyline",
            "tuple_50k_closed",
            {"closed": True},
            input_fixture="wave_3d_tuple_50k",
        ),
        PrimitiveBenchmarkCase(
            "primitive.rect.rotated",
            "rect / rotated",
            "rect",
            "rotated",
            {
                "width": 200.0,
                "height": 120.0,
                "angle": 37.0,
                "center": center,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.sphere.latlon.sub5.both",
            "sphere / latlon / subdivisions 5 / both",
            "sphere",
            "latlon_sub5_both",
            {
                "subdivisions": 5,
                "style": "latlon",
                "line_mode": "both",
                "center": center,
                "scale": 120.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.sphere.rings.sub5.both",
            "sphere / rings / subdivisions 5 / both",
            "sphere",
            "rings_sub5_both",
            {
                "subdivisions": 5,
                "style": "rings",
                "line_mode": "both",
                "center": center,
                "scale": 120.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.sphere.icosphere.sub5",
            "sphere / icosphere / subdivisions 5",
            "sphere",
            "icosphere_sub5",
            {
                "subdivisions": 5,
                "style": "icosphere",
                "line_mode": "both",
                "center": center,
                "scale": 120.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.sphere.zigzag.sub5",
            "sphere / zigzag / subdivisions 5",
            "sphere",
            "zigzag_sub5",
            {
                "subdivisions": 5,
                "style": "zigzag",
                "line_mode": "both",
                "center": center,
                "scale": 120.0,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.spiral.samples_32000",
            "spiral / 32,000 samples",
            "spiral",
            "samples_32000",
            {
                "inner_radius": 5.0,
                "outer_radius": 120.0,
                "turns": -17.25,
                "phase": 37.0,
                "samples": 32_000,
                "center": center,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.spline.3d_256x64_closed",
            "spline / 3D / 256 anchors x 64 samples / closed",
            "spline",
            "3d_256x64_closed",
            {
                "closed": True,
                "tension": 0.15,
                "segments_per_span": 64,
            },
            input_fixture="spline_3d_tuple_256",
        ),
        PrimitiveBenchmarkCase(
            "primitive.text.warm_wrapped_mixed",
            "text / warm wrapped mixed glyphs",
            "text",
            "warm_wrapped_mixed",
            {
                "text": (
                    "GRAFIX BENCHMARK 高速化 VECTOR PLOT\n0123456789 ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                ),
                "font": "NotoSansJP-Regular.ttf",
                "font_index": 0,
                "text_align": "center",
                "letter_spacing_em": 0.03,
                "line_height": 1.25,
                "use_bounding_box": True,
                "box_width": 360.0,
                "box_height": 180.0,
                "show_bounding_box": False,
                "quality": 0.85,
                "center": center,
                "scale": 40.0,
            },
            asset={"kind": "font", "sha256": _FONT_SHA256},
        ),
        PrimitiveBenchmarkCase(
            "primitive.torus.256x256_transformed",
            "torus / 256 x 256 / transformed",
            "torus",
            "256x256_transformed",
            {
                "major_radius": 100.0,
                "minor_radius": 25.0,
                "major_segments": 256,
                "minor_segments": 256,
                "center": center,
                "scale": 1.25,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.wave.triangle_rotated_32000",
            "wave / triangle / rotated / 32,000 samples",
            "wave",
            "triangle_rotated_32000",
            {
                "kind": "triangle",
                "length": 240.0,
                "amplitude": 45.0,
                "cycles": -19.25,
                "phase": 37.0,
                "samples": 32_000,
                "angle": -23.0,
                "center": center,
            },
        ),
        PrimitiveBenchmarkCase(
            "primitive.topographic_contours.grid_81x101_levels_13",
            "topographic contours / 81 x 101 grid / 13 levels",
            "topographic_contours",
            "grid_81x101_levels_13",
            {
                "width": 80.0,
                "height": 100.0,
                "focus_count": 6,
                "focus_spread": 1.0,
                "level_count": 13,
                "field_warp": 1.0,
                "warp_frequency": 1.0,
                "phase": 37.0,
                "grid_pitch": 1.0,
                "center": center,
            },
            run_seed_argument="seed",
        ),
        PrimitiveBenchmarkCase(
            "primitive.delaunay.guard_2_sites_500_candidates_8",
            "delaunay / guard 2 / 500 nominal sites / 8 candidates",
            "delaunay",
            "guard_2_sites_500_candidates_8",
            {
                "width": 240.0,
                "height": 180.0,
                "site_count": 500,
                "candidates": 8,
                "guard_band": 2.0,
                "center": center,
            },
            run_seed_argument="seed",
        ),
    )
    cold_controls = (
        PrimitiveBenchmarkCase(
            "primitive.asemic.cold_unique_bezier",
            "asemic / unique glyphs / cold control",
            "asemic",
            "cold_unique_bezier",
            {
                "text": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                "n_nodes": 28,
                "candidates": 16,
                "stroke_min": 3,
                "stroke_max": 6,
                "walk_min_steps": 3,
                "walk_max_steps": 6,
                "stroke_style": "bezier",
                "bezier_samples": 16,
                "bezier_tension": 0.4,
                "center": center,
                "scale": 40.0,
            },
            tags=("cold-control", "numba"),
            selectable_suites=("primitive-cold",),
            run_seed_argument="seed",
        ),
        PrimitiveBenchmarkCase(
            "primitive.text.cold_unique_high_quality",
            "text / unique glyphs / high quality / cold control",
            "text",
            "cold_unique_high_quality",
            {
                "text": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789高速化",
                "font": "NotoSansJP-Regular.ttf",
                "font_index": 0,
                "quality": 1.0,
                "center": center,
                "scale": 40.0,
            },
            tags=("cold-control",),
            selectable_suites=("primitive-cold",),
            asset={"kind": "font", "sha256": _FONT_SHA256},
        ),
    )
    common_tags = ("actual-work", "direct-raw", "exact-checksum")
    return tuple(
        PrimitiveBenchmarkCase(
            case_id=case.case_id,
            label=case.label,
            primitive=case.primitive,
            fixture=case.fixture,
            arguments=case.arguments,
            tags=(*common_tags, *case.tags),
            selectable_suites=case.selectable_suites,
            input_fixture=case.input_fixture,
            run_seed_argument=case.run_seed_argument,
            asset=case.asset,
            work=case.work,
        )
        for case in (*primary, *cold_controls)
    )


def setup_primitive_benchmark(
    parameters: dict[str, Any],
    seed: int,
) -> PrimitiveBenchmarkState:
    """raw callable、動的入力、固定assetを timed区間外で準備する。"""

    primitive = str(parameters["primitive"])
    module = importlib.import_module(f"grafix.core.primitives.{primitive}")
    raw_function = getattr(module, primitive)
    if not callable(raw_function):
        raise TypeError(f"primitive raw function is not callable: {primitive}")

    arguments = dict(cast(dict[str, Any], parameters["arguments"]))
    input_points: int | None = None
    input_sha256: str | None = None
    input_fixture = parameters.get("input_fixture")
    if input_fixture is not None:
        points_array: np.ndarray
        if input_fixture == "wave_3d_tuple_50k":
            points_array = _wave_points_3d(n_points=50_000)
        elif input_fixture == "spline_3d_tuple_256":
            points_array = _spline_points_3d(n_points=256)
        else:
            raise ValueError(f"unknown primitive input fixture: {input_fixture!r}")
        points = tuple(tuple(float(component) for component in point) for point in points_array)
        arguments["points"] = points
        input_points = len(points)
        input_sha256 = _array_sha256(points_array)

    run_seed_argument = parameters.get("run_seed_argument")
    if run_seed_argument is not None:
        arguments[str(run_seed_argument)] = int(seed)

    asset_sha256: str | None = None
    asset_bytes: int | None = None
    asset = parameters.get("asset")
    if asset is not None:
        asset_spec = cast(dict[str, Any], asset)
        expected_sha256 = str(asset_spec["sha256"])
        asset_kind = str(asset_spec["kind"])
        if asset_kind == "font":
            module_path = Path(cast(str, module.__file__)).resolve()
            font_path = module_path.parents[2].joinpath(
                "resource",
                "font",
                "Noto_Sans_JP",
                "static",
                str(arguments["font"]),
            )
            blob = font_path.read_bytes()
            arguments["font"] = str(font_path)
        elif asset_kind == "polyhedron":
            data_dir = getattr(module, "_DATA_DIR")
            kind = str(arguments["kind"])
            blob = data_dir.joinpath(f"{kind}_vertices_list.npz").read_bytes()
        else:
            raise ValueError(f"unknown primitive benchmark asset: {asset_kind!r}")
        asset_sha256 = hashlib.sha256(blob).hexdigest()
        asset_bytes = len(blob)
        if asset_sha256 != expected_sha256:
            raise RuntimeError(
                f"{primitive} benchmark asset checksum differs: {asset_sha256} != {expected_sha256}"
            )

    arguments = dict(normalize_args(arguments))
    font_resources: FontResources | None = None
    font_lease: ResolvedFontLease | None = None
    if primitive == "text":
        font = arguments["font"]
        font_index = arguments["font_index"]
        if type(font) is not str or type(font_index) is not int:
            raise TypeError("text benchmark font/font_index must be canonical values")
        font_resources = FontResources()
        try:
            config = runtime_config()
            font_lease = font_resources.resolve(
                font,
                font_index,
                config=EvaluationConfig(font_dirs=config.font_dirs),
            )
        except BaseException:
            font_resources.close()
            raise

    return PrimitiveBenchmarkState(
        primitive=primitive,
        raw_function=cast(Callable[..., GeomTuple], raw_function),
        arguments=arguments,
        input_points=input_points,
        input_sha256=input_sha256,
        asset_sha256=asset_sha256,
        asset_bytes=asset_bytes,
        work=dict(
            cast(
                dict[str, int | float | str | bool],
                parameters.get("work", {}),
            )
        ),
        font_resources=font_resources,
        font_lease=font_lease,
    )


@contextmanager
def primitive_measurement_context(state: object) -> Iterator[object]:
    """text の preflight lease を raw call 群へ束縛し、最後に owner を閉じる。"""

    primitive_state = cast(PrimitiveBenchmarkState, state)
    owner = primitive_state.font_resources
    lease = primitive_state.font_lease
    if owner is None:
        if lease is not None:
            raise RuntimeError("font lease exists without its FontResources owner")
        yield None
        return
    if lease is None:
        owner.close()
        raise RuntimeError("text benchmark FontResources has no resolved lease")

    snapshot = ExternalDependencySnapshot(
        fingerprint=EMPTY_EXTERNAL_DEPENDENCIES_FINGERPRINT,
        leases={_TEXT_EXTERNAL_DEPENDENCY_ID: lease},
    )
    try:
        with bind_external_dependency(snapshot, _TEXT_EXTERNAL_DEPENDENCY_ID):
            yield None
    finally:
        owner.close()


def run_raw_primitive(state: object) -> object:
    """timed区間では対象の raw primitive だけを直接呼び出す。"""

    primitive_state = cast(PrimitiveBenchmarkState, state)
    return primitive_state.raw_function(**primitive_state.arguments)


def observe_primitive_output(
    state: object,
    output: object,
) -> BenchmarkOutput:
    """raw output のmetrics/checksum契約を timed区間外で構築する。"""

    from grafix.core.operation_diagnostics import operation_diagnostic_context
    from grafix.devtools.benchmarks.metrics import geometry_checksum

    primitive_state = cast(PrimitiveBenchmarkState, state)
    coords, offsets = _raw_arrays(output, primitive=primitive_state.primitive)
    raw_writable = bool(coords.flags.writeable and offsets.flags.writeable)
    raw_layout = bool(
        coords.dtype == np.float32
        and offsets.dtype == np.int32
        and coords.ndim == 2
        and coords.shape[1:] == (3,)
        and offsets.ndim == 1
    )
    offsets_valid = _offsets_are_valid(coords, offsets)
    finite = bool(np.all(np.isfinite(coords)))

    with operation_diagnostic_context() as diagnostic_buffer:
        repeated_output = primitive_state.raw_function(**primitive_state.arguments)
    repeated_coords, repeated_offsets = _raw_arrays(
        repeated_output,
        primitive=primitive_state.primitive,
    )
    repeated_writable = bool(repeated_coords.flags.writeable and repeated_offsets.flags.writeable)
    independent = bool(
        not np.shares_memory(coords, repeated_coords)
        and not np.shares_memory(offsets, repeated_offsets)
    )

    geometry = RealizedGeometry(coords=coords, offsets=offsets)
    repeated_geometry = RealizedGeometry(
        coords=repeated_coords,
        offsets=repeated_offsets,
    )
    checksum = geometry_checksum(geometry)
    repeated_checksum = geometry_checksum(repeated_geometry)
    diagnostics = diagnostic_buffer.snapshot()
    closed_lines = _closed_line_count(geometry)
    input_unchanged = bool(
        primitive_state.input_sha256 is None
        or _array_sha256(cast(np.ndarray, primitive_state.arguments["points"]))
        == primitive_state.input_sha256
    )

    metrics = [
        _metric("primitive", "gauge", "text", primitive_state.primitive),
        _metric(
            "quality",
            "gauge",
            "unitless",
            primitive_state.arguments.get("quality", "raw"),
        ),
        _metric(
            "n_vertices",
            "counter",
            "count",
            int(geometry.coords.shape[0]),
        ),
        _metric(
            "n_lines",
            "counter",
            "count",
            int(geometry.offsets.size - 1),
        ),
        _metric("closed_lines", "counter", "count", closed_lines),
        _metric("output_bytes", "counter", "bytes", geometry.byte_size),
        _metric("diagnostics", "counter", "count", len(diagnostics)),
    ]
    metrics.extend(_specific_metrics(primitive_state, geometry))

    prefix = f"primitive.{primitive_state.primitive}"
    contracts = (
        evaluate_contract(
            contract_id=f"{prefix}.exact_checksum",
            severity="hard",
            actual=checksum,
            comparator="eq",
            limit=repeated_checksum,
            reason="raw primitive geometry checksum remains exact and deterministic",
        ),
        evaluate_contract(
            contract_id=f"{prefix}.raw_layout",
            severity="hard",
            actual=raw_layout,
            comparator="eq",
            limit=True,
            reason="raw primitive keeps float32/int32 packed array layout",
        ),
        evaluate_contract(
            contract_id=f"{prefix}.offsets",
            severity="hard",
            actual=offsets_valid,
            comparator="eq",
            limit=True,
            reason="raw primitive offsets remain monotonic and cover all vertices",
        ),
        evaluate_contract(
            contract_id=f"{prefix}.finite",
            severity="hard",
            actual=finite,
            comparator="eq",
            limit=True,
            reason="actual-work fixture produces finite coordinates",
        ),
        evaluate_contract(
            contract_id=f"{prefix}.raw_writable",
            severity="hard",
            actual=bool(raw_writable and repeated_writable),
            comparator="eq",
            limit=True,
            reason="each raw primitive output remains writable",
        ),
        evaluate_contract(
            contract_id=f"{prefix}.raw_independent",
            severity="hard",
            actual=independent,
            comparator="eq",
            limit=True,
            reason="successive raw primitive outputs do not share array memory",
        ),
        evaluate_contract(
            contract_id=f"{prefix}.input_unchanged",
            severity="hard",
            actual=input_unchanged,
            comparator="eq",
            limit=True,
            reason="raw primitive leaves benchmark input arrays unchanged",
        ),
    )
    return BenchmarkOutput(
        value=geometry,
        metrics=tuple(metrics),
        contracts=contracts,
    )


def _raw_arrays(
    output: object,
    *,
    primitive: str,
) -> tuple[np.ndarray, np.ndarray]:
    if not isinstance(output, tuple) or len(output) != 2:
        raise TypeError(f"{primitive} raw output must be a 2-tuple")
    coords, offsets = output
    if not isinstance(coords, np.ndarray) or not isinstance(offsets, np.ndarray):
        raise TypeError(f"{primitive} raw output must contain numpy arrays")
    return coords, offsets


def _wave_points_3d(*, n_points: int) -> np.ndarray:
    count = max(2, int(n_points))
    t = np.linspace(0.0, 80.0, num=count, dtype=np.float64)
    points = np.empty((count, 3), dtype=np.float64)
    points[:, 0] = t
    points[:, 1] = 30.0 * np.sin(0.37 * t)
    points[:, 2] = 7.0 * np.cos(0.11 * t)
    return points


def _spline_points_3d(*, n_points: int) -> np.ndarray:
    count = max(2, int(n_points))
    t = np.linspace(0.0, 16.0 * np.pi, num=count, endpoint=False, dtype=np.float64)
    points = np.empty((count, 3), dtype=np.float64)
    radius = 80.0 + 12.0 * np.sin(0.37 * t)
    points[:, 0] = radius * np.cos(t)
    points[:, 1] = radius * np.sin(t)
    points[:, 2] = 20.0 * np.sin(0.11 * t)
    return points


def _array_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(contiguous.dtype.str.encode("ascii"))
    digest.update(repr(contiguous.shape).encode("ascii"))
    digest.update(memoryview(cast(Any, contiguous)).cast("B"))
    return digest.hexdigest()


def _offsets_are_valid(coords: np.ndarray, offsets: np.ndarray) -> bool:
    return bool(
        offsets.ndim == 1
        and offsets.size >= 1
        and int(offsets[0]) == 0
        and int(offsets[-1]) == int(coords.shape[0])
        and not np.any(np.diff(offsets) < 0)
    )


def _closed_line_count(geometry: RealizedGeometry) -> int:
    closed = 0
    for start, stop in zip(
        geometry.offsets[:-1],
        geometry.offsets[1:],
        strict=True,
    ):
        points = geometry.coords[int(start) : int(stop)]
        if points.shape[0] >= 2 and np.allclose(
            points[0],
            points[-1],
            atol=1e-6,
            rtol=0.0,
        ):
            closed += 1
    return closed


def _metric(name: str, kind: str, unit: str, value: object) -> Metric:
    return Metric(
        name=name,
        kind=kind,
        unit=unit,
        phase="measure",
        scope="primitive",
        value=value,
    )


def _specific_metrics(
    state: PrimitiveBenchmarkState,
    geometry: RealizedGeometry,
) -> list[Metric]:
    primitive = state.primitive
    args = state.arguments
    metrics: list[Metric] = []

    def counter(name: str, value: int, *, unit: str = "count") -> None:
        metrics.append(_metric(name, "counter", unit, int(value)))

    def gauge(name: str, value: object, *, unit: str = "unitless") -> None:
        metrics.append(_metric(name, "gauge", unit, value))

    if primitive in {"arc", "bezier", "circle", "ellipse"}:
        segments = int(args["segments"])
        gauge("work.segments.requested", segments, unit="count")
        gauge("work.segments.effective", segments, unit="count")
    elif primitive == "polyline":
        input_points = int(state.input_points or 0)
        counter("work.input_points", input_points)
        gauge(
            "work.close_vertex_appended",
            bool(args.get("closed")) and geometry.coords.shape[0] == input_points + 1,
            unit="boolean",
        )
    elif primitive == "grid":
        nx = int(args["nx"])
        ny = int(args["ny"])
        counter("work.nx", nx)
        counter("work.ny", ny)
        counter("work.grid_lines.requested", nx + ny)
        counter("work.grid_lines.effective", int(geometry.offsets.size - 1))
    elif primitive == "lissajous":
        requested = int(args["samples"])
        gauge("work.samples.requested", requested, unit="count")
        gauge("work.samples.effective", max(2, requested), unit="count")
        gauge("work.turns", float(args["turns"]), unit="turns")
    elif primitive in {"spiral", "wave"}:
        requested = int(args["samples"])
        gauge("work.samples.requested", requested, unit="count")
        gauge("work.samples.effective", requested, unit="count")
        if primitive == "spiral":
            gauge("work.turns", float(args["turns"]), unit="turns")
        else:
            gauge("work.kind", str(args["kind"]), unit="text")
            gauge("work.cycles", float(args["cycles"]), unit="cycles")
    elif primitive == "spline":
        input_points = int(state.input_points or 0)
        segments_per_span = int(args["segments_per_span"])
        counter("work.input_points", input_points)
        gauge("work.closed", bool(args["closed"]), unit="boolean")
        gauge("work.segments_per_span", segments_per_span, unit="count")
        gauge("work.tension", float(args["tension"]))
    elif primitive == "polygon":
        requested_sides = int(args["n_sides"])
        requested_sweep = float(args["sweep"])
        gauge("work.sides.requested", requested_sides, unit="count")
        gauge("work.sides.effective", max(3, requested_sides), unit="count")
        gauge("work.sweep.requested", requested_sweep, unit="degrees")
        gauge(
            "work.sweep.effective",
            min(360.0, max(0.0, requested_sweep)),
            unit="degrees",
        )
    elif primitive == "torus":
        major = max(3, int(round(float(args["major_segments"]))))
        minor = max(3, int(round(float(args["minor_segments"]))))
        gauge(
            "work.major_segments.requested",
            int(args["major_segments"]),
            unit="count",
        )
        gauge("work.major_segments.effective", major, unit="count")
        gauge(
            "work.minor_segments.requested",
            int(args["minor_segments"]),
            unit="count",
        )
        gauge("work.minor_segments.effective", minor, unit="count")
        counter("work.meridian_lines", major)
        counter("work.parallel_lines", minor)
    elif primitive == "sphere":
        requested_subdivisions = float(args["subdivisions"])
        effective = min(5, max(0, int(round(requested_subdivisions))))
        gauge("work.style", str(args["style"]), unit="text")
        gauge("work.line_mode", str(args["line_mode"]), unit="text")
        gauge(
            "work.subdivisions.requested",
            requested_subdivisions,
            unit="count",
        )
        gauge("work.subdivisions.effective", effective, unit="count")
    elif primitive == "polyhedron":
        gauge("work.kind", str(args["kind"]), unit="text")
        counter("work.faces", int(geometry.offsets.size - 1))
        gauge("work.cache_sensitive", True, unit="boolean")
    elif primitive == "lsystem":
        gauge("work.kind", str(args["kind"]), unit="text")
        gauge("work.iterations", int(args["iters"]), unit="count")
        counter("work.expanded_chars", int(state.work["expanded_chars"]))
        counter("work.draw_commands", int(state.work["draw_commands"]))
        counter("work.branch_pushes", int(state.work["branch_pushes"]))
        gauge("work.jitter", float(args["jitter"]), unit="ratio")
    elif primitive == "laplace_field_grid":
        n_u = int(args["n_u"])
        n_v = int(args["n_v"])
        samples = int(args["samples"])
        boundary = (
            int(args.get("boundary_samples", 720))
            if args["preset"] == "cylinder_uniform" and bool(args.get("draw_boundary", True))
            else 0
        )
        gauge("work.preset", str(args["preset"]), unit="text")
        counter("work.grid_lines.requested", n_u + n_v)
        counter("work.points.mapped", (n_u + n_v) * samples + boundary)
        counter("work.points.kept", int(geometry.coords.shape[0]))
        counter("work.split_lines", int(geometry.offsets.size - 1))
    elif primitive == "topographic_contours":
        width = float(args["width"])
        height = float(args["height"])
        grid_pitch = float(args["grid_pitch"])
        n_x = math.ceil(width / grid_pitch) + 1
        n_y = math.ceil(height / grid_pitch) + 1
        counter("work.grid_points", n_x * n_y)
        counter("work.focus_count", int(args["focus_count"]))
        counter("work.level_count", int(args["level_count"]))
        counter("work.output_paths", int(geometry.offsets.size - 1))
    elif primitive == "delaunay":
        width = float(args["width"])
        height = float(args["height"])
        site_count = int(args["site_count"])
        candidates = int(args["candidates"])
        guard_band = float(args["guard_band"])
        nominal_area = width * height
        nominal_pitch = math.sqrt(nominal_area / site_count)
        guard_margin = guard_band * nominal_pitch
        expanded_width = width + 2.0 * guard_margin
        expanded_height = height + 2.0 * guard_margin
        expanded_site_count = (
            site_count
            if guard_band == 0.0
            else math.ceil(
                site_count * expanded_width * expanded_height / nominal_area
            )
        )
        candidate_checks = (
            candidates * expanded_site_count * (expanded_site_count - 1) // 2
            if candidates > 1
            else 0
        )
        counter("work.site_count", site_count)
        counter("work.candidates", candidates)
        gauge("work.guard_band", guard_band, unit="pitch")
        gauge("work.nominal_pitch", nominal_pitch, unit="geometry_units")
        gauge("work.guard_margin", guard_margin, unit="geometry_units")
        counter("work.expanded_site_count", expanded_site_count)
        counter("work.candidate_checks", candidate_checks)
        counter("work.triangle_count", int(geometry.offsets.size - 1))
    elif primitive in {"text", "asemic"}:
        text = str(args["text"])
        visible = [char for char in text if not char.isspace()]
        counter("work.characters", len(text))
        counter("work.unique_glyphs", len(set(visible)))
        counter("work.input_lines", len(text.split("\n")))
        gauge("work.cache_sensitive", True, unit="boolean")
        if primitive == "text":
            gauge("work.flatten_quality", float(args["quality"]))
        else:
            counter("work.n_nodes", int(args["n_nodes"]))
            counter("work.candidates", int(args["candidates"]))
            counter("work.bezier_samples", int(args["bezier_samples"]))

    if state.asset_sha256 is not None:
        gauge("fixture.asset_sha256", state.asset_sha256, unit="text")
    if state.asset_bytes is not None:
        counter("fixture.asset_bytes", state.asset_bytes, unit="bytes")
    return metrics


__all__ = [
    "case_definitions",
    "PrimitiveBenchmarkCase",
    "PrimitiveBenchmarkState",
    "observe_primitive_output",
    "primitive_measurement_context",
    "primitive_benchmark_cases",
    "run_raw_primitive",
    "setup_primitive_benchmark",
]
