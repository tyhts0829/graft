from __future__ import annotations

from grafix.api import E, G
from grafix.core.realize import realize


def _polyline_count(realized) -> int:
    return int(realized.offsets.size) - 1


def test_text_fill_is_stable_under_x_tilt_rotation() -> None:
    base = G.text(text="HELLO", font="SFNS.ttf", scale=100.0)
    boundary = realize(base)
    boundary_count = _polyline_count(boundary)
    assert boundary_count > 0

    for rx in (105.0, 120.0, 135.0):
        out = realize(E.affine(rotation=(rx, 0.0, 0.0)).fill()(base))
        assert _polyline_count(out) > boundary_count
