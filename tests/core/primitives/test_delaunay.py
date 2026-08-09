from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pytest
from shapely import Polygon, union_all

import grafix.core.primitives.delaunay as delaunay_module
from grafix import E, G
from grafix.core.primitives.delaunay import (
    _best_candidate_sites,
    _canonical_delaunay_faces,
    _face_filter_scratch_bytes,
    _faces_inside_bounds,
    _sampling_plan,
    _sampling_scratch_bytes,
    delaunay,
)
from grafix.core.realize import realize
from grafix.core.resource_budget import (
    ResourceBudget,
    ResourceLimitError,
    resource_budget_context,
)


def _small(**overrides: object) -> tuple[np.ndarray, np.ndarray]:
    arguments: dict[str, object] = {
        "width": 24.0,
        "height": 18.0,
        "site_count": 20,
        "seed": 271,
        "candidates": 4,
    }
    arguments.update(overrides)
    return delaunay(**arguments)  # type: ignore[arg-type]


def _packed_faces(coords: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    np.testing.assert_array_equal(
        np.diff(offsets),
        np.full((offsets.size - 1,), 4, dtype=np.int32),
    )
    return coords.reshape(-1, 4, 3)


def _triangle_area2(vertices: np.ndarray) -> float:
    a, b, c = vertices[:3, :2].astype(np.float64, copy=False)
    ab = b - a
    ac = c - a
    return float(ab[0] * ac[1] - ab[1] * ac[0])


def _ordered_edge(a: np.ndarray, b: np.ndarray) -> tuple[tuple[float, float], ...]:
    first = (float(a[0]), float(a[1]))
    second = (float(b[0]), float(b[1]))
    return (first, second) if first < second else (second, first)


def _face_key(face: np.ndarray) -> tuple[tuple[float, float], ...]:
    return tuple((float(vertex[0]), float(vertex[1])) for vertex in face[:3])


def _edge_counts(
    faces: np.ndarray,
) -> Counter[tuple[tuple[float, float], ...]]:
    counts: Counter[tuple[tuple[float, float], ...]] = Counter()
    for face in faces:
        triangle = face[:3]
        for index in range(3):
            counts[_ordered_edge(triangle[index], triangle[(index + 1) % 3])] += 1
    return counts


def _triangle_quality(face: np.ndarray) -> float:
    triangle = face[:3, :2].astype(np.float64, copy=False)
    edges = np.roll(triangle, -1, axis=0) - triangle
    denominator = float(np.sum(edges * edges))
    if denominator <= 0.0:
        return 0.0
    twice_area = abs(_triangle_area2(face))
    return 2.0 * math.sqrt(3.0) * twice_area / denominator


def _boundary_min_quality(faces: np.ndarray) -> float:
    counts = _edge_counts(faces)
    qualities = [
        _triangle_quality(face)
        for face in faces
        if any(
            counts[_ordered_edge(face[index], face[(index + 1) % 3])] == 1
            for index in range(3)
        )
    ]
    if not qualities:
        raise AssertionError("境界faceが存在しない")
    return min(qualities)


def _point_inside_or_on_ccw_face(point: np.ndarray, face: np.ndarray) -> bool:
    """CCW三角形の内部または境界上に点があるか返す。"""

    triangle = face[:3, :2].astype(np.float64, copy=False)
    point64 = point.astype(np.float64, copy=False)
    scale = max(1.0, float(np.ptp(triangle, axis=0).max()))
    tolerance = 1e-6 * scale * scale
    for index in range(3):
        start = triangle[index]
        edge = triangle[(index + 1) % 3] - start
        relative = point64 - start
        cross = float(edge[0] * relative[1] - edge[1] * relative[0])
        if cross < -tolerance:
            return False
    return True


def _assert_standard_geometry(coords: np.ndarray, offsets: np.ndarray) -> None:
    assert coords.dtype == np.float32
    assert offsets.dtype == np.int32
    assert coords.ndim == 2 and coords.shape[1] == 3
    assert offsets.ndim == 1 and offsets.size >= 1
    assert coords.flags.c_contiguous and coords.flags.writeable
    assert offsets.flags.c_contiguous and offsets.flags.writeable
    assert int(offsets[0]) == 0
    assert int(offsets[-1]) == int(coords.shape[0])
    assert np.isfinite(coords).all()


def test_delaunay_is_discoverable_with_complete_public_metadata() -> None:
    entry = G.describe("delaunay")

    assert entry.kind == "primitive"
    assert entry.n_inputs == 0
    assert entry.accepted_args == (
        "width",
        "height",
        "site_count",
        "seed",
        "candidates",
        "guard_band",
        "center",
    )
    assert entry.required_args == ()
    assert dict(entry.defaults) == {
        "activate": True,
        "width": 80.0,
        "height": 100.0,
        "site_count": 48,
        "seed": 0,
        "candidates": 8,
        "guard_band": 2.0,
        "center": (0.0, 0.0, 0.0),
    }
    assert all(metadata.description for metadata in entry.meta.values())
    assert entry.meta["site_count"].ui_min == 8
    assert entry.meta["guard_band"].ui_min == 0.0
    assert entry.meta["guard_band"].ui_max == 4.0
    assert entry.description == (
        "仮想site群から独立した閉Delaunay三角形領域を生成する。"
    )
    assert entry.provenance == "grafix.core.primitives.delaunay:delaunay"


def test_sampling_plan_guard_zero_matches_nominal_domain() -> None:
    plan = _sampling_plan(
        width=80.0,
        height=100.0,
        site_count=48,
        guard_band=0.0,
    )

    assert plan.nominal_pitch == pytest.approx(math.sqrt(8_000.0 / 48.0))
    assert plan.guard_margin == 0.0
    assert plan.expanded_width == 80.0
    assert plan.expanded_height == 100.0
    assert plan.expanded_site_count == 48


def test_sampling_plan_guard_two_preserves_nominal_density() -> None:
    width = 80.0
    height = 100.0
    site_count = 48
    guard_band = 2.0
    expected_pitch = math.sqrt(width * height / site_count)
    expected_margin = guard_band * expected_pitch
    expected_width = width + 2.0 * expected_margin
    expected_height = height + 2.0 * expected_margin
    expected_count = math.ceil(
        site_count * expected_width * expected_height / (width * height)
    )

    first = _sampling_plan(
        width=width,
        height=height,
        site_count=site_count,
        guard_band=guard_band,
    )
    second = _sampling_plan(
        width=width,
        height=height,
        site_count=site_count,
        guard_band=guard_band,
    )

    assert first == second
    assert first.nominal_pitch == pytest.approx(expected_pitch)
    assert first.guard_margin == pytest.approx(expected_margin)
    assert first.expanded_width == pytest.approx(expected_width)
    assert first.expanded_height == pytest.approx(expected_height)
    assert first.expanded_site_count == expected_count == 120


def test_delaunay_zero_argument_call_returns_nonempty_standard_geometry() -> None:
    coords, offsets = delaunay()

    _assert_standard_geometry(coords, offsets)
    assert coords.shape[0] > 0
    assert offsets.size > 1


def test_delaunay_returns_canonical_closed_ccw_faces() -> None:
    coords, offsets = _small()

    _assert_standard_geometry(coords, offsets)
    assert coords.shape[0] > 0
    faces = _packed_faces(coords, offsets)
    np.testing.assert_array_equal(
        faces[:, 0].view(np.uint32),
        faces[:, -1].view(np.uint32),
    )

    keys = [_face_key(face) for face in faces]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    for face, key in zip(faces, keys, strict=True):
        assert len(set(key)) == 3
        assert key[0] == min(key)
        assert _triangle_area2(face) > 0.0

    edge_counts = _edge_counts(faces)
    assert max(edge_counts.values()) <= 2
    assert 2 in edge_counts.values()


def test_delaunay_is_byte_deterministic_and_returns_fresh_arrays() -> None:
    first_coords, first_offsets = _small()
    second_coords, second_offsets = _small()

    assert second_coords.tobytes() == first_coords.tobytes()
    assert second_offsets.tobytes() == first_offsets.tobytes()
    assert second_coords is not first_coords
    assert second_offsets is not first_offsets
    assert not np.shares_memory(second_coords, first_coords)
    assert not np.shares_memory(second_offsets, first_offsets)

    second_coords[0, 0] += np.float32(1.0)
    second_offsets[0] = np.int32(1)
    assert second_coords.tobytes() != first_coords.tobytes()
    assert int(first_offsets[0]) == 0


def test_delaunay_guard_zero_matches_unexpanded_triangulation() -> None:
    arguments = {
        "width": 24.0,
        "height": 18.0,
        "site_count": 20,
        "seed": 271,
        "candidates": 4,
        "center": (3.0, -2.0, 1.25),
    }
    sites = _best_candidate_sites(
        width=arguments["width"],
        height=arguments["height"],
        site_count=arguments["site_count"],
        seed=arguments["seed"],
        candidates=arguments["candidates"],
    )
    output_sites = np.ascontiguousarray(
        sites + np.asarray(arguments["center"][:2], dtype=np.float64),
        dtype=np.float32,
    )
    expected_faces = _canonical_delaunay_faces(output_sites)
    face_count = int(expected_faces.shape[0])
    expected_by_face = np.empty((face_count, 4, 3), dtype=np.float32)
    expected_by_face[:, :3, :2] = expected_faces
    expected_by_face[:, :3, 2] = np.float32(arguments["center"][2])
    expected_by_face[:, 3] = expected_by_face[:, 0]
    expected_coords = expected_by_face.reshape((-1, 3))
    expected_offsets = np.arange(0, 4 * face_count + 1, 4, dtype=np.int32)

    coords, offsets = delaunay(**arguments, guard_band=0.0)

    np.testing.assert_array_equal(coords, expected_coords)
    np.testing.assert_array_equal(offsets, expected_offsets)


def test_delaunay_respects_bounds_center_and_z_plane() -> None:
    width = 18.0
    height = 12.0
    center = (7.0, -4.0, 2.5)
    coords, offsets = _small(width=width, height=height, center=center)

    _assert_standard_geometry(coords, offsets)
    assert float(coords[:, 0].min()) >= center[0] - width / 2.0
    assert float(coords[:, 0].max()) <= center[0] + width / 2.0
    assert float(coords[:, 1].min()) >= center[1] - height / 2.0
    assert float(coords[:, 1].max()) <= center[1] + height / 2.0
    np.testing.assert_array_equal(
        coords[:, 2],
        np.full((coords.shape[0],), np.float32(center[2]), dtype=np.float32),
    )

    base_coords, base_offsets = _small(width=width, height=height)
    np.testing.assert_array_equal(offsets, base_offsets)
    np.testing.assert_allclose(
        coords,
        base_coords + np.asarray(center, dtype=np.float32),
        rtol=0.0,
        atol=2e-6,
    )


def test_faces_inside_bounds_is_inclusive_and_preserves_input_order() -> None:
    inside_boundary = np.asarray(
        [[5.0, -7.0], [15.0, -7.0], [10.0, -1.0]],
        dtype=np.float64,
    )
    outside_x = np.asarray(
        [[5.0, -7.0], [15.01, -7.0], [10.0, -1.0]],
        dtype=np.float64,
    )
    inside_inset = np.asarray(
        [[6.0, -6.0], [14.0, -6.0], [10.0, -2.0]],
        dtype=np.float64,
    )
    outside_y = np.asarray(
        [[6.0, -7.01], [14.0, -6.0], [10.0, -2.0]],
        dtype=np.float64,
    )
    faces = np.stack(
        [inside_boundary, outside_x, inside_inset, outside_y],
        axis=0,
    )

    filtered = _faces_inside_bounds(
        faces,
        width=10.0,
        height=6.0,
        center=(10.0, -4.0, 2.0),
    )

    assert filtered.dtype == faces.dtype
    assert filtered.flags.c_contiguous
    np.testing.assert_array_equal(
        filtered,
        np.stack([inside_boundary, inside_inset], axis=0),
    )


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("width", 25.0),
        ("height", 19.0),
        ("site_count", 21),
        ("seed", 272),
        ("candidates", 5),
        ("guard_band", 0.0),
    ),
)
def test_delaunay_generation_parameters_change_output(
    name: str,
    value: object,
) -> None:
    base_coords, base_offsets = _small()
    changed_coords, changed_offsets = _small(**{name: value})

    assert not (
        np.array_equal(changed_coords, base_coords)
        and np.array_equal(changed_offsets, base_offsets)
    )


