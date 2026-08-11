from __future__ import annotations

import grafix
import grafix.api as api
from grafix import RenderOptions
from grafix.core import canvas_sizes

EXPECTED_SIZES = {
    "A2": (420, 594),
    "A2_LANDSCAPE": (594, 420),
    "A3": (297, 420),
    "A3_LANDSCAPE": (420, 297),
    "A4": (210, 297),
    "A4_LANDSCAPE": (297, 210),
    "A5": (148, 210),
    "A5_LANDSCAPE": (210, 148),
    "A6": (105, 148),
    "A6_LANDSCAPE": (148, 105),
    "SQUARE": (300, 300),
}


def test_canvas_size_constants_have_exact_public_values() -> None:
    assert tuple(canvas_sizes.__all__) == tuple(EXPECTED_SIZES)

    for name, expected in EXPECTED_SIZES.items():
        defined = getattr(canvas_sizes, name)
        public = getattr(grafix, name)

        assert type(defined) is tuple
        assert all(type(dimension) is int for dimension in defined)
        assert defined == expected
        assert public is defined
        assert name in grafix.__all__
        assert name not in api.__all__
        assert not hasattr(api, name)


def test_landscape_constants_reverse_portrait_dimensions() -> None:
    for paper_name in ("A2", "A3", "A4", "A5", "A6"):
        portrait = getattr(canvas_sizes, paper_name)
        landscape = getattr(canvas_sizes, f"{paper_name}_LANDSCAPE")

        assert landscape == (portrait[1], portrait[0])


def test_canvas_size_constants_are_valid_render_options() -> None:
    for name, expected in EXPECTED_SIZES.items():
        options = RenderOptions(canvas_size=getattr(grafix, name))

        assert options.canvas_size == expected
