from __future__ import annotations

import numpy as np

from grafix.devtools.benchmarks.cases import build_default_cases
from grafix.devtools.benchmarks.catalog import case_definitions
from grafix.devtools.benchmarks.runner import run_case_isolated
from grafix.devtools.benchmarks.schema import Metric


def test_effect_case_runs_in_isolated_process_with_exact_geometry_checksum() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "effect.translate.line_small"
    )
    result = run_case_isolated(
        definition,
        seed=0,
        mode="warm",
        samples=1,
        warmup=0,
        target_ns=0,
        disable_gc=False,
        timeout_seconds=30.0,
    )

    assert result.status == "ok", result.error
    assert result.checksum_kind == "realized_geometry_exact_v1"
    assert result.checksum
    metrics = {metric.name: metric.value for metric in result.metrics}
    assert metrics["n_vertices"] == 2


def test_heavy_effect_registry_splits_draft_and_final_quality_cases() -> None:
    definitions = {definition.case_id: definition for definition in case_definitions()}

    for effect_name in ("growth", "metaball", "reaction_diffusion"):
        draft_id = f"effect.{effect_name}.draft.rings_2"
        final_id = f"effect.{effect_name}.final.rings_2"
        assert definitions[draft_id].parameters["quality"] == "draft"
        assert definitions[final_id].parameters["quality"] == "final"
        assert definitions[draft_id].parameters["expected_checksum"]
        assert definitions[final_id].parameters["expected_checksum"]
        assert "quality-draft" in definitions[draft_id].tags
        assert "quality-final" in definitions[final_id].tags
        assert f"effect.{effect_name}.rings_2" not in definitions


def test_growth_quality_cases_emit_typed_work_metrics_and_checksum_contracts() -> None:
    definitions = {definition.case_id: definition for definition in case_definitions()}
    draft = definitions["effect.growth.draft.rings_2"]
    final = definitions["effect.growth.final.rings_2"]

    draft_output = draft.workload(draft.setup(dict(draft.parameters), 0))
    draft_metrics = {metric.name: metric for metric in draft_output.metrics}
    assert all(isinstance(metric, Metric) for metric in draft_output.metrics)
    assert draft_metrics["quality"].value == "draft"
    assert draft_metrics["work.iterations.requested"].value == 250
    assert draft_metrics["work.iterations.effective"].value == 32
    assert draft_metrics["n_vertices"].kind == "counter"
    assert draft_metrics["n_lines"].kind == "counter"
    assert len(draft_output.contracts) == 1
    assert draft_output.contracts[0].contract_id == "effect.growth.draft_checksum"
    assert draft_output.contracts[0].severity == "hard"
    assert draft_output.contracts[0].passed

    final_output = final.workload(final.setup(dict(final.parameters), 0))
    final_metrics = {metric.name: metric for metric in final_output.metrics}
    assert final_metrics["quality"].value == "final"
    assert final_metrics["work.iterations.requested"].value == 250
    assert final_metrics["work.iterations.effective"].value == 250
    assert len(final_output.contracts) == 1
    assert final_output.contracts[0].contract_id == "effect.growth.final_checksum"
    assert final_output.contracts[0].severity == "hard"
    assert final_output.contracts[0].passed


def test_fill_matched_height_100_case_keeps_exact_baseline_checksum() -> None:
    definition = next(
        definition
        for definition in case_definitions()
        if definition.case_id == "effect.fill.matched.height_100"
    )

    output = definition.workload(definition.setup(dict(definition.parameters), 0))
    metrics = {metric.name: metric.value for metric in output.metrics}

    assert metrics["n_vertices"] == 50
    assert metrics["n_lines"] == 25
    assert len(output.contracts) == 1
    assert output.contracts[0].severity == "hard"
    assert output.contracts[0].passed


def test_target_effect_speedup_cases_cover_actual_work_and_geometry_shapes() -> None:
    definitions = {definition.case_id: definition for definition in case_definitions()}

    expected = {
        "effect.translate.polyline_long": ("translate", "polyline_long"),
        "effect.translate.many_lines": ("translate", "many_lines"),
        "effect.rotate.pivot.polyline_long": ("rotate", "polyline_long"),
        "effect.scale.by_line.many_lines": ("scale", "many_lines"),
        "effect.scale.by_face.many_rings": ("scale", "many_rings"),
        "effect.subdivide.actual.polyline_spaced_long": (
            "subdivide",
            "polyline_spaced_long",
        ),
        "effect.subdivide.actual.many_lines": ("subdivide", "many_lines"),
        "effect.fill.dense.rings_2": ("fill", "rings_2"),
        "effect.fill.matched.height_100": ("fill", "fill_ring_height_100"),
        "effect.fill.scale_4x.height_400": ("fill", "fill_ring_height_400"),
        "effect.fill.many_rings": ("fill", "many_rings"),
    }
    for case_id, (effect_name, fixture) in expected.items():
        definition = definitions[case_id]
        assert definition.parameters["effect"] == effect_name
        assert definition.parameters["fixture"] == fixture
        assert "actual-work" in definition.tags
        assert "exact-checksum" in definition.tags

    fixtures = {case.case_id: case for case in build_default_cases(seed=0)}
    spaced = fixtures["polyline_spaced_long"].inputs[0]
    segment_lengths = np.linalg.norm(np.diff(spaced.coords, axis=0), axis=1)
    assert float(np.min(segment_lengths)) > 0.01

    many_rings = fixtures["many_rings"].inputs[0]
    assert many_rings.offsets.size - 1 == 512
    np.testing.assert_array_equal(
        np.diff(many_rings.offsets),
        np.full((512,), 5, dtype=np.int32),
    )
    for line_index in (0, 255, 511):
        start = int(many_rings.offsets[line_index])
        stop = int(many_rings.offsets[line_index + 1])
        np.testing.assert_array_equal(
            many_rings.coords[start],
            many_rings.coords[stop - 1],
        )

    fill_1x = fixtures["fill_ring_height_100"].inputs[0]
    fill_4x = fixtures["fill_ring_height_400"].inputs[0]
    assert fill_1x.coords.shape == fill_4x.coords.shape == (4_097, 3)
    assert float(np.ptp(fill_1x.coords[:, 1])) == 100.0
    assert float(np.ptp(fill_4x.coords[:, 1])) == 400.0
    np.testing.assert_allclose(fill_4x.coords, 4.0 * fill_1x.coords, rtol=0.0, atol=0.0)

    matched = definitions["effect.fill.matched.height_100"]
    assert matched.parameters["angle_sets"] == 1
    assert matched.parameters["angle"] == 0.0
    assert matched.parameters["density"] == 25.0
    assert matched.parameters["min_spacing"] == 0.0
    assert matched.parameters["spacing_gradient"] == 0.0
    assert matched.parameters["remove_boundary"] is True
    assert matched.parameters["expected_checksum"]