def test_delaunay_three_sites_returns_one_closed_face() -> None:
    coords, offsets = _small(site_count=3, guard_band=0.0)

    assert offsets.tolist() == [0, 4]
    faces = _packed_faces(coords, offsets)
    assert faces.shape == (1, 4, 3)
    np.testing.assert_array_equal(
        faces[0, 0].view(np.uint32),
        faces[0, -1].view(np.uint32),
    )
    assert _triangle_area2(faces[0]) > 0.0


def test_delaunay_low_site_count_with_guard_can_return_standard_empty() -> None:
    coords, offsets = delaunay(
        width=80.0,
        height=100.0,
        site_count=3,
        seed=0,
        candidates=8,
        guard_band=2.0,
    )

    _assert_standard_geometry(coords, offsets)
    assert coords.shape == (0, 3)
    np.testing.assert_array_equal(offsets, [0])


@pytest.mark.parametrize("candidates", (1, 5))
def test_best_candidate_sites_are_deterministic_bounded_and_prefix_stable(
    candidates: int,
) -> None:
    arguments = {
        "width": 16.0,
        "height": 10.0,
        "seed": 43,
        "candidates": candidates,
    }
    prefix = _best_candidate_sites(site_count=7, **arguments)
    full = _best_candidate_sites(site_count=12, **arguments)
    repeated = _best_candidate_sites(site_count=12, **arguments)

    assert full.dtype == np.float64
    assert full.shape == (12, 2)
    assert np.isfinite(full).all()
    assert float(full[:, 0].min()) >= -8.0
    assert float(full[:, 0].max()) <= 8.0
    assert float(full[:, 1].min()) >= -5.0
    assert float(full[:, 1].max()) <= 5.0
    np.testing.assert_array_equal(full[:7], prefix)
    np.testing.assert_array_equal(repeated, full)


