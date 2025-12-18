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
    left = realize(G.text(text="A", font="SFNS.ttf", em_size_mm=10.0, text_align="left"))
    center = realize(G.text(text="A", font="SFNS.ttf", em_size_mm=10.0, text_align="center"))
    right = realize(G.text(text="A", font="SFNS.ttf", em_size_mm=10.0, text_align="right"))

    assert left.coords.shape[0] > 0
    assert center.coords.shape[0] > 0
    assert right.coords.shape[0] > 0

    min_left = float(left.coords[:, 0].min())
    min_center = float(center.coords[:, 0].min())
    min_right = float(right.coords[:, 0].min())

    assert min_center < min_left
    assert min_right < min_center


def test_text_multiline_increases_y_extent() -> None:
    single = realize(G.text(text="A", font="SFNS.ttf", em_size_mm=10.0))
    multi = realize(G.text(text="A\nA", font="SFNS.ttf", em_size_mm=10.0, line_height=1.2))

    assert single.coords.shape[0] > 0
    assert multi.coords.shape[0] > 0

    max_y_single = float(single.coords[:, 1].max())
    max_y_multi = float(multi.coords[:, 1].max())
    assert max_y_multi > max_y_single + 5.0

