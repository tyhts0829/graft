"""Builtin effect benchmark case provider。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from grafix.core.operation_diagnostics import OperationDiagnostic
from grafix.core.realized_geometry import RealizedGeometry
from grafix.devtools.benchmarks.definition import CaseDefinition, define_case
from grafix.devtools.benchmarks.metrics import geometry_checksum
from grafix.devtools.benchmarks.schema import BenchmarkOutput, Metric, evaluate_contract

_CASES_SOURCE_FILE = Path(__file__).with_name("cases.py")
_HEAVY_EFFECT_FINAL_CHECKSUMS = {
    "growth": "88db2188d515eb8320998e5613ca66f5ce773842ae0318ba834ff3c1f2d7db35",
    "metaball": "1df0d8425ddd1f520de5a984eba822ee063fb080a4ae04f7b95a9317610177fd",
    "reaction_diffusion": "b012b5cdb123b635ce475180ba7b12099f7c761c4d0833f4e499044c9d142d40",
}
_HEAVY_EFFECT_DRAFT_CHECKSUMS = {
    "growth": "74f2b9d7186860a848bc2df2eecb99049f805926b5760da6a2ff81275e77850f",
    "metaball": "06ef8acbe6cc943a3d7e0dce65cc783ca3febecc7e83a805c7399711fdadf8ae",
    "reaction_diffusion": "1d04f1417005b3409b8bc35a1e3fdcd689aa04b3433afa6d4c5ed0c85d509f3b",
}
_FILL_MATCHED_HEIGHT_100_CHECKSUM = (
    "68ef73d07f730528912b2deb777040a223ceea680e63253d4a704bd36ab78e40"
)


def case_definitions() -> tuple[CaseDefinition, ...]:
    """Effect benchmark case を返す。"""

    return (
        define_case(
            "effect.translate.line_small",
            "translate / 2 vertices",
            category="effect",
            suite="micro",
            fixture="line_small",
            parameters={"effect": "translate", "delta": [12.0, 5.0, 0.0]},
            tags=("unary", "small", "exact-checksum"),
            selectable_suites=("smoke", "micro", "effects"),
            setup=_setup_effect,
            workload=_workload_effect,
            support_source_files=(_CASES_SOURCE_FILE,),
            support_implementations=(_effect_metrics, _diagnostic_effective_value),
        ),
        define_case(
            "effect.rotate.polyline_long",
            "rotate / 50k vertices",
            category="effect",
            suite="micro",
            fixture="polyline_long",
            parameters={"effect": "rotate", "rotation": [10.0, 20.0, 5.0]},
            tags=("unary", "large", "exact-checksum"),
            selectable_suites=("micro", "effects"),
            setup=_setup_effect,
            workload=_workload_effect,
            support_source_files=(_CASES_SOURCE_FILE,),
            support_implementations=(_effect_metrics, _diagnostic_effective_value),
        ),
        *_effect_definitions(),
        *_target_effect_speedup_definitions(),
    )


def _effect_definitions() -> list[CaseDefinition]:
    """各 effect が要求する入力形へ fixture を明示対応させた代表 case を返す。"""

    fixtures: dict[str, tuple[str, dict[str, Any]]] = {
        "affine": (
            "polyline_long",
            {"scale": [1.05, 1.02, 1.0], "rotation": [5.0, 10.0, 0.0], "delta": [12.0, 5.0, 0.0]},
        ),
        "bold": ("rings_2", {}),
        "buffer": ("rings_2", {"distance": 5.0, "quad_segs": 8, "join": "round"}),
        "clip": ("binary_mask", {}),
        "collapse": ("polyline_long", {}),
        "dash": ("polyline_long", {}),
        "displace": ("polyline_long", {}),
        "drop": ("many_lines", {}),
        "extrude": ("polyline_long", {}),
        "fill": ("rings_2", {}),
        "growth": ("rings_2", {}),
        "highpass": ("polyline_long", {}),
        "isocontour": ("rings_2", {}),
        "lowpass": ("polyline_long", {}),
        "metaball": ("rings_2", {}),
        "mirror": ("polyline_long", {"n_mirror": 3}),
        "mirror3d": ("polyline_long", {}),
        "partition": ("rings_2", {"site_count": 30, "seed": 0}),
        "pixelate": ("polyline_long", {}),
        "quantize": ("polyline_long", {}),
        "reaction_diffusion": ("rings_2", {}),
        "relax": ("rings_2", {}),
        "repeat": ("line_small", {"count": 5}),
        "scale": ("polyline_long", {"scale": [1.15, 0.9, 1.0]}),
        "subdivide": ("polyline_long", {"subdivisions": 2}),
        "trim": ("polyline_long", {}),
        "twist": ("polyline_long", {}),
        "warp": ("binary_mask", {}),
        "weave": ("many_lines", {}),
        "wobble": ("polyline_long", {}),
    }
    definitions: list[CaseDefinition] = []
    for effect_name, (fixture, overrides) in fixtures.items():
        if effect_name in _HEAVY_EFFECT_FINAL_CHECKSUMS:
            for quality in ("draft", "final"):
                parameters: dict[str, Any] = {
                    "effect": effect_name,
                    "fixture": fixture,
                    "quality": quality,
                    **overrides,
                }
                parameters["expected_checksum"] = (
                    _HEAVY_EFFECT_FINAL_CHECKSUMS[effect_name]
                    if quality == "final"
                    else _HEAVY_EFFECT_DRAFT_CHECKSUMS[effect_name]
                )
                definitions.append(
                    define_case(
                        f"effect.{effect_name}.{quality}.{fixture}",
                        f"{effect_name} / {fixture} / {quality}",
                        category="effect",
                        suite="effects",
                        fixture=fixture,
                        parameters=parameters,
                        tags=(
                            "explicit-fixture",
                            "exact-checksum",
                            f"quality-{quality}",
                            "heavy-effect",
                        ),
                        selectable_suites=("effects",),
                        setup=_setup_effect,
                        workload=_workload_effect,
                        support_source_files=(_CASES_SOURCE_FILE,),
                        support_implementations=(
                            _effect_metrics,
                            _diagnostic_effective_value,
                        ),
                    )
                )
            continue
        definitions.append(
            define_case(
                f"effect.{effect_name}.{fixture}",
                f"{effect_name} / {fixture}",
                category="effect",
                suite="effects",
                fixture=fixture,
                parameters={
                    "effect": effect_name,
                    "fixture": fixture,
                    **overrides,
                },
                tags=("explicit-fixture", "exact-checksum"),
                selectable_suites=("effects",),
                setup=_setup_effect,
                workload=_workload_effect,
                support_source_files=(_CASES_SOURCE_FILE,),
                support_implementations=(
                    _effect_metrics,
                    _diagnostic_effective_value,
                ),
            )
        )
    return definitions


def _target_effect_speedup_definitions() -> list[CaseDefinition]:
    """高速化対象 effect の actual-work と shape 別 case を返す。"""

    cases: tuple[tuple[str, str, str, str, dict[str, Any], tuple[str, ...]], ...] = (
        (
            "effect.translate.polyline_long",
            "translate / 50k vertices",
            "polyline_long",
            "translate",
            {"delta": [12.0, 5.0, 3.5]},
            ("large", "coordinate-only"),
        ),
        (
            "effect.translate.many_lines",
            "translate / 5k lines",
            "many_lines",
            "translate",
            {"delta": [12.0, 5.0, 0.0]},
            ("many-short-lines", "coordinate-only"),
        ),
        (
            "effect.rotate.pivot.polyline_long",
            "rotate fixed pivot / 50k vertices",
            "polyline_long",
            "rotate",
            {
                "auto_center": False,
                "pivot": [12.0, -5.0, 3.0],
                "rotation": [10.0, 20.0, 5.0],
            },
            ("large", "coordinate-only", "fixed-pivot"),
        ),
        (
            "effect.scale.by_line.many_lines",
            "scale by line / 5k lines",
            "many_lines",
            "scale",
            {"mode": "by_line", "scale": [1.15, 0.9, 1.0]},
            ("many-short-lines", "coordinate-only"),
        ),
        (
            "effect.scale.by_face.many_rings",
            "scale by face / 512 rings",
            "many_rings",
            "scale",
            {"mode": "by_face", "scale": [1.15, 0.9, 1.0]},
            ("many-short-lines", "rings", "coordinate-only"),
        ),
        (
            "effect.subdivide.actual.polyline_spaced_long",
            "subdivide actual work / 50k vertices",
            "polyline_spaced_long",
            "subdivide",
            {"subdivisions": 2},
            ("large", "topology-changing"),
        ),
        (
            "effect.subdivide.actual.many_lines",
            "subdivide actual work / 5k lines",
            "many_lines",
            "subdivide",
            {"subdivisions": 2},
            ("many-short-lines", "topology-changing"),
        ),
        (
            "effect.fill.dense.rings_2",
            "fill dense cross hatch / outer and hole",
            "rings_2",
            "fill",
            {
                "angle_sets": 3,
                "angle": 17.0,
                "density": 1000.0,
                "remove_boundary": True,
            },
            ("rings", "dense", "topology-changing"),
        ),
        (
            "effect.fill.matched.height_100",
            "fill matched output / 4k edges / height 100",
            "fill_ring_height_100",
            "fill",
            {
                "angle_sets": 1,
                "angle": 0.0,
                "density": 25.0,
                "min_spacing": 0.0,
                "spacing_gradient": 0.0,
                "remove_boundary": True,
                "expected_checksum": _FILL_MATCHED_HEIGHT_100_CHECKSUM,
            },
            ("rings", "high-edge", "matched-output", "topology-changing"),
        ),
        (
            "effect.fill.scale_4x.height_400",
            "fill scale 4x / 4k edges / height 400",
            "fill_ring_height_400",
            "fill",
            {
                "angle_sets": 1,
                "angle": 0.0,
                "density": 25.0,
                "min_spacing": 0.0,
                "spacing_gradient": 0.0,
                "remove_boundary": True,
            },
            ("rings", "high-edge", "scale-4x", "topology-changing"),
        ),
        (
            "effect.fill.many_rings",
            "fill / 512 disjoint rings",
            "many_rings",
            "fill",
            {
                "angle_sets": 1,
                "angle": 0.0,
                "density": 20.0,
                "remove_boundary": True,
            },
            ("many-short-lines", "rings", "topology-changing"),
        ),
    )
    return [
        define_case(
            case_id,
            label,
            category="effect",
            suite="effects",
            fixture=fixture,
            parameters={"effect": effect_name, "fixture": fixture, **parameters},
            tags=("explicit-fixture", "exact-checksum", "actual-work", *tags),
            selectable_suites=("effects",),
            setup=_setup_effect,
            workload=_workload_effect,
            support_source_files=(_CASES_SOURCE_FILE,),
            support_implementations=(
                _effect_metrics,
                _diagnostic_effective_value,
            ),
        )
        for case_id, label, fixture, effect_name, parameters, tags in cases
    ]


def _setup_effect(parameters: dict[str, Any], seed: int) -> object:
    from grafix.core.builtins import builtin_operation_catalog
    from grafix.devtools.benchmarks.cases import build_default_cases

    effect_name = str(parameters["effect"])
    fixture = str(
        parameters.get(
            "fixture",
            "line_small" if effect_name == "translate" else "polyline_long",
        )
    )
    benchmark_case = next(
        case for case in build_default_cases(seed=seed) if case.case_id == fixture
    )
    spec = builtin_operation_catalog().resolve("effect", effect_name)
    quality = str(parameters.get("quality", "final"))
    if quality not in {"draft", "final"}:
        raise ValueError(f"unknown effect benchmark quality: {quality!r}")
    expected_checksum = parameters.get("expected_checksum")
    if expected_checksum is not None and not isinstance(expected_checksum, str):
        raise TypeError("expected effect checksum must be a string")
    args = dict(spec.schema.defaults)
    for key, value in parameters.items():
        if key in {"effect", "fixture", "quality", "expected_checksum"}:
            continue
        meta = spec.schema.meta.get(key)
        if meta is not None and meta.kind in {"vec3", "rgb"}:
            if not isinstance(value, list) or len(value) != 3:
                raise TypeError(
                    f"{effect_name}.{key} benchmark parameter must be a three-item JSON array"
                )
            value = tuple(value)
        args[key] = value
    args_tuple = tuple(sorted(args.items()))
    return (
        spec.evaluator,
        benchmark_case.inputs,
        args_tuple,
        effect_name,
        quality,
        expected_checksum,
    )


def _diagnostic_effective_value(
    diagnostics: tuple[OperationDiagnostic, ...],
    *,
    op: str,
    requested: int | float,
) -> int | float:
    effective: int | float = requested
    for diagnostic in diagnostics:
        if diagnostic.op != op:
            continue
        value = diagnostic.effective_value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            effective = value
    return effective


def _effect_metrics(
    *,
    effect_name: str,
    quality: str,
    args: dict[str, Any],
    inputs: tuple[RealizedGeometry, ...],
    geometry: RealizedGeometry,
    diagnostics: tuple[OperationDiagnostic, ...],
) -> tuple[Metric, ...]:
    metrics = [
        Metric(
            name="quality",
            kind="gauge",
            unit="unitless",
            phase="measure",
            scope="effect",
            value=quality,
        ),
        Metric(
            name="n_vertices",
            kind="counter",
            unit="count",
            phase="measure",
            scope="effect",
            value=int(geometry.coords.shape[0]),
        ),
        Metric(
            name="n_lines",
            kind="counter",
            unit="count",
            phase="measure",
            scope="effect",
            value=int(geometry.offsets.size - 1),
        ),
        Metric(
            name="output_bytes",
            kind="counter",
            unit="bytes",
            phase="measure",
            scope="effect",
            value=int(geometry.coords.nbytes + geometry.offsets.nbytes),
        ),
        Metric(
            name="diagnostics",
            kind="counter",
            unit="count",
            phase="measure",
            scope="effect",
            value=len(diagnostics),
        ),
    ]

    def add_work_metric(name: str, value: int | float, *, unit: str) -> None:
        metrics.append(
            Metric(
                name=name,
                kind="gauge",
                unit=unit,
                phase="measure",
                scope="effect",
                value=value,
            )
        )

    if effect_name == "reaction_diffusion":
        requested_steps = int(args["steps"])
        requested_pitch = float(args["grid_pitch"])
        add_work_metric("work.steps.requested", requested_steps, unit="count")
        add_work_metric(
            "work.steps.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="reaction_diffusion.steps",
                requested=requested_steps,
            ),
            unit="count",
        )
        add_work_metric(
            "work.grid_pitch.requested",
            requested_pitch,
            unit="geometry_units",
        )
        add_work_metric(
            "work.grid_pitch.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="reaction_diffusion.grid_pitch",
                requested=requested_pitch,
            ),
            unit="geometry_units",
        )
    elif effect_name == "growth":
        requested_iterations = int(args["iters"])
        add_work_metric(
            "work.iterations.requested",
            requested_iterations,
            unit="count",
        )
        add_work_metric(
            "work.iterations.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="growth.iters",
                requested=requested_iterations,
            ),
            unit="count",
        )
        point_budget = next(
            (
                diagnostic
                for diagnostic in reversed(diagnostics)
                if diagnostic.op == "growth.total_points"
            ),
            None,
        )
        if point_budget is not None:
            original = point_budget.original_value
            effective = point_budget.effective_value
            if isinstance(original, int) and isinstance(effective, int):
                add_work_metric(
                    "work.total_points.requested",
                    original,
                    unit="count",
                )
                add_work_metric(
                    "work.total_points.effective",
                    effective,
                    unit="count",
                )
    elif effect_name == "metaball":
        requested_pitch = float(args["grid_pitch"])
        requested_segments = sum(
            max(0, int(stop) - int(start) - 1)
            for geometry_input in inputs
            for start, stop in zip(
                geometry_input.offsets[:-1],
                geometry_input.offsets[1:],
                strict=True,
            )
        )
        add_work_metric(
            "work.grid_pitch.requested",
            requested_pitch,
            unit="geometry_units",
        )
        add_work_metric(
            "work.grid_pitch.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="metaball.grid_pitch",
                requested=requested_pitch,
            ),
            unit="geometry_units",
        )
        add_work_metric(
            "work.segments.requested",
            requested_segments,
            unit="count",
        )
        add_work_metric(
            "work.segments.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="metaball.ring_segments",
                requested=requested_segments,
            ),
            unit="count",
        )
    return tuple(metrics)


def _workload_effect(state: object) -> BenchmarkOutput:
    from grafix.core.operation_diagnostics import operation_diagnostic_context
    from grafix.core.preview_quality import preview_quality_context

    (
        evaluator,
        inputs,
        args_tuple,
        effect_name,
        quality,
        expected_checksum,
    ) = cast(tuple[Any, Any, Any, str, str, str | None], state)
    with operation_diagnostic_context() as diagnostic_buffer:
        with preview_quality_context(cast(Any, quality)):
            output = evaluator(inputs, args_tuple)
    geometry = (
        output
        if isinstance(output, RealizedGeometry)
        else RealizedGeometry(coords=output[0], offsets=output[1])
    )
    diagnostics = diagnostic_buffer.snapshot()
    args = dict(args_tuple)
    contracts = (
        (
            evaluate_contract(
                contract_id=f"effect.{effect_name}.{quality}_checksum",
                severity="hard",
                actual=geometry_checksum(geometry),
                comparator="eq",
                limit=expected_checksum,
                reason=f"{quality} quality geometry checksum remains exact",
            ),
        )
        if expected_checksum is not None
        else ()
    )
    return BenchmarkOutput(
        value=geometry,
        metrics=_effect_metrics(
            effect_name=effect_name,
            quality=quality,
            args=args,
            inputs=inputs,
            geometry=geometry,
            diagnostics=diagnostics,
        ),
        contracts=contracts,
    )


__all__ = [
    "case_definitions",
]
