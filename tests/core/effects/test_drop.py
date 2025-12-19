"""drop effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


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


def test_drop_interval_drop_mode_respects_offset() -> None:
    g = G.drop_test_lines5()
    out = E.drop(interval=2, offset=1, keep_mode="drop")(g)
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


def test_drop_interval_keep_mode_respects_offset() -> None:
    g = G.drop_test_lines5()
    out = E.drop(interval=2, offset=1, keep_mode="keep")(g)
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

