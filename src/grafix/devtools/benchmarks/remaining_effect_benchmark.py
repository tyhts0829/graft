"""除外 5 件以外の組み込み effect 用 actual-work benchmark。"""

from __future__ import annotations

import hashlib
import json
import warnings
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

from grafix.core.operation_authoring import EffectEvaluator
from grafix.core.operation_diagnostics import (
    OperationDiagnostic,
    OperationDiagnosticBuffer,
    operation_diagnostic_context,
)
from grafix.core.preview_quality import PreviewQuality, preview_quality_context
from grafix.core.realized_geometry import RealizedGeometry
from grafix.devtools.benchmarks.definition import CaseDefinition, define_case
from grafix.devtools.benchmarks.schema import (
    BenchmarkOutput,
    ContractResult,
    Metric,
    evaluate_contract,
    freeze_json_object,
    materialize_json_object,
)

_EXCLUDED_EFFECTS = frozenset({"fill", "subdivide", "scale", "rotate", "translate"})
_JIT_EFFECTS = frozenset(
    {
        "collapse",
        "dash",
        "displace",
        "growth",
        "highpass",
        "isocontour",
        "lowpass",
        "metaball",
        "mirror",
        "pixelate",
        "reaction_diffusion",
        "relax",
        "repeat",
        "resample",
        "trim",
        "warp",
        "weave",
    }
)
_PROCESS_COLD_EFFECTS = frozenset(
    {"boolean", "buffer", "clip", "mirror3d", "offset_curve", "partition"}
)


def case_definitions() -> tuple[CaseDefinition, ...]:
    """除外 5 件以外の effect direct-evaluator actual-work case を返す。"""

    cases_source_file = Path(__file__).with_name("cases.py")
    return tuple(
        define_case(
            case.case_id,
            case.label,
            category="effect",
            suite="effects-remaining",
            fixture=case.fixture,
            parameters=case.parameters(),
            tags=case.tags,
            selectable_suites=case.selectable_suites,
            setup=setup_remaining_effect_benchmark,
            workload=run_remaining_effect,
            postprocess=observe_remaining_effect_output,
            measurement_context=remaining_effect_measurement_context,
            support_source_files=(Path(__file__), cases_source_file),
        )
        for case in remaining_effect_benchmark_cases()
    )