def test_triangle_quality_is_normalized_and_scale_invariant() -> None:
    equilateral = np.asarray(
        [[0.0, 0.0], [2.0, 0.0], [1.0, math.sqrt(3.0)]],
        dtype=np.float64,
    )
    right_isosceles = np.asarray(
        [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        dtype=np.float64,
    )
    sliver = np.asarray(
        [[0.0, 0.0], [1.0, 0.0], [2.0, 1.0e-8]],
        dtype=np.float64,
    )

    assert _triangle_quality(equilateral) == pytest.approx(1.0)
    assert _triangle_quality(13.0 * equilateral + 7.0) == pytest.approx(1.0)
    assert _triangle_quality(right_isosceles) == pytest.approx(
        math.sqrt(3.0) / 2.0
    )
    assert _triangle_quality(sliver) < 1.0e-7


@pytest.mark.parametrize(
    "arguments",
    (
        {
            "width": 80.0,
            "height": 100.0,
            "site_count": 48,
            "seed": 110,
            "candidates": 8,
        },
        {
            "width": 54.0,
            "height": 46.0,
            "site_count": 18,
            "seed": 150,
            "candidates": 8,
        },
    ),
)
def test_default_guard_improves_boundary_quality_above_contract(
    arguments: dict[str, int | float],
) -> None:
    guarded_coords, guarded_offsets = delaunay(**arguments)
    hull_coords, hull_offsets = delaunay(**arguments, guard_band=0.0)
    guarded_faces = _packed_faces(guarded_coords, guarded_offsets)
    hull_faces = _packed_faces(hull_coords, hull_offsets)

    guarded_quality = _boundary_min_quality(guarded_faces)
    hull_quality = _boundary_min_quality(hull_faces)
    assert guarded_quality >= 0.30
    assert guarded_quality > hull_quality


def test_guard_improves_boundary_quality_for_uniform_candidate_fast_path() -> None:
    arguments = {
        "width": 80.0,
        "height": 100.0,
        "site_count": 48,
        "seed": 50,
        "candidates": 1,
    }
    guarded_coords, guarded_offsets = delaunay(**arguments, guard_band=2.0)
    hull_coords, hull_offsets = delaunay(**arguments, guard_band=0.0)

    guarded_quality = _boundary_min_quality(
        _packed_faces(guarded_coords, guarded_offsets)
    )
    hull_quality = _boundary_min_quality(_packed_faces(hull_coords, hull_offsets))
    assert guarded_quality > hull_quality


def test_delaunay_does_not_change_numpy_global_rng_state() -> None:
    np.random.seed(20260809)
    expected = np.random.random(8)
    np.random.seed(20260809)

    _small()
    actual = np.random.random(8)

    np.testing.assert_array_equal(actual, expected)


def test_canonical_faces_match_fixed_non_cocircular_points_and_input_order() -> None:
    sites = np.asarray(
        [
            [0.0, 0.0],
            [4.0, 0.0],
            [0.0, 3.0],
            [1.0, 1.0],
        ],
        dtype=np.float64,
    )
    faces = _canonical_delaunay_faces(sites)
    permuted = _canonical_delaunay_faces(sites[[2, 0, 3, 1]])

    expected = {
        ((0.0, 0.0), (1.0, 1.0), (0.0, 3.0)),
        ((0.0, 0.0), (4.0, 0.0), (1.0, 1.0)),
        ((0.0, 3.0), (1.0, 1.0), (4.0, 0.0)),
    }
    actual = {
        tuple((float(x), float(y)) for x, y in face)
        for face in faces
    }

    assert faces.dtype == np.float64
    assert faces.shape == (3, 3, 2)
    assert actual == expected
    np.testing.assert_array_equal(permuted, faces)


def test_canonical_faces_ignore_exact_duplicate_sites() -> None:
    unique = np.asarray(
        [[0.0, 0.0], [4.0, 0.0], [0.0, 3.0], [1.0, 1.0]],
        dtype=np.float64,
    )
    duplicated = np.concatenate([unique, unique[[0, 3, 3]]], axis=0)

    np.testing.assert_array_equal(
        _canonical_delaunay_faces(duplicated),
        _canonical_delaunay_faces(unique),
    )


@pytest.mark.parametrize(
    "sites",
    (
        np.asarray([[0.0, 0.0], [1.0, 0.0]], dtype=np.float64),
        np.asarray([[2.0, 3.0], [2.0, 3.0], [2.0, 3.0]], dtype=np.float64),
        np.asarray(
            [[-2.0, -2.0], [-1.0, -1.0], [0.0, 0.0], [2.0, 2.0]],
            dtype=np.float64,
        ),
        np.asarray(
            [[0.0, 0.0], [1.0, 0.0], [2.0, np.finfo(np.float64).eps]],
            dtype=np.float64,
        ),
    ),
)
def test_canonical_faces_return_standard_empty_for_degenerate_sites(
    sites: np.ndarray,
) -> None:
    faces = _canonical_delaunay_faces(sites)

    assert faces.dtype == np.float64
    assert faces.shape == (0, 3, 2)


def test_canonical_faces_handle_cocircular_sites_without_fixing_diagonal() -> None:
    sites = np.asarray(
        [[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]],
        dtype=np.float64,
    )
    faces = _canonical_delaunay_faces(sites)

    assert faces.shape == (2, 3, 2)
    assert len({tuple(face.reshape(-1)) for face in faces}) == 2
    areas = []
    for face in faces:
        first_edge = face[1].astype(np.float64) - face[0]
        second_edge = face[2].astype(np.float64) - face[0]
        area2 = first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0]
        areas.append(0.5 * abs(float(area2)))
    np.testing.assert_allclose(sum(areas), 4.0, rtol=0.0, atol=1e-12)


