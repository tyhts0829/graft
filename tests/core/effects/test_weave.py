"""weave effect のウェブ生成に関するテスト群。"""

from __future__ import annotations

import numpy as np

from grafix.api import E, G
from grafix.core.primitive_registry import primitive
from grafix.core.realize import realize
from grafix.core.realized_geometry import RealizedGeometry


@primitive
def weave_test_square() -> RealizedGeometry:
    """一辺 10 の正方形（閉ポリライン）を返す。"""
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
            [0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([0, coords.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def weave_test_empty() -> RealizedGeometry:
    """空のジオメトリを返す。"""
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive
def weave_test_open_long() -> RealizedGeometry:
    """長い開ポリラインを返す（weave は閉曲線のみ対象）。"""
    n = 50_000
    x = np.arange(n, dtype=np.float32)
    coords = np.stack([x, np.zeros_like(x), np.zeros_like(x)], axis=1)
    offsets = np.array([0, n], dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def test_weave_empty_geometry_is_noop() -> None:
    g = G.weave_test_empty()
    out = realize(E.weave(num_candidate_lines=10, relaxation_iterations=10, step=0.1)(g))

    assert out.coords.shape == (0, 3)
    assert out.offsets.tolist() == [0]


def test_weave_zero_candidates_is_near_noop() -> None:
    g = G.weave_test_square()
    base = realize(g)

    out = realize(E.weave(num_candidate_lines=0, relaxation_iterations=0, step=0.5)(g))
    np.testing.assert_allclose(out.coords, base.coords, rtol=0.0, atol=1e-6)
    assert out.offsets.tolist() == base.offsets.tolist()


def test_weave_open_polyline_is_noop() -> None:
    g = G.weave_test_open_long()
    base = realize(g)
    out = realize(E.weave(num_candidate_lines=10, relaxation_iterations=10, step=0.1)(g))

    assert out is base


def test_weave_generates_more_than_boundary() -> None:
    g = G.weave_test_square()
    base = realize(g)

    out = realize(E.weave(num_candidate_lines=3, relaxation_iterations=5, step=0.125)(g))

    assert np.isfinite(out.coords).all()
    assert out.coords.shape[0] >= base.coords.shape[0]
    assert (len(out.offsets) - 1) > (len(base.offsets) - 1)


def test_weave_clamps_parameters_without_crashing() -> None:
    g = G.weave_test_square()
    out = realize(E.weave(num_candidate_lines=9999, relaxation_iterations=-999, step=999.0)(g))

    assert np.isfinite(out.coords).all()
    assert out.offsets[0] == 0
    assert out.offsets[-1] == out.coords.shape[0]