# 既存 effect は Phase 0 の immutable baseline から固定した値。今回新設した effect は
# semantic test と公開契約を確定した初期実装の出力を、今後の変更検出用 baseline とする。
_EXPECTED_CHECKSUMS = {
    "effect.remaining.affine.polyline_long": (
        "ebb0862ac419b5b1b2bd2d1f65c136ad68cc3e52b1c97f0af452e2a24383105f"
    ),
    "effect.remaining.bold.many_lines": (
        "fd8ae218caf14a03280ddf293ccfb91316268eccd8df982efdc9d7a7a4be17b1"
    ),
    "effect.remaining.boolean.binary_regions": (
        "fa1154e8c00e4ceb0f0518f13a8037791bf6fe656e8ed0fa2defab8880c483ba"
    ),
    "effect.remaining.buffer.ring_big": (
        "12b6e76184c63a7b258099099d6e8cda2a23e127def92531f6f66804675dd195"
    ),
    "effect.remaining.clip.binary_mask": (
        "15890dd41d8a28b4f3061bd74f908b442890f5e5b26018168bb4022cc9940a30"
    ),
    "effect.remaining.collapse.polyline_long": (
        "f9122e2faf56e4eefe5e3df1189be1ef6f03cbf6be08fdbb4d70b7ebb268b4d9"
    ),
    "effect.remaining.dash.polyline_long": (
        "e05e5845c1b4355a3892eea85253af008382d1b0f0f2539cd2e4b5ae47942201"
    ),
    "effect.remaining.dash.many_lines_jitter": (
        "bfaf3b7a5c03402b6aff345c2a372a1fa2a02405db32752161340c7950a70ccb"
    ),
    "effect.remaining.deduplicate.dedup_duplicates": (
        "cbf1986f9218f0850dd3a671604f1b312a5044587958ac7c54e22bae4ab8e89c"
    ),
    "effect.remaining.displace.polyline_long": (
        "96b2d7e87a0c720e9ad0017b04643806e119cc56f630b03a28c7e407421cacd5"
    ),
    "effect.remaining.drop.many_lines": (
        "5f821aeed7b314a4e7a8e4a9e6afa05112d96aef53adcc2efc4eb0a529432a01"
    ),
    "effect.remaining.extrude.polyline_long": (
        "98929650c02d465087825c6e195159a5936d9e4b3cd6d25f78133cbe89f3a3d2"
    ),
    "effect.remaining.growth.draft.rings_medium": (
        "3ec855ec25a245eb398424c3101203fdc140d432985ea75b51cb67c3f8ede45f"
    ),
    "effect.remaining.growth.final.rings_medium": (
        "37a5f5e5101ff87844d2a16d8fb8058e16f368a63ef407a68d92fb8876c11054"
    ),
    "effect.remaining.highpass.polyline_long": (
        "a6c232a43beb77d40153e25f324df47724980a77a35619de9d5cdfcb0f201637"
    ),
    "effect.remaining.highpass.many_lines": (
        "df8fe89ae7b84b467259e22b64dc93f4f85cb953c7840c8ed2e716acf18680e3"
    ),
    "effect.remaining.isocontour.rings_medium": (
        "909cb4d8136ad95650d9f5692f2f9fadf88939d4a9eb2f6d963fdd10793e21f2"
    ),
    "effect.remaining.lowpass.polyline_long": (
        "0d22bfc91be16f5f57bf429404f4ee179cbc958092b246bcb21731d3b636e0d3"
    ),
    "effect.remaining.lowpass.many_lines": (
        "83afb067b864e968b513973112793227418124ab71c20a44bfd28e1af5d9f4f4"
    ),
    "effect.remaining.metaball.draft.rings_medium": (
        "1f66d4457127f82946bfbf1a20bb48cc16419dcdea18f264844e49ebdb007b25"
    ),
    "effect.remaining.metaball.final.rings_medium": (
        "3232fdae439eb72ac8a089f8976acae07c5fed8cbe36b0653efef2162b683fb2"
    ),
    "effect.remaining.mirror.many_lines": (
        "3839b2c1169bcc00480a1d3fb095d340c673d3ed806d756927cf28de71c616ec"
    ),
    "effect.remaining.mirror3d.many_lines": (
        "d367af93f2de5465de288350acfe0300ae9662727afe98f3fadf7e23640d76fa"
    ),
    "effect.remaining.offset_curve.many_lines": (
        "4d97941068f5cf5c4a283144f6134e9035dddb2ffc86599af9b65c8d61b65ef4"
    ),
    "effect.remaining.offset_curve.polyline_long": (
        "f45660b4d376bb6242dc7fdaa457168c1dbfd8136c06072fde96c0962872cdd1"
    ),
    "effect.remaining.partition.rings_medium": (
        "a124d9487a40bc39b08f6b60fd6676ed7d6817bb505a976724636a0958432ace"
    ),
    "effect.remaining.pixelate.polyline_spaced_long": (
        "d49b75fa6d15cfe08c8d2b09113831194b92a2758bd224d84a84ee1ac7150f4d"
    ),
    "effect.remaining.quantize.polyline_long": (
        "53bac4de5fe8ffe6b8cd9d2c3c51b7c08356fbcbee7fa86c9dabb259d7b2b0ca"
    ),
    "effect.remaining.reaction_diffusion.draft.rings_medium": (
        "edc9d0d9795483b9b29be51806fbefffb23f4bf1d92c286474910709c1124994"
    ),
    "effect.remaining.reaction_diffusion.final.rings_medium": (
        "b012b5cdb123b635ce475180ba7b12099f7c761c4d0833f4e499044c9d142d40"
    ),
    "effect.remaining.relax.shared_network": (
        "a9700a14025467b8bb6f79fd502d531ac3d8f3c8bff47bd88b476ee3b2223445"
    ),
    "effect.remaining.repeat.many_lines": (
        "73e99c99737f4afe9525f2327b10f1869ec3a435f4f90e2970b05f833f5db07c"
    ),
    "effect.remaining.resample.polyline_spaced_long": (
        "784df695614ea144de053e9799b0beaf4e6b0ab2983b24886c7cee7a5244b687"
    ),
    "effect.remaining.resample.upsample.polyline_spaced_long": (
        "edbeb5fab137fc8f9104052a02336219def887cb704bfa505f6b80b16426b0c5"
    ),
    "effect.remaining.simplify.polyline_long": (
        "f28215cc209c53710c2165972d876db29c094a783be6e85b57d45b4db310f9d4"
    ),
    "effect.remaining.trim.polyline_long": (
        "c62484b95b1b0f85419cded7d48c4f1408436b0fb18d4ba64bf0012866b8bff4"
    ),
    "effect.remaining.twist.polyline_long": (
        "00b080f83f26d1ba01f6c661dd3fe0178f891921294b02b297e1869663b95f68"
    ),
    "effect.remaining.warp.binary_long_mask": (
        "1ea8f53c69851e3b5ef1aee7d6fac509eec00370ce4982e93253b945cecff719"
    ),
    "effect.remaining.weave.ring_weave": (
        "b2e412b01003b4490f7890176a2e3caea53675dda06d9be4ca4f6d04d2f790d3"
    ),
    "effect.remaining.wobble.polyline_long": (
        "612d6892cb6d5cf0b2b946506eed654f3398f318214862396f63b47240a302a0"
    ),
}
_EXPECTED_DIAGNOSTICS: dict[str, list[list[object]]] = {
    "effect.remaining.growth.draft.rings_medium": [
        [
            "growth.iters",
            64,
            32,
            ("draft preview capped simulation iterations; final capture keeps the requested value"),
            "info",
        ]
    ],
    "effect.remaining.metaball.draft.rings_medium": [
        [
            "GridSpec.from_bbox",
            0.75,
            2.6376173738046162,
            "grid pitch was coarsened to satisfy the point limit",
            "warning",
        ],
        [
            "metaball.grid_pitch",
            0.75,
            2.6376173738046162,
            ("draft preview coarsened the field grid; final capture keeps the requested pitch"),
            "info",
        ],
    ],
    "effect.remaining.reaction_diffusion.draft.rings_medium": [
        [
            "GridSpec.from_bbox",
            0.8,
            2.0077973938621607,
            "grid pitch was coarsened to satisfy the point limit",
            "warning",
        ],
        [
            "reaction_diffusion.grid_pitch",
            0.8,
            2.0077973938621607,
            (
                "draft preview coarsened the simulation grid to keep points × "
                "steps within budget; final capture keeps the requested pitch"
            ),
            "info",
        ],
        [
            "reaction_diffusion.steps",
            800,
            600,
            ("draft preview capped points × steps work; final capture keeps the requested value"),
            "info",
        ],
    ],
}
_EXPECTED_LAYOUT: dict[str, object] = {
    "coords_dtype": "<f4",
    "offsets_dtype": "<i4",
    "coords_strides": [12, 4],
    "offsets_strides": [4],
    "coords_c_contiguous": True,
    "offsets_c_contiguous": True,
    "coords_f_contiguous": False,
    "offsets_f_contiguous": True,
    "coords_writeable": False,
    "offsets_writeable": False,
    "coords_owndata": False,
    "offsets_owndata": False,
    "coords_aligned": True,
    "offsets_aligned": True,
}
_EXPECTED_LAYOUT_OVERRIDES: dict[str, dict[str, object]] = {
    # shape=(0, 3) は C/F の両方に contiguous と判定される。
    "effect.remaining.reaction_diffusion.final.rings_medium": {
        "coords_f_contiguous": True,
    },
}
_EXPECTED_OFFSETS_ALIAS = frozenset(
    {
        "effect.remaining.affine.polyline_long",
        "effect.remaining.displace.polyline_long",
        "effect.remaining.quantize.polyline_long",
        "effect.remaining.relax.shared_network",
        "effect.remaining.twist.polyline_long",
        "effect.remaining.warp.binary_long_mask",
        "effect.remaining.wobble.polyline_long",
    }
)


@dataclass(frozen=True, slots=True)
class RemainingEffectBenchmarkCase:
    """runner が process 間で再構築できる effect case 記述。"""

    case_id: str
    label: str
    effect: str
    fixture: str
    arguments: Mapping[str, object]
    work_kind: str
    quality: PreviewQuality = "final"
    tags: tuple[str, ...] = ()
    selectable_suites: tuple[str, ...] = ("effects-remaining",)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            freeze_json_object(self.arguments),
        )

    def parameters(self) -> dict[str, Any]:
        """JSON-compatible な case parameter を返す。"""

        return {
            "case_id": self.case_id,
            "effect": self.effect,
            "fixture": self.fixture,
            "arguments": materialize_json_object(freeze_json_object(self.arguments)),
            "work_kind": self.work_kind,
            "quality": self.quality,
            "expected_checksum": _EXPECTED_CHECKSUMS.get(self.case_id),
            "expected_diagnostics": _EXPECTED_DIAGNOSTICS.get(self.case_id, []),
            "expected_warnings": [],
            "expected_layout": {
                **_EXPECTED_LAYOUT,
                **_EXPECTED_LAYOUT_OVERRIDES.get(self.case_id, {}),
            },
            "expected_alias": {
                "output_is_input": False,
                "coords_is_input": False,
                "offsets_is_input": self.case_id in _EXPECTED_OFFSETS_ALIAS,
                "coords_alias_input": False,
                "offsets_alias_input": self.case_id in _EXPECTED_OFFSETS_ALIAS,
            },
        }