def test_delaunay_retriangulates_sites_at_float32_output_precision() -> None:
    coords, offsets = delaunay(
        width=24.0,
        height=18.0,
        site_count=48,
        seed=0,
        candidates=8,
        center=(3.0e7, 3.0e7, 0.0),
    )
    faces = _packed_faces(coords, offsets)
    polygons = [Polygon(face[:3, :2]) for face in faces]

    assert polygons
    assert all(polygon.is_valid and polygon.area > 0.0 for polygon in polygons)
    total_face_area = sum(polygon.area for polygon in polygons)
    union_area = union_all(polygons).area
    assert total_face_area == pytest.approx(union_area, rel=0.0, abs=1e-9)


@pytest.mark.parametrize(
    "sites",
    (
        np.asarray([[0.0, 0.0], [1.0, 0.0], [0.0, np.nan]], dtype=np.float64),
        np.asarray([[0.0, 0.0], [1.0, 0.0], [0.0, np.inf]], dtype=np.float64),
        np.zeros((3, 3), dtype=np.float64),
    ),
)
def test_canonical_faces_reject_invalid_site_arrays(sites: np.ndarray) -> None:
    with pytest.raises(ValueError):
        _canonical_delaunay_faces(sites)


@pytest.mark.parametrize(
    ("arguments", "error_type"),
    (
        ({"width": 0.0}, ValueError),
        ({"width": -1.0}, ValueError),
        ({"width": float("nan")}, ValueError),
        ({"height": 0.0}, ValueError),
        ({"height": float("inf")}, ValueError),
        ({"site_count": 2}, ValueError),
        ({"site_count": True}, TypeError),
        ({"site_count": 3.0}, TypeError),
        ({"seed": -1}, ValueError),
        ({"seed": False}, TypeError),
        ({"seed": 1.0}, TypeError),
        ({"candidates": 0}, ValueError),
        ({"candidates": True}, TypeError),
        ({"candidates": 2.0}, TypeError),
        ({"guard_band": -1.0}, ValueError),
        ({"guard_band": float("nan")}, ValueError),
        ({"guard_band": float("inf")}, ValueError),
        ({"guard_band": True}, TypeError),
        ({"center": [0.0, 0.0, 0.0]}, TypeError),
        ({"center": (0.0, 0.0)}, TypeError),
        ({"center": (0.0, float("inf"), 0.0)}, ValueError),
    ),
)
def test_delaunay_rejects_invalid_parameters(
    arguments: dict[str, object],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        _small(**arguments)


@pytest.mark.parametrize(
    "arguments",
    (
        {"width": 1.0e40},
        {"height": 1.0e-50},
        {"center": (0.0, 0.0, 1.0e40)},
        {"center": (1.0e20, 0.0, 0.0)},
    ),
)
def test_delaunay_rejects_noncanonical_float32_bounds(
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="float32"):
        _small(**arguments)


def test_delaunay_rejects_noncanonical_expanded_bounds_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delaunay_module, "_best_candidate_sites", _fail_if_called)

    with pytest.raises(ValueError, match="float32"):
        delaunay(width=1.0, height=1.0, site_count=3, guard_band=1.0e40)


