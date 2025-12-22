import pytest

from grafix.core.parameters import ParamStore
from grafix.core.parameters.invariants import assert_invariants
from grafix.core.parameters.style import style_key
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.runtime.style_resolver import StyleResolver


def test_style_resolver_respects_override_flags():
    store = ParamStore()
    resolver = StyleResolver(
        store,
        base_background_color_rgb01=(1.0, 0.0, 0.0),
        base_global_thickness=0.001,
        base_global_line_color_rgb01=(0.0, 1.0, 0.0),
    )

    style0 = resolver.resolve()
    assert style0.bg_color_rgb01 == (1.0, 0.0, 0.0)
    assert style0.global_line_color_rgb01 == (0.0, 1.0, 0.0)
    assert style0.global_thickness == pytest.approx(0.001)

    bg_key = style_key("background_color")
    bg_meta = store.get_meta(bg_key)
    assert bg_meta is not None
    update_state_from_ui(store, bg_key, (0, 0, 255), meta=bg_meta)

    thickness_key = style_key("global_thickness")
    thickness_meta = store.get_meta(thickness_key)
    assert thickness_meta is not None
    update_state_from_ui(store, thickness_key, 0.002, meta=thickness_meta)

    style1 = resolver.resolve()
    assert style1.bg_color_rgb01 == (0.0, 0.0, 1.0)
    assert style1.global_thickness == pytest.approx(0.002)

    update_state_from_ui(store, bg_key, (0, 0, 255), meta=bg_meta, override=False)
    update_state_from_ui(store, thickness_key, 0.002, meta=thickness_meta, override=False)

    style2 = resolver.resolve()
    assert style2.bg_color_rgb01 == (1.0, 0.0, 0.0)
    assert style2.global_thickness == pytest.approx(0.001)
    assert_invariants(store)