@dataclass(frozen=True, slots=True)
class _ArrayMutationSnapshot:
    """入力 ndarray の bytes と mutation-sensitive layout を固定する。"""

    dtype: str
    shape: tuple[int, ...]
    strides: tuple[int, ...]
    c_contiguous: bool
    f_contiguous: bool
    writeable: bool
    owndata: bool
    aligned: bool
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class _GeometryMutationSnapshot:
    """1 geometry の coords / offsets mutation snapshot。"""

    coords: _ArrayMutationSnapshot
    offsets: _ArrayMutationSnapshot


@dataclass(slots=True)
class RemainingEffectBenchmarkState:
    """setup 済み evaluator、immutable input、期待契約。"""

    case_id: str
    effect: str
    evaluator: EffectEvaluator
    inputs: tuple[RealizedGeometry, ...]
    arguments: tuple[tuple[str, Any], ...]
    quality: PreviewQuality
    work_kind: str
    expected_checksum: str | None
    expected_diagnostics: list[list[object]] | None
    expected_warnings: list[list[str]] | None
    expected_layout: dict[str, object] | None
    expected_alias: dict[str, bool] | None
    input_checksums: tuple[str, ...]
    input_snapshots: tuple[_GeometryMutationSnapshot, ...]
    effect_source_sha256: str
    geometry_kernels_source_sha256: str
    diagnostic_buffer: OperationDiagnosticBuffer | None = None