def _fail_if_called(*args: object, **kwargs: object) -> np.ndarray:
    del args, kwargs
    raise AssertionError("site散布を実行してはならない")


def test_delaunay_rejects_site_hard_cap_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delaunay_module, "_best_candidate_sites", _fail_if_called)

    with pytest.raises(ResourceLimitError, match="site_count"):
        delaunay(site_count=10_001, guard_band=0.0)


def test_delaunay_rejects_expanded_site_hard_cap_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delaunay_module, "_best_candidate_sites", _fail_if_called)

    with pytest.raises(ResourceLimitError, match="expanded_site_count"):
        delaunay(site_count=9_999, candidates=1, guard_band=2.0)


def test_delaunay_rejects_excessive_candidate_work_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delaunay_module, "_best_candidate_sites", _fail_if_called)

    with pytest.raises(ResourceLimitError, match="candidate"):
        delaunay(site_count=3_800, candidates=8, guard_band=2.0)


def test_delaunay_honors_active_geometry_budget_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delaunay_module, "_best_candidate_sites", _fail_if_called)
    budget = ResourceBudget(
        max_output_vertices=3,
        max_output_lines=1,
        max_output_bytes=1_000,
    )

    with resource_budget_context(budget):
        with pytest.raises(ResourceLimitError, match="resource budget"):
            delaunay(site_count=3)


