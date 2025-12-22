from __future__ import annotations

import pytest

from grafix.core.parameters import ParamStore
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.style_ops import ensure_style_entries
from grafix.core.parameters.style import (
    STYLE_BACKGROUND_COLOR,
    STYLE_GLOBAL_LINE_COLOR,
    STYLE_GLOBAL_THICKNESS,
    coerce_rgb255,
    rgb01_to_rgb255,
    rgb255_to_rgb01,
    style_key,
)


def test_rgb01_to_rgb255_converts_and_clamps():
    assert rgb01_to_rgb255((0.0, 0.5, 1.0)) == (0, 128, 255)
    assert rgb01_to_rgb255((-1.0, 2.0, 0.25)) == (0, 255, 64)


def test_rgb255_to_rgb01_converts():
    r, g, b = rgb255_to_rgb01((0, 128, 255))
    assert r == 0.0
    assert g == 128.0 / 255.0
    assert b == 1.0


def test_coerce_rgb255_returns_rgb255_tuple():
    assert coerce_rgb255((0, 128, 255)) == (0, 128, 255)


def test_coerce_rgb255_converts_and_clamps():
    assert coerce_rgb255((-1, 256, 0.2)) == (0, 255, 0)


def test_coerce_rgb255_rejects_non_length3():
    with pytest.raises(ValueError):
        coerce_rgb255((1, 2))


def test_ensure_style_entries_creates_state_and_meta():
    store = ParamStore()
    ensure_style_entries(
        store,
        background_color_rgb01=(1.0, 0.0, 0.0),
        global_thickness=0.01,
        global_line_color_rgb01=(0.0, 1.0, 0.0),
    )

    for arg, expected_kind in (
        (STYLE_BACKGROUND_COLOR, "rgb"),
        (STYLE_GLOBAL_THICKNESS, "float"),
        (STYLE_GLOBAL_LINE_COLOR, "rgb"),
    ):
        key = style_key(arg)
        state = store.get_state(key)
        meta = store.get_meta(key)
        assert state is not None
        assert meta is not None
        assert meta.kind == expected_kind
        assert state.override is True

    bg_state = store.get_state(style_key(STYLE_BACKGROUND_COLOR))
    assert bg_state is not None
    assert bg_state.ui_value == (255, 0, 0)
    assert_invariants(store)
