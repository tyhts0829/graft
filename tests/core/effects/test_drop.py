"""drop effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.effects.drop import drop as drop_impl


@primitive
def drop_test_lines5() -> RealizedGeometry:
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
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def drop_test_lines_and_faces() -> RealizedGeometry:
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
    return RealizedGeometry(coords=coords, offsets=offsets)


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
    out1 = E.drop(probability=0.5, seed=42, keep_mode="drop")(g)
    r1 = realize(out1)

    # realize_cache を回避して同じ計算を再実行するため、別 ID の no-op ノードを噛ませる。
    g2 = E.translate(delta=(0.0, 0.0, 0.0))(g)
    out2 = E.drop(probability=0.5, seed=42, keep_mode="drop")(g2)
    r2 = realize(out2)

    np.testing.assert_allclose(r2.coords, r1.coords, rtol=0.0, atol=0.0)
    assert r2.offsets.tolist() == r1.offsets.tolist()


def test_drop_all_dropped_returns_empty_geometry() -> None:
    g = G.drop_test_lines5()
    out = E.drop(interval=1, keep_mode="drop")(g)
    realized = realize(out)

    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def test_drop_probability_clamps_range() -> None:
    g = G.drop_test_lines5()
    base = realize(g)

    out_neg = realize(E.drop(probability=-0.25, keep_mode="drop")(g))
    np.testing.assert_allclose(out_neg.coords, base.coords, rtol=0.0, atol=0.0)
    assert out_neg.offsets.tolist() == base.offsets.tolist()

    out_over = realize(E.drop(probability=2.0, seed=0, keep_mode="drop")(g))
    assert out_over.coords.shape == (0, 3)
    assert out_over.offsets.tolist() == [0]


def test_drop_probability_non_finite_is_noop_in_impl() -> None:
    g = G.drop_test_lines5()
    base = realize(g)

    out_nan = drop_impl([base], probability=float("nan"))
    np.testing.assert_allclose(out_nan.coords, base.coords, rtol=0.0, atol=0.0)
    assert out_nan.offsets.tolist() == base.offsets.tolist()

    out_inf = drop_impl([base], probability=float("inf"))
    np.testing.assert_allclose(out_inf.coords, base.coords, rtol=0.0, atol=0.0)
    assert out_inf.offsets.tolist() == base.offsets.tolist()


def test_drop_unknown_keep_mode_is_noop() -> None:
    g = G.drop_test_lines5()
    base = realize(g)
    out = realize(E.drop(interval=1, keep_mode="wat")(g))
    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_drop_unknown_by_is_noop() -> None:
    g = G.drop_test_lines5()
    base = realize(g)
    out = realize(E.drop(interval=1, by="wat", keep_mode="drop")(g))
    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=0.0)
    assert out.offsets.tolist() == base.offsets.tolist()


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
    out = E.drop(probability=1.0, seed=0, by="face", keep_mode="drop")(g)
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