def test_delaunay_budgets_expanded_arrays_and_face_filter_before_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delaunay_module, "_best_candidate_sites", _fail_if_called)
    plan = _sampling_plan(
        width=80.0,
        height=100.0,
        site_count=3,
        guard_band=2.0,
    )
    maximum_face_count = 2 * plan.expanded_site_count - 5
    geometry_bytes = (
        4 * maximum_face_count * 3 * np.dtype(np.float32).itemsize
        + (maximum_face_count + 1) * np.dtype(np.int32).itemsize
    )
    sampling_bytes = _sampling_scratch_bytes(
        site_count=plan.expanded_site_count,
        candidates=1,
    )
    filter_bytes = _face_filter_scratch_bytes(face_count=maximum_face_count)
    assert filter_bytes > 0
    budget = ResourceBudget(
        max_output_vertices=4 * maximum_face_count,
        max_output_lines=maximum_face_count,
        max_output_bytes=geometry_bytes + sampling_bytes,
    )

    with resource_budget_context(budget):
        with pytest.raises(ResourceLimitError, match="estimated_bytes"):
            delaunay(site_count=3, candidates=1, guard_band=2.0)


def test_delaunay_activate_false_skips_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(delaunay_module, "_best_candidate_sites", _fail_if_called)

    realized = realize(G.delaunay(activate=False))

    assert realized.coords.shape == (0, 3)
    np.testing.assert_array_equal(realized.offsets, [0])


