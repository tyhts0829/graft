"""clip effect の実体変換に関するテスト群。"""

from __future__ import annotations

import numpy as np
import pytest

from grafix.api import E, G
from grafix.core.realize import realize

try:
    import pyclipper  # type: ignore[import-not-found]  # noqa: F401
except Exception:  # pragma: no cover
    pyclipper = None


def test_clip_requires_two_inputs() -> None:
    a = G.grid(nx=3, ny=3, scale=10.0)
    with pytest.raises(TypeError):
        E.clip()(a)


def test_unary_effect_rejects_multiple_inputs() -> None:
    a = G.grid(nx=3, ny=3, scale=10.0)
    b = G.polygon(n_sides=4, scale=5.0)
    with pytest.raises(TypeError):
        E.scale(scale=(2.0, 2.0, 2.0))(a, b)


def test_clip_noop_when_mask_has_no_valid_rings() -> None:
    a = G.grid(nx=7, ny=7, scale=10.0)
    b = G.line(length=100.0)

    out = E.clip(mode="inside")(a, b)
    realized_out = realize(out)
    realized_a = realize(a)

    np.testing.assert_allclose(realized_out.coords, realized_a.coords, rtol=0.0, atol=1e-6)
    assert realized_out.offsets.tolist() == realized_a.offsets.tolist()


@pytest.mark.skipif(pyclipper is None, reason="pyclipper が未インストール")  # type: ignore[arg-type]
def test_clip_inside_reduces_bounds_on_xy_plane() -> None:
    a = G.grid(nx=11, ny=11, scale=10.0)
    b = G.polygon(n_sides=6, scale=3.0)

    out = E.clip(mode="inside")(a, b)
    realized_out = realize(out)

    assert realized_out.coords.shape[0] > 0
    assert realized_out.offsets.size >= 2

    mask = realize(b)
    out_xy = realized_out.coords[:, 0:2]
    mask_xy = mask.coords[:, 0:2]

    out_min = np.min(out_xy, axis=0)
    out_max = np.max(out_xy, axis=0)
    mask_min = np.min(mask_xy, axis=0)
    mask_max = np.max(mask_xy, axis=0)

    eps = 1e-3
    assert float(out_min[0]) >= float(mask_min[0]) - eps
    assert float(out_min[1]) >= float(mask_min[1]) - eps
    assert float(out_max[0]) <= float(mask_max[0]) + eps
    assert float(out_max[1]) <= float(mask_max[1]) + eps


@pytest.mark.skipif(pyclipper is None, reason="pyclipper が未インストール")  # type: ignore[arg-type]
def test_clip_outside_empty_when_mask_covers_all() -> None:
    a = G.grid(nx=11, ny=11, scale=1.0)
    b = G.polygon(n_sides=6, scale=100.0)

    out = E.clip(mode="outside")(a, b)
    realized_out = realize(out)

    assert realized_out.coords.shape == (0, 3)
    assert realized_out.offsets.tolist() == [0]


@pytest.mark.skipif(pyclipper is None, reason="pyclipper が未インストール")  # type: ignore[arg-type]
def test_clip_restores_pose_from_rotated_plane() -> None:
    from grafix.core.effects.util import transform_to_xy_plane

    a0 = G.grid(nx=11, ny=11, scale=10.0)
    b0 = G.polygon(n_sides=6, scale=3.0)

    a = E.rotate(auto_center=False, pivot=(0.0, 0.0, 0.0), rotation=(45.0, 0.0, 0.0))(a0)
    a = E.translate(delta=(0.0, 0.0, 10.0))(a)
    b = E.rotate(auto_center=False, pivot=(0.0, 0.0, 0.0), rotation=(45.0, 0.0, 0.0))(b0)
    b = E.translate(delta=(0.0, 0.0, 10.0))(b)

    out = E.clip(mode="inside")(a, b)
    realized_out = realize(out)
    assert realized_out.coords.shape[0] > 0

    z_range = float(np.max(realized_out.coords[:, 2]) - np.min(realized_out.coords[:, 2]))
    assert z_range > 0.5

    mask = realize(b)
    _aligned_mask, rotation_matrix, z_offset = transform_to_xy_plane(mask.coords)
    aligned_out = realized_out.coords.astype(np.float64, copy=False) @ rotation_matrix.T
    aligned_out[:, 2] -= float(z_offset)
    assert float(np.max(np.abs(aligned_out[:, 2]))) < 1e-3


@pytest.mark.skipif(pyclipper is None, reason="pyclipper が未インストール")  # type: ignore[arg-type]
def test_clip_draw_outline_appends_mask_ring() -> None:
    a = G.grid(nx=11, ny=11, scale=10.0)
    b = G.polygon(n_sides=6, scale=3.0)

    base = realize(E.clip(mode="inside")(a, b))
    out = realize(E.clip(mode="inside", draw_outline=True)(a, b))

    assert out.offsets.size >= base.offsets.size + 1


@pytest.mark.skipif(pyclipper is None, reason="pyclipper が未インストール")  # type: ignore[arg-type]
def test_clip_draw_outline_returns_outline_when_result_empty() -> None:
    a = G.grid(nx=11, ny=11, scale=1.0)
    b = G.polygon(n_sides=6, scale=100.0)

    out = realize(E.clip(mode="outside", draw_outline=True)(a, b))
    assert out.coords.shape[0] > 0
    assert out.offsets.size >= 2

    s0 = int(out.offsets[0])
    e0 = int(out.offsets[1])
    ring = out.coords[s0:e0]
    assert ring.shape[0] >= 4
    np.testing.assert_allclose(ring[0], ring[-1], rtol=0.0, atol=1e-6)
