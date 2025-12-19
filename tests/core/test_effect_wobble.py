"""core.effects.wobble をテスト。"""

from __future__ import annotations

import numpy as np

from grafix.core.effects.wobble import wobble
from grafix.core.realized_geometry import RealizedGeometry


def _line(coords: list[tuple[float, float, float]]) -> RealizedGeometry:
    c = np.asarray(coords, dtype=np.float32)
    o = np.asarray([0, c.shape[0]], dtype=np.int32)
    return RealizedGeometry(coords=c, offsets=o)


def test_wobble_empty_inputs_returns_empty_geometry() -> None:
    out = wobble([])
    assert out.coords.shape == (0, 3)
    assert out.coords.dtype == np.float32
    assert out.offsets.tolist() == [0]
    assert out.offsets.dtype == np.int32


def test_wobble_amplitude_zero_is_noop() -> None:
    base = _line([(0.0, 1.0, 2.0), (10.0, 20.0, 30.0)])
    out = wobble([base], amplitude=0.0)
    assert out is base


def test_wobble_matches_componentwise_formula() -> None:
    base = _line([(0.0, 0.0, 0.0), (10.0, 5.0, -2.0), (20.0, -3.0, 4.0)])
    out = wobble([base], amplitude=2.0, frequency=(0.05, 0.1, 0.2), phase=30.0)

    v = base.coords.astype(np.float64, copy=False)
    phase_rad = float(np.deg2rad(30.0))
    expected = v.copy()
    expected[:, 0] = v[:, 0] + 2.0 * np.sin(2.0 * np.pi * 0.05 * v[:, 0] + phase_rad)
    expected[:, 1] = v[:, 1] + 2.0 * np.sin(2.0 * np.pi * 0.1 * v[:, 1] + phase_rad)
    expected[:, 2] = v[:, 2] + 2.0 * np.sin(2.0 * np.pi * 0.2 * v[:, 2] + phase_rad)

    assert out.coords.dtype == np.float32
    assert out.coords.flags.writeable is False
    assert out.offsets is base.offsets
    assert np.allclose(out.coords, expected.astype(np.float32, copy=False), atol=1e-6)

