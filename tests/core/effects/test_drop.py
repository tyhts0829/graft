"""drop effect の実体変換に関するテスト群。"""

from __future__ import annotations

import warnings

import numpy as np
import pytest

import grafix.core.effects.drop as drop_module
from grafix.api import E, G
from grafix.core.effects.drop import drop as drop_impl
from grafix.core.operation_diagnostics import operation_diagnostic_context
from grafix.core.operation_authoring import primitive
from grafix.core.realize import RealizeError, realize
from grafix.core.realized_geometry import GeomTuple


@primitive
def drop_test_lines5() -> GeomTuple:
    """長さ 1〜5 の 2 点ポリラインを 5 本返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [2.0, 1.0, 0.0],
            [0.0, 2.0, 0.0],
            [3.0, 2.0, 0.0],
            [0.0, 3.0, 0.0],
            [4.0, 3.0, 0.0],
            [0.0, 4.0, 0.0],
            [5.0, 4.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 4, 6, 8, 10], dtype=np.int32)
    return coords, offsets


@primitive
def drop_test_lines_and_faces() -> GeomTuple:
    """line/face を混在させたポリライン列を返す。"""
    coords = np.array(
        [
            # line (2)
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            # face (4) perimeter=4 (not explicitly closed)
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            # face (4) perimeter=8 (not explicitly closed)
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [2.0, 2.0, 0.0],
            [0.0, 2.0, 0.0],
            # line (2)
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 6, 10, 12], dtype=np.int32)
    return coords, offsets


@primitive
def drop_test_lines_xneg_xpos() -> GeomTuple:
    """x=-1 と x=+1 に 2 点ポリラインを 1 本ずつ返す。"""
    coords = np.array(
        [
            # line at x=-1
            [-1.0, 0.0, 0.0],
            [-1.0, 1.0, 0.0],
            # line at x=+1
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 4], dtype=np.int32)
    return coords, offsets


@primitive
def drop_test_empty() -> GeomTuple:
    """空ジオメトリを返す。"""
    return np.zeros((0, 3), dtype=np.float32), np.zeros((1,), dtype=np.int32)


def test_drop_interval_drop_mode_respects_index_offset() -> None:
    g = G.drop_test_lines5()
    out = E.drop(interval=2, index_offset=1, keep_mode="drop")(g)
    realized = realize(out)

    expected_coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 2.0, 0.0],
            [3.0, 2.0, 0.0],
            [0.0, 4.0, 0.0],
            [5.0, 4.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected_coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2, 4, 6]


def test_drop_interval_keep_mode_respects_index_offset() -> None:
    g = G.drop_test_lines5()
    out = E.drop(interval=2, index_offset=1, keep_mode="keep")(g)
    realized = realize(out)

    expected_coords = np.array(
        [
            [0.0, 1.0, 0.0],
            [2.0, 1.0, 0.0],
            [0.0, 3.0, 0.0],
            [4.0, 3.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected_coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2, 4]


def test_drop_length_filters_union() -> None:
    g = G.drop_test_lines5()
    out = E.drop(min_length=2.5, max_length=4.5, keep_mode="drop")(g)
    realized = realize(out)

    expected_coords = np.array(
        [
            [0.0, 2.0, 0.0],
            [3.0, 2.0, 0.0],
            [0.0, 3.0, 0.0],
            [4.0, 3.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected_coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2, 4]


def test_drop_probability_is_deterministic_for_same_seed() -> None:
    g = G.drop_test_lines5()
    out1 = E.drop(
        probability_base=(0.5, 0.5, 0.5),
        probability_slope=(0.0, 0.0, 0.0),
        seed=42,
        keep_mode="drop",
    )(g)
    r1 = realize(out1)

    # convenience API は呼び出しごとに一時 session を使うため、同じ DAG を再評価できる。
    r2 = realize(out1)

    np.testing.assert_allclose(r2.coords, r1.coords, rtol=0.0, atol=0.0)
    assert r2.offsets.tolist() == r1.offsets.tolist()


def test_drop_probability_seeded_output_characterization() -> None:
    coords = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0],
         [3.0, 0.0, 0.0], [4.0, 0.0, 0.0], [5.0, 0.0, 0.0]],
        dtype=np.float32,
    )
    offsets = np.array([0, 2, 4, 6], dtype=np.int32)

    out_coords, out_offsets = drop_impl(
        (coords, offsets),
        probability_base=(0.45, 0.0, 0.0),
        seed=17,
    )

    np.testing.assert_array_equal(out_coords, coords[[0, 1, 4, 5]])
    np.testing.assert_array_equal(out_offsets, np.array([0, 2, 4], dtype=np.int32))


def test_drop_all_dropped_returns_empty_geometry() -> None:
    g = G.drop_test_lines5()
    out = E.drop(interval=1, keep_mode="drop")(g)
    realized = realize(out)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def test_drop_probability_clamps_range() -> None:
    g = G.drop_test_lines5()
    base = realize(g)

    out_neg = realize(
        E.drop(
            probability_base=(-1.0, -1.0, -1.0),
            probability_slope=(0.0, 0.0, 0.0),
            keep_mode="drop",
        )(g)
    )
    np.testing.assert_allclose(out_neg.coords, base.coords, rtol=0.0, atol=0.0)
    assert out_neg.offsets.tolist() == base.offsets.tolist()

    out_over = realize(
        E.drop(
            probability_base=(2.0, 2.0, 2.0),
            probability_slope=(0.0, 0.0, 0.0),
            seed=0,
            keep_mode="drop",
        )(g)
    )
    assert out_over.coords.shape == (0, 3)
    assert out_over.offsets.tolist() == [0]


def test_drop_nan_probability_component_keeps_legacy_non_selection() -> None:
    coords, offsets = _many_two_point_lines(10)

    out_coords, out_offsets = drop_impl(
        (coords, offsets),
        probability_base=(float("nan"), 0.5, 0.0),
        seed=0,
    )

    np.testing.assert_array_equal(out_coords, coords)
    np.testing.assert_array_equal(out_offsets, offsets)


def test_drop_unknown_keep_mode_is_rejected_eagerly() -> None:
    with pytest.raises(ValueError, match="keep_mode"):
        E.drop(interval=1, keep_mode="wat")


def test_drop_unknown_by_is_rejected_eagerly() -> None:
    with pytest.raises(ValueError, match="by"):
        E.drop(interval=1, by="wat", keep_mode="drop")


def test_drop_rejects_negative_interval_before_empty_input() -> None:
    with pytest.raises(RealizeError) as exc_info:
        realize(E.drop(interval=-1)(G.drop_test_empty()))

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert "interval" in str(exc_info.value.__cause__)


def test_drop_face_mode_with_lines_only_is_noop() -> None:
    g = G.drop_test_lines5()
    base = realize(g)

    out_coords, out_offsets = drop_impl(
        (base.coords, base.offsets),
        interval=1,
        by="face",
    )

    assert out_coords is base.coords
    assert out_offsets is base.offsets


def test_drop_face_interval_uses_face_index_and_drops_faces_only() -> None:
    g = G.drop_test_lines_and_faces()
    out = E.drop(interval=2, index_offset=0, by="face", keep_mode="drop")(g)
    realized = realize(out)

    expected_coords = np.array(
        [
            # line (kept)
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            # face2 (kept)
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [2.0, 2.0, 0.0],
            [0.0, 2.0, 0.0],
            # line (kept)
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected_coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2, 6, 8]


def test_drop_face_keep_mode_keeps_selected_faces_but_lines_always_remain() -> None:
    g = G.drop_test_lines_and_faces()
    out = E.drop(interval=2, index_offset=0, by="face", keep_mode="keep")(g)
    realized = realize(out)

    expected_coords = np.array(
        [
            # line (kept)
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            # face1 (kept)
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
            [0.0, 1.0, 0.0],
            # line (kept)
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected_coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2, 6, 8]


def test_drop_face_length_uses_closed_perimeter() -> None:
    g = G.drop_test_lines_and_faces()
    base = realize(g)
    out = realize(E.drop(min_length=3.5, by="face", keep_mode="drop")(g))

    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_drop_face_probability_one_drops_all_faces_but_keeps_lines() -> None:
    g = G.drop_test_lines_and_faces()
    out = E.drop(
        probability_base=(1.0, 1.0, 1.0),
        probability_slope=(0.0, 0.0, 0.0),
        seed=0,
        by="face",
        keep_mode="drop",
    )(g)
    realized = realize(out)

    expected_coords = np.array(
        [
            # line
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            # line
            [0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected_coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2, 4]


def test_drop_probability_position_gradient_by_x() -> None:
    g = G.drop_test_lines_xneg_xpos()
    out = E.drop(
        probability_base=(0.5, 0.0, 0.0),
        probability_slope=(0.5, 0.0, 0.0),
        seed=0,
        keep_mode="drop",
    )(g)
    realized = realize(out)

    expected_coords = np.array(
        [
            [-1.0, 0.0, 0.0],
            [-1.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(realized.coords, expected_coords, rtol=0.0, atol=1e-6)
    assert realized.offsets.tolist() == [0, 2]


def _many_two_point_lines(n_lines: int = 512) -> GeomTuple:
    rng = np.random.default_rng(20260719)
    starts = rng.uniform(-100.0, 100.0, size=(n_lines, 3)).astype(np.float32)
    delta = rng.normal(0.0, 3.0, size=(n_lines, 3)).astype(np.float32)
    points = np.empty((n_lines, 2, 3), dtype=np.float32)
    points[:, 0] = starts
    points[:, 1] = starts + delta
    coords = points.reshape(-1, 3)
    offsets = np.arange(0, 2 * n_lines + 1, 2, dtype=np.int32)
    return coords, offsets


@pytest.mark.parametrize(
    "arguments",
    (
        {
            "interval": 3,
            "index_offset": 1,
            "probability_base": (0.15, 0.15, 0.15),
            "seed": 20260719,
        },
        {
            "interval": 7,
            "index_offset": -5,
            "min_length": 2.5,
            "max_length": 6.0,
            "probability_base": (0.2, 0.3, 0.1),
            "probability_slope": (0.5, -0.25, 0.125),
            "seed": 12345,
            "keep_mode": "keep",
        },
        {
            "interval": 10_000,
            "index_offset": 9_999,
            "keep_mode": "drop",
        },
        {
            "interval": 1,
            "keep_mode": "drop",
        },
    ),
)
def test_drop_many_two_point_fast_path_matches_size_fallback_bitwise(
    arguments: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coords, offsets = _many_two_point_lines()
    coords.setflags(write=False)
    offsets.setflags(write=False)
    input_bytes = coords.tobytes()

    actual_coords, actual_offsets = drop_impl(
        (coords, offsets),
        **arguments,
    )
    monkeypatch.setattr(
        drop_module,
        "_TWO_POINT_FAST_PATH_MAX_LINES",
        int(offsets.size) - 2,
    )
    expected_coords, expected_offsets = drop_impl(
        (coords, offsets),
        **arguments,
    )

    np.testing.assert_array_equal(
        actual_coords.view(np.uint32),
        expected_coords.view(np.uint32),
    )
    np.testing.assert_array_equal(actual_offsets, expected_offsets)
    assert actual_coords.dtype == expected_coords.dtype == np.float32
    assert actual_offsets.dtype == expected_offsets.dtype == np.int32
    assert actual_coords.flags.c_contiguous
    assert actual_offsets.flags.c_contiguous
    assert not np.shares_memory(actual_coords, coords)
    assert not np.shares_memory(actual_offsets, offsets)
    assert coords.tobytes() == input_bytes
    assert not coords.flags.writeable
    assert not offsets.flags.writeable


def test_drop_two_point_resource_limit_covers_primary_with_bounded_scratch() -> None:
    estimated_peak = drop_module._TWO_POINT_FAST_PATH_MAX_LINES * 192

    assert estimated_peak <= 8 * 1024 * 1024
    assert (
        drop_module._TWO_POINT_FAST_PATH_MIN_LINES
        <= 5_000
        <= drop_module._TWO_POINT_FAST_PATH_MAX_LINES
    )


def test_drop_two_point_resource_limit_boundary_matches_fallback_observably(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n_lines = 128
    coords, offsets = _many_two_point_lines(n_lines)
    coords.setflags(write=False)
    offsets.setflags(write=False)
    before = (coords.tobytes(), offsets.tobytes())
    monkeypatch.setattr(
        drop_module,
        "_TWO_POINT_FAST_PATH_MAX_LINES",
        n_lines,
    )

    original_pack = drop_module._pack_uniform_two_point_lines
    pack_calls = 0

    def _spy_pack(
        input_coords: np.ndarray,
        keep_mask: np.ndarray,
    ) -> GeomTuple:
        nonlocal pack_calls
        pack_calls += 1
        return original_pack(input_coords, keep_mask)

    monkeypatch.setattr(drop_module, "_pack_uniform_two_point_lines", _spy_pack)
    arguments = {
        "interval": 7,
        "index_offset": -5,
        "min_length": 2.5,
        "max_length": 6.0,
        "probability_base": (0.2, 0.3, 0.1),
        "probability_slope": (0.5, -0.25, 0.125),
        "seed": 12345,
        "keep_mode": "keep",
    }
    with (
        warnings.catch_warnings(record=True) as fast_warnings,
        operation_diagnostic_context() as fast_diagnostics,
    ):
        warnings.simplefilter("always")
        fast = drop_module.drop((coords, offsets), **arguments)
    assert pack_calls == 1

    monkeypatch.setattr(
        drop_module,
        "_TWO_POINT_FAST_PATH_MAX_LINES",
        n_lines - 1,
    )
    with (
        warnings.catch_warnings(record=True) as fallback_warnings,
        operation_diagnostic_context() as fallback_diagnostics,
    ):
        warnings.simplefilter("always")
        fallback = drop_module.drop((coords, offsets), **arguments)

    assert pack_calls == 1
    np.testing.assert_array_equal(
        fast[0].view(np.uint32),
        fallback[0].view(np.uint32),
    )
    np.testing.assert_array_equal(fast[1], fallback[1])
    assert fast[0].dtype == fallback[0].dtype == np.float32
    assert fast[1].dtype == fallback[1].dtype == np.int32
    assert fast[0].flags.c_contiguous
    assert fallback[0].flags.c_contiguous
    assert not np.shares_memory(fast[0], coords)
    assert not np.shares_memory(fallback[0], coords)
    assert [str(item.message) for item in fast_warnings] == [
        str(item.message) for item in fallback_warnings
    ]
    assert [item.category for item in fast_warnings] == [
        item.category for item in fallback_warnings
    ]
    assert fast_diagnostics.snapshot() == fallback_diagnostics.snapshot()
    assert (coords.tobytes(), offsets.tobytes()) == before
    assert not coords.flags.writeable
    assert not offsets.flags.writeable