def remaining_effect_benchmark_cases() -> tuple[RemainingEffectBenchmarkCase, ...]:
    """対象 32 effect の final actual-work と heavy draft case を返す。"""

    cases = (
        RemainingEffectBenchmarkCase(
            "effect.remaining.affine.polyline_long",
            "affine / 50k vertices / composite",
            "affine",
            "polyline_long",
            {
                "scale": [1.05, 0.97, 1.02],
                "rotation": [7.0, -11.0, 19.0],
                "delta": [12.0, -5.0, 3.0],
            },
            "changed",
            tags=("coordinate-only", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.bold.many_lines",
            "bold / 5k lines / 10 copies",
            "bold",
            "many_lines",
            {"count": 10, "radius": 0.75, "seed": 20260719},
            "more_vertices",
            tags=("copy", "many-short-lines", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.boolean.binary_regions",
            "boolean / outer+hole against overlapping 2k-side region / xor",
            "boolean",
            "binary_regions",
            {"mode": "xor"},
            "topology_changed",
            tags=("binary", "external-backend", "ring"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.buffer.ring_big",
            "buffer / 5k-segment ring",
            "buffer",
            "ring_big",
            {
                "distance": 5.0,
                "quad_segs": 8,
                "join": "round",
                "union": False,
            },
            "topology_changed",
            tags=("external-backend", "ring"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.clip.binary_mask",
            "clip / 1k lines / outer and hole",
            "clip",
            "binary_mask",
            {"mode": "inside", "draw_outline": False},
            "changed",
            tags=("binary", "external-backend"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.collapse.polyline_long",
            "collapse / 50k vertices",
            "collapse",
            "polyline_long",
            {"intensity": 5.0, "subdivisions": 2},
            "more_vertices",
            tags=("topology-changing", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.dash.polyline_long",
            "dash / 50k vertices",
            "dash",
            "polyline_long",
            {"dash_length": 6.0, "gap_length": 3.0},
            "topology_changed",
            tags=("topology-changing", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.dash.many_lines_jitter",
            "dash / 5k lines / jitter",
            "dash",
            "many_lines",
            {
                "dash_length": 2.0,
                "gap_length": 3.0,
                "offset": 0.5,
                "offset_jitter": 0.75,
            },
            "topology_changed",
            tags=("topology-changing", "many-short-lines", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.deduplicate.dedup_duplicates",
            "deduplicate / 12k segments / four exact copies",
            "deduplicate",
            "dedup_duplicates",
            {"tolerance": 0.0, "merge_chains": True},
            "fewer_vertices",
            tags=("graph", "duplicate-segments", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.displace.polyline_long",
            "displace / 50k vertices",
            "displace",
            "polyline_long",
            {
                "amplitude": [8.0, 5.0, 3.0],
                "spatial_freq": [0.04, 0.06, 0.08],
                "t": 0.25,
            },
            "changed",
            tags=("coordinate-only", "noise", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.drop.many_lines",
            "drop / 5k lines / interval and probability",
            "drop",
            "many_lines",
            {
                "interval": 3,
                "index_offset": 1,
                "probability_base": [0.15, 0.15, 0.15],
                "seed": 20260719,
            },
            "fewer_lines",
            tags=("selection", "many-short-lines", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.extrude.polyline_long",
            "extrude / 50k vertices / subdivided",
            "extrude",
            "polyline_long",
            {
                "delta": [7.0, -3.0, 2.0],
                "scale": 0.8,
                "subdivisions": 2,
            },
            "more_vertices",
            tags=("topology-changing", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.growth.final.rings_medium",
            "growth / medium rings / final",
            "growth",
            "rings_medium",
            {
                "seed_count": 8,
                "target_spacing": 4.0,
                "iters": 64,
                "seed": 20260719,
            },
            "changed",
            quality="final",
            tags=("simulation", "quality-final", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.growth.draft.rings_medium",
            "growth / medium rings / draft",
            "growth",
            "rings_medium",
            {
                "seed_count": 8,
                "target_spacing": 4.0,
                "iters": 64,
                "seed": 20260719,
            },
            "changed",
            quality="draft",
            tags=("simulation", "quality-draft", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.highpass.polyline_long",
            "highpass / 50k vertices / wide kernel",
            "highpass",
            "polyline_long",
            {"step": 0.25, "sigma": 4.0, "gain": 1.25, "closed": "open"},
            "changed",
            tags=("filter", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.highpass.many_lines",
            "highpass / 5k short lines",
            "highpass",
            "many_lines",
            {"step": 1.0, "sigma": 2.0, "gain": 1.25, "closed": "open"},
            "changed",
            tags=("filter", "many-short-lines"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.isocontour.rings_medium",
            "isocontour / medium rings / both",
            "isocontour",
            "rings_medium",
            {
                "spacing": 4.0,
                "max_dist": 20.0,
                "mode": "both",
                "grid_pitch": 1.0,
            },
            "topology_changed",
            tags=("distance-field", "ring"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.lowpass.polyline_long",
            "lowpass / 50k vertices / wide kernel",
            "lowpass",
            "polyline_long",
            {"step": 0.25, "sigma": 4.0, "closed": "open"},
            "changed",
            tags=("filter", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.lowpass.many_lines",
            "lowpass / 5k short lines",
            "lowpass",
            "many_lines",
            {"step": 1.0, "sigma": 2.0, "closed": "open"},
            "changed",
            tags=("filter", "many-short-lines"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.metaball.final.rings_medium",
            "metaball / medium rings / final",
            "metaball",
            "rings_medium",
            {
                "radius": 8.0,
                "threshold": 1.0,
                "grid_pitch": 0.75,
                "output": "both",
            },
            "topology_changed",
            quality="final",
            tags=("distance-field", "quality-final"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.metaball.draft.rings_medium",
            "metaball / medium rings / draft",
            "metaball",
            "rings_medium",
            {
                "radius": 8.0,
                "threshold": 1.0,
                "grid_pitch": 0.75,
                "output": "both",
            },
            "topology_changed",
            quality="draft",
            tags=("distance-field", "quality-draft"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.mirror.many_lines",
            "mirror / 5k lines / eight planes",
            "mirror",
            "many_lines",
            {"n_mirror": 8},
            "topology_changed",
            tags=("symmetry", "many-short-lines"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.mirror3d.many_lines",
            "mirror3d / 5k lines / icosahedral",
            "mirror3d",
            "many_lines",
            {"mode": "polyhedral", "group": "I"},
            "more_vertices",
            tags=("symmetry", "many-short-lines", "cache-sensitive"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.offset_curve.many_lines",
            "offset_curve / 5k open lines / both sides",
            "offset_curve",
            "many_lines",
            {
                "distance": 2.0,
                "side": "both",
                "count": 1,
                "join": "round",
                "keep_original": False,
            },
            "more_lines",
            tags=("external-backend", "many-short-lines", "topology-changing"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.offset_curve.polyline_long",
            "offset_curve / 50k-vertex open line / both sides",
            "offset_curve",
            "polyline_long",
            {
                "distance": 2.0,
                "side": "both",
                "count": 1,
                "join": "round",
                "keep_original": False,
            },
            "more_lines",
            tags=("external-backend", "large", "topology-changing"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.partition.rings_medium",
            "partition / medium rings / 64 sites",
            "partition",
            "rings_medium",
            {"mode": "merge", "site_count": 64, "seed": 20260719},
            "topology_changed",
            tags=("external-backend", "rng", "ring"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.pixelate.polyline_spaced_long",
            "pixelate / spaced 50k vertices",
            "pixelate",
            "polyline_spaced_long",
            {"step": [1.0, 0.75, 1.25], "corner": "auto"},
            "changed",
            tags=("topology-changing", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.quantize.polyline_long",
            "quantize / 50k vertices / anisotropic",
            "quantize",
            "polyline_long",
            {"step": [0.7, 1.1, 0.3]},
            "changed",
            tags=("coordinate-only", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.reaction_diffusion.final.rings_medium",
            "reaction diffusion / medium rings / final",
            "reaction_diffusion",
            "rings_medium",
            {
                "grid_pitch": 0.8,
                "steps": 800,
                "seed": 20260719,
                "min_points": 8,
            },
            "topology_changed",
            quality="final",
            tags=("simulation", "quality-final", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.reaction_diffusion.draft.rings_medium",
            "reaction diffusion / medium rings / draft",
            "reaction_diffusion",
            "rings_medium",
            {
                "grid_pitch": 0.8,
                "steps": 800,
                "seed": 20260719,
                "min_points": 8,
            },
            "topology_changed",
            quality="draft",
            tags=("simulation", "quality-draft", "rng"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.relax.shared_network",
            "relax / 2.5k-node shared network",
            "relax",
            "shared_network",
            {"relaxation_iterations": 15, "step": 0.125},
            "changed",
            tags=("graph", "shared-nodes"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.repeat.many_lines",
            "repeat / 5k lines / grid transform",
            "repeat",
            "many_lines",
            {
                "layout": "grid",
                "count": 5,
                "offset": [12.0, 7.0, 0.0],
                "rotation_step": [0.0, 0.0, 7.0],
                "scale": [0.98, 1.01, 1.0],
                "cumulative_offset": True,
                "cumulative_rotate": True,
                "cumulative_scale": True,
            },
            "more_vertices",
            tags=("copy", "many-short-lines"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.resample.polyline_spaced_long",
            "resample / spaced 50k vertices / step 0.5",
            "resample",
            "polyline_spaced_long",
            {"step": 0.5, "closed": "open"},
            "fewer_vertices",
            tags=("topology-changing", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.resample.upsample.polyline_spaced_long",
            "resample / spaced 50k vertices / step 0.1",
            "resample",
            "polyline_spaced_long",
            {"step": 0.1, "closed": "open"},
            "more_vertices",
            tags=("topology-changing", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.simplify.polyline_long",
            "simplify / 50k vertices / tolerance 0.05",
            "simplify",
            "polyline_long",
            {"tolerance": 0.05, "closed": "open"},
            "fewer_vertices",
            tags=("topology-changing", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.trim.polyline_long",
            "trim / 50k vertices / interior range",
            "trim",
            "polyline_long",
            {"start_param": 0.17, "end_param": 0.83},
            "fewer_vertices",
            tags=("topology-changing", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.twist.polyline_long",
            "twist / 50k vertices / arbitrary 3D axis",
            "twist",
            "polyline_long",
            {"angle": 137.0, "axis_dir": [1.0, 2.0, 3.0]},
            "changed",
            tags=("coordinate-only", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.warp.binary_long_mask",
            "warp / 50k base vertices / lens",
            "warp",
            "binary_long_mask",
            {
                "mode": "lens",
                "kind": "rotate",
                "profile": "band",
                "band": 20.0,
                "angle": 31.0,
                "strength": 0.85,
            },
            "changed",
            tags=("binary", "distance-field", "large"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.weave.ring_weave",
            "weave / closed 800-segment ring",
            "weave",
            "ring_weave",
            {
                "num_candidate_lines": 100,
                "relaxation_iterations": 15,
                "step": 0.125,
            },
            "changed",
            tags=("graph", "ring"),
        ),
        RemainingEffectBenchmarkCase(
            "effect.remaining.wobble.polyline_long",
            "wobble / 50k vertices / XYZ",
            "wobble",
            "polyline_long",
            {
                "amplitude": [2.0, 3.0, 4.0],
                "frequency": [0.1, 0.07, 0.13],
                "phase": 0.37,
            },
            "changed",
            tags=("coordinate-only", "large"),
        ),
    )
    common_tags = ("actual-work", "direct-evaluator", "exact-checksum")
    return tuple(
        RemainingEffectBenchmarkCase(
            case_id=case.case_id,
            label=case.label,
            effect=case.effect,
            fixture=case.fixture,
            arguments=case.arguments,
            work_kind=case.work_kind,
            quality=case.quality,
            tags=(*common_tags, *case.tags),
            selectable_suites=(
                *case.selectable_suites,
                *(("effects-remaining-jit",) if case.effect in _JIT_EFFECTS else ()),
                *(("effects-remaining-cold",) if case.effect in _PROCESS_COLD_EFFECTS else ()),
            ),
        )
        for case in cases
    )


def setup_remaining_effect_benchmark(
    parameters: dict[str, Any],
    seed: int,
) -> RemainingEffectBenchmarkState:
    """evaluator、fixture、引数、baseline 契約を timer 外で準備する。"""

    from grafix.core.builtins import builtin_operation_catalog
    from grafix.devtools.benchmarks.metrics import geometry_checksum

    effect_name = str(parameters["effect"])
    if effect_name in _EXCLUDED_EFFECTS:
        raise ValueError(f"excluded effect in remaining benchmark: {effect_name}")
    fixture = str(parameters["fixture"])
    inputs = _build_inputs(fixture=fixture, seed=int(seed))

    spec = builtin_operation_catalog().resolve("effect", effect_name)
    arguments = dict(spec.schema.defaults)
    for name, value in cast(dict[str, Any], parameters["arguments"]).items():
        meta = spec.schema.meta.get(name)
        if meta is not None and meta.kind in {"vec3", "rgb"}:
            if not isinstance(value, list) or len(value) != 3:
                raise TypeError(
                    f"{effect_name}.{name} benchmark argument must be a three-item JSON array"
                )
            value = tuple(value)
        arguments[name] = value
    args_tuple = tuple(sorted(arguments.items()))

    quality = cast(PreviewQuality, str(parameters["quality"]))
    if quality not in {"draft", "final"}:
        raise ValueError(f"unknown effect benchmark quality: {quality!r}")

    package_dir = Path(__file__).resolve().parents[2]
    effect_path = package_dir / "core" / "effects" / f"{effect_name}.py"
    geometry_kernels_path = package_dir / "core" / "geometry_kernels"
    return RemainingEffectBenchmarkState(
        case_id=str(parameters["case_id"]),
        effect=effect_name,
        evaluator=spec.evaluator,
        inputs=inputs,
        arguments=args_tuple,
        quality=quality,
        work_kind=str(parameters["work_kind"]),
        expected_checksum=cast(str | None, parameters.get("expected_checksum")),
        expected_diagnostics=cast(
            list[list[object]] | None,
            parameters.get("expected_diagnostics"),
        ),
        expected_warnings=cast(
            list[list[str]] | None,
            parameters.get("expected_warnings"),
        ),
        expected_layout=cast(
            dict[str, object] | None,
            parameters.get("expected_layout"),
        ),
        expected_alias=cast(
            dict[str, bool] | None,
            parameters.get("expected_alias"),
        ),
        input_checksums=tuple(geometry_checksum(value) for value in inputs),
        input_snapshots=tuple(_geometry_mutation_snapshot(value) for value in inputs),
        effect_source_sha256=_file_sha256(effect_path),
        geometry_kernels_source_sha256=_python_sources_sha256(geometry_kernels_path),
    )


@contextmanager
def remaining_effect_measurement_context(
    state: object,
) -> Iterator[object]:
    """quality/diagnostic context を timed sample 群の外側で一度だけ開く。"""

    effect_state = cast(RemainingEffectBenchmarkState, state)
    with preview_quality_context(effect_state.quality):
        with operation_diagnostic_context() as diagnostic_buffer:
            effect_state.diagnostic_buffer = diagnostic_buffer
            try:
                yield None
            finally:
                effect_state.diagnostic_buffer = None


def run_remaining_effect(state: object) -> object:
    """timed 区間では登録済み effect evaluator だけを呼び出す。"""

    effect_state = cast(RemainingEffectBenchmarkState, state)
    return effect_state.evaluator(effect_state.inputs, effect_state.arguments)


def observe_remaining_effect_output(
    state: object,
    output: object,
) -> BenchmarkOutput:
    """raw evaluator output を timer 外で exact 検証・要約する。"""

    from grafix.devtools.benchmarks.metrics import geometry_checksum

    effect_state = cast(RemainingEffectBenchmarkState, state)
    if not isinstance(output, RealizedGeometry):
        raise TypeError(f"{effect_state.effect} evaluator output must be RealizedGeometry")

    timed_geometry = output
    timed_checksum = geometry_checksum(timed_geometry)
    diagnostic_objects = (
        () if effect_state.diagnostic_buffer is None else effect_state.diagnostic_buffer.snapshot()
    )
    timed_diagnostics = _diagnostic_values(diagnostic_objects)

    with warnings.catch_warnings(record=True) as warning_records:
        warnings.simplefilter("always")
        with operation_diagnostic_context() as diagnostic_buffer:
            with preview_quality_context(effect_state.quality):
                repeated_geometry = effect_state.evaluator(
                    effect_state.inputs,
                    effect_state.arguments,
                )
    repeated_checksum = geometry_checksum(repeated_geometry)
    repeated_diagnostics = _diagnostic_values(diagnostic_buffer.snapshot())
    repeated_warnings = [
        [record.category.__name__, str(record.message)] for record in warning_records
    ]

    layout = _layout_values(timed_geometry)
    alias = _alias_values(timed_geometry, effect_state.inputs)
    repeated_alias = _alias_values(repeated_geometry, effect_state.inputs)
    inputs_unchanged = (
        tuple(_geometry_mutation_snapshot(value) for value in effect_state.inputs)
        == effect_state.input_snapshots
    )
    offsets_valid = _offsets_are_valid(timed_geometry)
    actual_work = timed_checksum != effect_state.input_checksums[0]
    work_contract = _work_contract_passed(
        effect_state.work_kind,
        input_geometry=effect_state.inputs[0],
        output_geometry=timed_geometry,
    )

    metrics = [
        _metric("effect", "gauge", "text", effect_state.effect),
        _metric("quality", "gauge", "text", effect_state.quality),
        _metric(
            "input_vertices",
            "counter",
            "count",
            sum(int(value.coords.shape[0]) for value in effect_state.inputs),
        ),
        _metric(
            "input_lines",
            "counter",
            "count",
            sum(int(value.offsets.size - 1) for value in effect_state.inputs),
        ),
        _metric(
            "n_vertices",
            "counter",
            "count",
            int(timed_geometry.coords.shape[0]),
        ),
        _metric(
            "n_lines",
            "counter",
            "count",
            int(timed_geometry.offsets.size - 1),
        ),
        _metric(
            "closed_lines",
            "counter",
            "count",
            _closed_line_count(timed_geometry),
        ),
        _metric(
            "output_bytes",
            "counter",
            "bytes",
            timed_geometry.byte_size,
        ),
        _metric("actual_work", "gauge", "boolean", actual_work),
        _metric(
            "diagnostics",
            "counter",
            "count",
            len(timed_diagnostics),
        ),
        _metric(
            "effect_source_sha256",
            "gauge",
            "text",
            effect_state.effect_source_sha256,
        ),
        _metric(
            "geometry_kernels_source_sha256",
            "gauge",
            "text",
            effect_state.geometry_kernels_source_sha256,
        ),
    ]
    metrics.extend(
        _specific_metrics(
            effect_state,
            geometry=timed_geometry,
            diagnostics=diagnostic_objects,
        )
    )

    prefix = f"effect.remaining.{effect_state.effect}.{effect_state.quality}"
    contracts: list[ContractResult] = [
        _contract(
            f"{prefix}.deterministic_geometry",
            timed_checksum,
            repeated_checksum,
            "timed and validation calls keep exact geometry bytes",
        ),
        _contract(
            f"{prefix}.deterministic_diagnostics",
            timed_diagnostics,
            repeated_diagnostics,
            "timed and validation calls keep diagnostic payload and order",
        ),
        _contract(
            f"{prefix}.deterministic_alias",
            alias,
            repeated_alias,
            "timed and validation calls keep identity/alias behavior",
        ),
        _contract(
            f"{prefix}.input_unchanged",
            inputs_unchanged,
            True,
            "effect leaves every immutable input geometry unchanged",
        ),
        _contract(
            f"{prefix}.packed_layout",
            bool(
                timed_geometry.coords.dtype == np.float32
                and timed_geometry.offsets.dtype == np.int32
                and timed_geometry.coords.ndim == 2
                and timed_geometry.coords.shape[1:] == (3,)
            ),
            True,
            "effect keeps the packed float32/int32 geometry layout",
        ),
        _contract(
            f"{prefix}.offsets",
            offsets_valid,
            True,
            "offsets remain monotonic and cover all output vertices",
        ),
        _contract(
            f"{prefix}.actual_work",
            actual_work,
            True,
            "primary fixture must not silently become a no-op",
        ),
        _contract(
            f"{prefix}.work_kind",
            work_contract,
            True,
            f"primary fixture satisfies {effect_state.work_kind!r}",
        ),
    ]
    _append_expected_contract(
        contracts,
        contract_id=f"{prefix}.baseline_checksum",
        actual=timed_checksum,
        expected=effect_state.expected_checksum,
        reason="geometry remains exact against the immutable baseline",
    )
    _append_expected_contract(
        contracts,
        contract_id=f"{prefix}.baseline_diagnostics",
        actual=timed_diagnostics,
        expected=effect_state.expected_diagnostics,
        reason="diagnostic payload and order remain exact against baseline",
    )
    _append_expected_contract(
        contracts,
        contract_id=f"{prefix}.baseline_warnings",
        actual=repeated_warnings,
        expected=effect_state.expected_warnings,
        reason="warning category/message/order remain exact against baseline",
    )
    _append_expected_contract(
        contracts,
        contract_id=f"{prefix}.baseline_layout",
        actual=layout,
        expected=effect_state.expected_layout,
        reason="array layout/writeability remain exact against baseline",
    )
    _append_expected_contract(
        contracts,
        contract_id=f"{prefix}.baseline_alias",
        actual=alias,
        expected=effect_state.expected_alias,
        reason="identity and array alias behavior remain exact against baseline",
    )
    return BenchmarkOutput(
        value=timed_geometry,
        metrics=tuple(metrics),
        contracts=tuple(contracts),
    )


def target_remaining_effect_names() -> frozenset[str]:
    """除外 5 件を含まない benchmark 対象集合を返す。"""

    return frozenset(case.effect for case in remaining_effect_benchmark_cases())


def _build_inputs(*, fixture: str, seed: int) -> tuple[RealizedGeometry, ...]:
    from grafix.devtools.benchmarks.cases import build_default_cases

    defaults = {case.case_id: case.inputs for case in build_default_cases(seed=int(seed))}
    if fixture in defaults:
        return defaults[fixture]
    if fixture == "rings_medium":
        return (_two_rings(outer_sides=256, inner_sides=128),)
    if fixture == "ring_weave":
        return (_regular_polygon_ring(n_sides=800, radius=120.0),)
    if fixture == "shared_network":
        return (_shared_grid_network(nx=50, ny=50),)
    if fixture == "binary_long_mask":
        return (defaults["polyline_long"][0], _two_rings(256, 128))
    if fixture == "binary_regions":
        return _binary_regions_with_hole()
    if fixture == "dedup_duplicates":
        return (
            _duplicated_segment_chain(
                n_segments=12_000,
                copies=4,
            ),
        )
    raise ValueError(f"unknown remaining effect fixture: {fixture!r}")


def _regular_polygon_ring(*, n_sides: int, radius: float) -> RealizedGeometry:
    sides = max(3, int(n_sides))
    angles = np.linspace(
        0.0,
        2.0 * np.pi,
        num=sides,
        endpoint=False,
        dtype=np.float64,
    )
    coords = np.empty((sides + 1, 3), dtype=np.float32)
    coords[:-1, 0] = (float(radius) * np.cos(angles)).astype(np.float32)
    coords[:-1, 1] = (float(radius) * np.sin(angles)).astype(np.float32)
    coords[:-1, 2] = np.float32(0.0)
    coords[-1] = coords[0]
    return RealizedGeometry(
        coords=coords,
        offsets=np.asarray([0, sides + 1], dtype=np.int32),
    )


def _two_rings(outer_sides: int, inner_sides: int) -> RealizedGeometry:
    outer = _regular_polygon_ring(n_sides=outer_sides, radius=150.0)
    inner = _regular_polygon_ring(n_sides=inner_sides, radius=60.0)
    coords = np.concatenate((outer.coords, inner.coords), axis=0)
    offsets = np.asarray(
        [0, outer.coords.shape[0], coords.shape[0]],
        dtype=np.int32,
    )
    return RealizedGeometry(coords=coords, offsets=offsets)


def _shared_grid_network(*, nx: int, ny: int) -> RealizedGeometry:
    x_count = max(2, int(nx))
    y_count = max(2, int(ny))
    xs = np.linspace(-100.0, 100.0, num=x_count, dtype=np.float32)
    ys = np.linspace(-100.0, 100.0, num=y_count, dtype=np.float32)
    points = np.empty((x_count * y_count, 3), dtype=np.float32)
    points[:, 0] = np.tile(xs, y_count)
    points[:, 1] = np.repeat(ys, x_count)
    points[:, 2] = np.float32(0.0)

    edges: list[tuple[int, int]] = []
    for y_index in range(y_count):
        row_start = y_index * x_count
        for x_index in range(x_count - 1):
            edges.append((row_start + x_index, row_start + x_index + 1))
    for y_index in range(y_count - 1):
        row_start = y_index * x_count
        next_start = row_start + x_count
        for x_index in range(x_count):
            edges.append((row_start + x_index, next_start + x_index))

    coords = np.empty((2 * len(edges), 3), dtype=np.float32)
    for edge_index, (start, stop) in enumerate(edges):
        coords[2 * edge_index] = points[start]
        coords[2 * edge_index + 1] = points[stop]
    offsets = np.arange(0, coords.shape[0] + 1, 2, dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _binary_regions_with_hole() -> tuple[RealizedGeometry, RealizedGeometry]:
    """hole を持つ領域と一部だけ重なる正多角形領域を返す。"""

    first = _two_rings(outer_sides=2_048, inner_sides=1_024)
    second = _regular_polygon_ring(n_sides=2_048, radius=120.0)
    second_coords = second.coords.copy()
    second_coords[:, 0] += np.float32(150.0)
    return (
        first,
        RealizedGeometry(coords=second_coords, offsets=second.offsets.copy()),
    )


def _duplicated_segment_chain(
    *,
    n_segments: int,
    copies: int,
) -> RealizedGeometry:
    """各 edge を正逆交互に複製した一本の波状 segment chain を返す。"""

    segment_count = max(1, int(n_segments))
    copy_count = max(2, int(copies))
    x = np.arange(segment_count + 1, dtype=np.float32) * np.float32(0.125)
    y = (8.0 * np.sin(x * np.float32(0.025))).astype(
        np.float32,
        copy=False,
    )
    points = np.zeros((segment_count + 1, 3), dtype=np.float32)
    points[:, 0] = x
    points[:, 1] = y

    segments = np.empty(
        (segment_count, copy_count, 2, 3),
        dtype=np.float32,
    )
    for copy_index in range(copy_count):
        if copy_index % 2 == 0:
            segments[:, copy_index, 0] = points[:-1]
            segments[:, copy_index, 1] = points[1:]
        else:
            segments[:, copy_index, 0] = points[1:]
            segments[:, copy_index, 1] = points[:-1]

    coords = segments.reshape(segment_count * copy_count * 2, 3)
    offsets = np.arange(0, coords.shape[0] + 1, 2, dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _work_contract_passed(
    kind: str,
    *,
    input_geometry: RealizedGeometry,
    output_geometry: RealizedGeometry,
) -> bool:
    input_vertices = int(input_geometry.coords.shape[0])
    output_vertices = int(output_geometry.coords.shape[0])
    input_lines = int(input_geometry.offsets.size - 1)
    output_lines = int(output_geometry.offsets.size - 1)
    if kind == "changed":
        return not (
            np.array_equal(input_geometry.coords, output_geometry.coords)
            and np.array_equal(input_geometry.offsets, output_geometry.offsets)
        )
    if kind == "topology_changed":
        return not np.array_equal(
            input_geometry.offsets,
            output_geometry.offsets,
        )
    if kind == "more_vertices":
        return output_vertices > input_vertices
    if kind == "fewer_vertices":
        return output_vertices < input_vertices
    if kind == "more_lines":
        return output_lines > input_lines
    if kind == "fewer_lines":
        return output_lines < input_lines
    raise ValueError(f"unknown actual-work contract: {kind!r}")


def _diagnostic_values(
    diagnostics: Sequence[OperationDiagnostic],
) -> list[list[object]]:
    return [
        [
            diagnostic.op,
            _json_value(diagnostic.original_value),
            _json_value(diagnostic.effective_value),
            diagnostic.reason,
            diagnostic.severity,
        ]
        for diagnostic in diagnostics
    ]


def _json_value(value: object) -> object:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _layout_values(geometry: RealizedGeometry) -> dict[str, object]:
    return {
        "coords_dtype": geometry.coords.dtype.str,
        "offsets_dtype": geometry.offsets.dtype.str,
        "coords_strides": list(geometry.coords.strides),
        "offsets_strides": list(geometry.offsets.strides),
        "coords_c_contiguous": bool(geometry.coords.flags.c_contiguous),
        "offsets_c_contiguous": bool(geometry.offsets.flags.c_contiguous),
        "coords_f_contiguous": bool(geometry.coords.flags.f_contiguous),
        "offsets_f_contiguous": bool(geometry.offsets.flags.f_contiguous),
        "coords_writeable": bool(geometry.coords.flags.writeable),
        "offsets_writeable": bool(geometry.offsets.flags.writeable),
        "coords_owndata": bool(geometry.coords.flags.owndata),
        "offsets_owndata": bool(geometry.offsets.flags.owndata),
        "coords_aligned": bool(geometry.coords.flags.aligned),
        "offsets_aligned": bool(geometry.offsets.flags.aligned),
    }


def _alias_values(
    geometry: RealizedGeometry,
    inputs: Sequence[RealizedGeometry],
) -> dict[str, bool]:
    return {
        "output_is_input": any(geometry is value for value in inputs),
        "coords_is_input": any(geometry.coords is value.coords for value in inputs),
        "offsets_is_input": any(geometry.offsets is value.offsets for value in inputs),
        "coords_alias_input": any(
            np.shares_memory(geometry.coords, value.coords) for value in inputs
        ),
        "offsets_alias_input": any(
            np.shares_memory(geometry.offsets, value.offsets) for value in inputs
        ),
    }


def _array_mutation_snapshot(array: np.ndarray) -> _ArrayMutationSnapshot:
    """ndarray の内容と、checksum が捨てる layout/flags を同時に保存する。"""

    return _ArrayMutationSnapshot(
        dtype=array.dtype.str,
        shape=tuple(int(value) for value in array.shape),
        strides=tuple(int(value) for value in array.strides),
        c_contiguous=bool(array.flags.c_contiguous),
        f_contiguous=bool(array.flags.f_contiguous),
        writeable=bool(array.flags.writeable),
        owndata=bool(array.flags.owndata),
        aligned=bool(array.flags.aligned),
        raw_bytes=array.tobytes(order="A"),
    )


def _geometry_mutation_snapshot(
    geometry: RealizedGeometry,
) -> _GeometryMutationSnapshot:
    """RealizedGeometry の mutation-sensitive snapshot を返す。"""

    return _GeometryMutationSnapshot(
        coords=_array_mutation_snapshot(geometry.coords),
        offsets=_array_mutation_snapshot(geometry.offsets),
    )


def _offsets_are_valid(geometry: RealizedGeometry) -> bool:
    offsets = geometry.offsets
    return bool(
        offsets.ndim == 1
        and offsets.size >= 1
        and int(offsets[0]) == 0
        and int(offsets[-1]) == int(geometry.coords.shape[0])
        and not np.any(np.diff(offsets) < 0)
    )


def _closed_line_count(geometry: RealizedGeometry) -> int:
    count = 0
    for start, stop in zip(
        geometry.offsets[:-1],
        geometry.offsets[1:],
        strict=True,
    ):
        points = geometry.coords[int(start) : int(stop)]
        if points.shape[0] >= 2 and np.array_equal(points[0], points[-1]):
            count += 1
    return count


def _metric(name: str, kind: str, unit: str, value: object) -> Metric:
    return Metric(
        name=name,
        kind=kind,
        unit=unit,
        phase="measure",
        scope="effect",
        value=value,
    )


def _specific_metrics(
    state: RemainingEffectBenchmarkState,
    *,
    geometry: RealizedGeometry,
    diagnostics: Sequence[OperationDiagnostic],
) -> list[Metric]:
    """parameter/output/diagnostic から hot loop 外で work 量を導出する。"""

    args = dict(state.arguments)
    metrics: list[Metric] = []

    def gauge(name: str, value: object, *, unit: str = "unitless") -> None:
        metrics.append(_metric(name, "gauge", unit, value))

    def counter(name: str, value: int, *, unit: str = "count") -> None:
        metrics.append(_metric(name, "counter", unit, int(value)))

    if state.effect == "bold":
        counter("work.copies", int(args["count"]))
    elif state.effect == "repeat":
        gauge("work.layout", str(args["layout"]), unit="text")
        counter("work.copies", int(args["count"]))
    elif state.effect == "mirror":
        counter("work.mirror_planes", int(args["n_mirror"]))
    elif state.effect == "mirror3d":
        mode = str(args["mode"])
        gauge("work.mode", mode, unit="text")
        if mode == "polyhedral":
            group = str(args["group"])
            gauge("work.group", group, unit="text")
            counter("work.rotation_matrices", {"T": 12, "O": 24, "I": 60}[group])
        else:
            counter("work.azimuth_sectors", int(args["n_azimuth"]))
    elif state.effect in {"lowpass", "highpass"}:
        gauge("work.step", float(args["step"]), unit="geometry_units")
        gauge("work.sigma", float(args["sigma"]), unit="geometry_units")
        counter("work.resampled_vertices", int(geometry.coords.shape[0]))
    elif state.effect == "resample":
        gauge("work.step", float(args["step"]), unit="geometry_units")
        gauge("work.closed", str(args["closed"]), unit="text")
        counter("work.resampled_vertices", int(geometry.coords.shape[0]))
    elif state.effect == "simplify":
        input_vertices = sum(int(value.coords.shape[0]) for value in state.inputs)
        gauge(
            "work.tolerance",
            float(args["tolerance"]),
            unit="geometry_units",
        )
        gauge("work.closed", str(args["closed"]), unit="text")
        counter("work.removed_vertices", input_vertices - int(geometry.coords.shape[0]))
    elif state.effect == "deduplicate":
        input_segments = sum(
            max(0, int(stop) - int(start) - 1)
            for value in state.inputs
            for start, stop in zip(
                value.offsets[:-1],
                value.offsets[1:],
                strict=True,
            )
        )
        output_segments = sum(
            max(0, int(stop) - int(start) - 1)
            for start, stop in zip(
                geometry.offsets[:-1],
                geometry.offsets[1:],
                strict=True,
            )
        )
        gauge(
            "work.tolerance",
            float(args["tolerance"]),
            unit="geometry_units",
        )
        gauge("work.merge_chains", bool(args["merge_chains"]), unit="boolean")
        counter("work.input_segments", input_segments)
        counter("work.output_segments", output_segments)
        counter("work.removed_segments", input_segments - output_segments)
    elif state.effect == "boolean":
        gauge("work.mode", str(args["mode"]), unit="text")
        counter(
            "work.input_rings",
            sum(int(value.offsets.size - 1) for value in state.inputs),
        )
        counter("work.output_rings", int(geometry.offsets.size - 1))
    elif state.effect == "offset_curve":
        input_paths = sum(int(value.offsets.size - 1) for value in state.inputs)
        retained_paths = input_paths if bool(args["keep_original"]) else 0
        gauge(
            "work.distance",
            float(args["distance"]),
            unit="geometry_units",
        )
        gauge("work.side", str(args["side"]), unit="text")
        counter("work.levels", int(args["count"]))
        gauge("work.join", str(args["join"]), unit="text")
        gauge(
            "work.keep_original",
            bool(args["keep_original"]),
            unit="boolean",
        )
        counter(
            "work.generated_paths",
            max(0, int(geometry.offsets.size - 1) - retained_paths),
        )
    elif state.effect == "dash":
        gauge("work.dash_length", float(args["dash_length"]), unit="geometry_units")
        gauge("work.gap_length", float(args["gap_length"]), unit="geometry_units")
    elif state.effect == "growth":
        requested = int(args["iters"])
        gauge("work.iterations.requested", requested, unit="count")
        gauge(
            "work.iterations.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="growth.iters",
                requested=requested,
            ),
            unit="count",
        )
    elif state.effect == "reaction_diffusion":
        requested_steps = int(args["steps"])
        requested_pitch = float(args["grid_pitch"])
        gauge("work.steps.requested", requested_steps, unit="count")
        gauge(
            "work.steps.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="reaction_diffusion.steps",
                requested=requested_steps,
            ),
            unit="count",
        )
        gauge(
            "work.grid_pitch.requested",
            requested_pitch,
            unit="geometry_units",
        )
        gauge(
            "work.grid_pitch.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="reaction_diffusion.grid_pitch",
                requested=requested_pitch,
            ),
            unit="geometry_units",
        )
    elif state.effect == "metaball":
        requested_pitch = float(args["grid_pitch"])
        requested_segments = sum(
            max(0, int(stop) - int(start) - 1)
            for input_geometry in state.inputs
            for start, stop in zip(
                input_geometry.offsets[:-1],
                input_geometry.offsets[1:],
                strict=True,
            )
        )
        gauge(
            "work.grid_pitch.requested",
            requested_pitch,
            unit="geometry_units",
        )
        gauge(
            "work.grid_pitch.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="metaball.grid_pitch",
                requested=requested_pitch,
            ),
            unit="geometry_units",
        )
        gauge("work.segments.requested", requested_segments, unit="count")
        gauge(
            "work.segments.effective",
            _diagnostic_effective_value(
                diagnostics,
                op="metaball.ring_segments",
                requested=requested_segments,
            ),
            unit="count",
        )
    elif state.effect in {"isocontour", "partition"}:
        if state.effect == "isocontour":
            gauge(
                "work.grid_pitch.requested",
                float(args["grid_pitch"]),
                unit="geometry_units",
            )
        else:
            counter("work.sites.requested", int(args["site_count"]))
    elif state.effect == "weave":
        counter("work.candidates", int(args["num_candidate_lines"]))
        counter(
            "work.relaxation_iterations",
            int(args["relaxation_iterations"]),
        )
    elif state.effect == "relax":
        counter(
            "work.relaxation_iterations",
            int(args["relaxation_iterations"]),
        )

    if state.effect in {
        "boolean",
        "buffer",
        "clip",
        "offset_curve",
        "partition",
    }:
        counter(
            "work.input_paths",
            sum(int(value.offsets.size - 1) for value in state.inputs),
        )
        counter("work.output_paths", int(geometry.offsets.size - 1))
    return metrics


def _diagnostic_effective_value(
    diagnostics: Sequence[OperationDiagnostic],
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


def _contract(
    contract_id: str,
    actual: object,
    expected: object,
    reason: str,
) -> ContractResult:
    return evaluate_contract(
        contract_id=contract_id,
        severity="hard",
        actual=_contract_operand(actual),
        comparator="eq",
        limit=_contract_operand(expected),
        reason=reason,
    )


def _append_expected_contract(
    contracts: list[ContractResult],
    *,
    contract_id: str,
    actual: object,
    expected: object | None,
    reason: str,
) -> None:
    if expected is None:
        return
    contracts.append(_contract(contract_id, actual, expected, reason))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _python_sources_sha256(directory: Path) -> str:
    """directory 直下の Python source 列をファイル名と内容で要約する。"""

    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.py")):
        name = path.relative_to(directory).as_posix().encode("utf-8")
        source = path.read_bytes()
        digest.update(len(name).to_bytes(8, byteorder="big"))
        digest.update(name)
        digest.update(len(source).to_bytes(8, byteorder="big"))
        digest.update(source)
    return digest.hexdigest()


def _contract_operand(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "case_definitions",
    "RemainingEffectBenchmarkCase",
    "RemainingEffectBenchmarkState",
    "observe_remaining_effect_output",
    "remaining_effect_benchmark_cases",
    "remaining_effect_measurement_context",
    "run_remaining_effect",
    "setup_remaining_effect_benchmark",
    "target_remaining_effect_names",
]