def test_delaunay_composes_with_fill_and_preserves_independent_faces() -> None:
    source = G.delaunay(
        width=24.0,
        height=18.0,
        site_count=16,
        seed=271,
        candidates=4,
    )
    boundary = realize(source)
    hatch_only = realize(
        E.fill(
            angle_sets=1,
            angle=31.0,
            density=80.0,
            remove_boundary=True,
        )(source)
    )
    with_boundary = realize(
        E.fill(
            angle_sets=1,
            angle=31.0,
            density=80.0,
            remove_boundary=False,
        )(source)
    )

    assert hatch_only.coords.shape[0] > 0
    np.testing.assert_array_equal(
        np.diff(hatch_only.offsets),
        np.full((hatch_only.offsets.size - 1,), 2, dtype=np.int32),
    )

    boundary_faces = _packed_faces(boundary.coords, boundary.offsets)
    boundary_keys = {_face_key(face) for face in boundary_faces}
    hatch_midpoints = np.asarray(
        [
            hatch_only.coords[int(start) : int(stop), :2].mean(axis=0)
            for start, stop in zip(
                hatch_only.offsets[:-1],
                hatch_only.offsets[1:],
                strict=True,
            )
        ],
        dtype=np.float32,
    )
    covered_faces = np.zeros((boundary_faces.shape[0],), dtype=np.bool_)
    for midpoint in hatch_midpoints:
        containing = np.asarray(
            [
                _point_inside_or_on_ccw_face(midpoint, face)
                for face in boundary_faces
            ],
            dtype=np.bool_,
        )
        assert np.any(containing)
        covered_faces |= containing
    assert np.all(covered_faces)

    retained_faces = []
    for index, length in enumerate(np.diff(with_boundary.offsets)):
        if int(length) != 4:
            continue
        start = int(with_boundary.offsets[index])
        stop = int(with_boundary.offsets[index + 1])
        retained_faces.append(_face_key(with_boundary.coords[start:stop]))
    assert set(retained_faces) == boundary_keys
    assert len(retained_faces) == len(boundary_keys)


def test_delaunay_composes_with_translate_and_rotate() -> None:
    source = G.delaunay(
        width=16.0,
        height=12.0,
        site_count=12,
        seed=9,
        candidates=3,
    )
    transformed = E.rotate(rotation=(0.0, 0.0, 23.0))(
        E.translate(delta=(3.0, -2.0, 1.5))(source)
    )

    realized = realize(transformed)

    assert realized.coords.dtype == np.float32
    assert realized.offsets.dtype == np.int32
    assert realized.coords.flags.c_contiguous
    assert realized.offsets.flags.c_contiguous
    assert np.isfinite(realized.coords).all()
    assert int(realized.offsets[0]) == 0
    assert int(realized.offsets[-1]) == int(realized.coords.shape[0])
    assert realized.coords.shape[0] > 0
    _packed_faces(realized.coords, realized.offsets)
