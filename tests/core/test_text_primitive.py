from __future__ import annotations

import numpy as np

from graft.api import G
from graft.core.realize import realize


def test_text_empty_returns_empty_geometry() -> None:
    g = G.text(text="", font="SFNS.ttf")
    realized = realize(g)
    assert realized.coords.dtype == np.float32
    assert realized.offsets.dtype == np.int32
    assert realized.coords.shape == (0, 3)
    assert realized.offsets.tolist() == [0]


def test_text_align_shifts_x() -> None:
    left = realize(
        G.text(text="A", font="SFNS.ttf", scale=(10.0, 10.0, 1.0), text_align="left")
    )
    center = realize(
        G.text(text="A", font="SFNS.ttf", scale=(10.0, 10.0, 1.0), text_align="center")
    )
    right = realize(
        G.text(text="A", font="SFNS.ttf", scale=(10.0, 10.0, 1.0), text_align="right")
    )

    assert left.coords.shape[0] > 0
    assert center.coords.shape[0] > 0
    assert right.coords.shape[0] > 0

    min_left = float(left.coords[:, 0].min())
    min_center = float(center.coords[:, 0].min())
    min_right = float(right.coords[:, 0].min())

    assert min_center < min_left
    assert min_right < min_center


def test_text_multiline_increases_y_extent() -> None:
    single = realize(G.text(text="A", font="SFNS.ttf", scale=(10.0, 10.0, 1.0)))
    multi = realize(
        G.text(
            text="A\nA",
            font="SFNS.ttf",
            scale=(10.0, 10.0, 1.0),
            line_height=1.2,
        )
    )

    assert single.coords.shape[0] > 0
    assert multi.coords.shape[0] > 0

    max_y_single = float(single.coords[:, 1].max())
    max_y_multi = float(multi.coords[:, 1].max())
    assert max_y_multi > max_y_single + 5.0


def test_text_center_translates_coords() -> None:
    base = realize(
        G.text(
            text="A",
            font="SFNS.ttf",
            scale=(10.0, 10.0, 1.0),
            center=(0.0, 0.0, 0.0),
        )
    )
    shifted = realize(
        G.text(
            text="A",
            font="SFNS.ttf",
            scale=(10.0, 10.0, 1.0),
            center=(12.5, 7.25, 0.0),
        )
    )

    assert shifted.coords.shape == base.coords.shape
    assert np.allclose(shifted.coords[:, 0], base.coords[:, 0] + 12.5, atol=1e-5)
    assert np.allclose(shifted.coords[:, 1], base.coords[:, 1] + 7.25, atol=1e-5)


def test_text_scale_scales_extent() -> None:
    a = realize(G.text(text="A", font="SFNS.ttf", scale=(10.0, 10.0, 1.0)))
    b = realize(G.text(text="A", font="SFNS.ttf", scale=(20.0, 30.0, 1.0)))

    extent_a_x = float(a.coords[:, 0].max() - a.coords[:, 0].min())
    extent_a_y = float(a.coords[:, 1].max() - a.coords[:, 1].min())
    extent_b_x = float(b.coords[:, 0].max() - b.coords[:, 0].min())
    extent_b_y = float(b.coords[:, 1].max() - b.coords[:, 1].min())

    assert extent_b_x > extent_a_x
    assert extent_b_y > extent_a_y
    assert np.isclose(extent_b_x, extent_a_x * 2.0, rtol=1e-3, atol=1e-4)
    assert np.isclose(extent_b_y, extent_a_y * 3.0, rtol=1e-3, atol=1e-4)
