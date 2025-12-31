"""core.effects.bold をテスト。"""

from __future__ import annotations

import numpy as np

from grafix.core.effects.bold import bold
from grafix.core.realized_geometry import RealizedGeometry


def _geometry(*, coords: list[tuple[float, float, float]], offsets: list[int]) -> RealizedGeometry:
    c = np.asarray(coords, dtype=np.float32)
    o = np.asarray(offsets, dtype=np.int32)
    return RealizedGeometry(coords=c, offsets=o)


def test_bold_empty_inputs_returns_empty_geometry() -> None:
    out = bold([])
    assert out.coords.shape == (0, 3)
    assert out.coords.dtype == np.float32
    assert out.offsets.tolist() == [0]
    assert out.offsets.dtype == np.int32


def test_bold_count_le_1_is_noop() -> None:
    base = _geometry(coords=[(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)], offsets=[0, 2])
    out = bold([base], count=1, radius=1.0)
    assert out is base


def test_bold_radius_le_0_is_noop() -> None:
    base = _geometry(coords=[(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)], offsets=[0, 2])
    out = bold([base], count=3, radius=0.0)
    assert out is base


def test_bold_repeats_geometry_and_preserves_offsets_structure() -> None:
    base = _geometry(
        coords=[
            (0.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
            (10.0, 0.0, 0.0),
            (10.0, 1.0, 0.0),
            (10.0, 2.0, 0.0),
        ],
        offsets=[0, 2, 5],
    )

    out = bold([base], count=3, radius=1.0, seed=123)

    assert out.coords.dtype == np.float32
    assert out.coords.flags.writeable is False
    assert out.offsets.dtype == np.int32
    assert out.offsets.flags.writeable is False

    assert out.coords.shape == (15, 3)
    assert out.offsets.tolist() == [0, 2, 5, 7, 10, 12, 15]

    n = int(base.coords.shape[0])
    assert np.allclose(out.coords[:n], base.coords, atol=1e-6)

    for k in range(3):
        s = k * n
        e = s + n
        delta = out.coords[s:e].astype(np.float64, copy=False) - base.coords.astype(
            np.float64, copy=False
        )
        assert np.allclose(delta[:, 2], 0.0, atol=1e-6)
        assert np.allclose(delta[:, 0], delta[0, 0], atol=1e-6)
        assert np.allclose(delta[:, 1], delta[0, 1], atol=1e-6)


def test_bold_is_deterministic_for_same_seed() -> None:
    base = _geometry(
        coords=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)],
        offsets=[0, 3],
    )

    out1 = bold([base], count=5, radius=1.0, seed=999)
    out2 = bold([base], count=5, radius=1.0, seed=999)

    assert out1.offsets.tolist() == out2.offsets.tolist()
    assert np.allclose(out1.coords, out2.coords, atol=0.0)
