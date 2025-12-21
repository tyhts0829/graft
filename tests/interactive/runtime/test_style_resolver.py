import pytest

from grafix.core.parameters import ParamStore
from grafix.core.parameters.style import style_key
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

    bg_state = store.get_state(style_key("background_color"))
    assert bg_state is not None
    bg_state.ui_value = (0, 0, 255)

    thickness_state = store.get_state(style_key("global_thickness"))
    assert thickness_state is not None
    thickness_state.ui_value = 0.002

    style1 = resolver.resolve()
    assert style1.bg_color_rgb01 == (0.0, 0.0, 1.0)
    assert style1.global_thickness == pytest.approx(0.002)

    bg_state.override = False
    thickness_state.override = False

    style2 = resolver.resolve()
    assert style2.bg_color_rgb01 == (1.0, 0.0, 0.0)
    assert style2.global_thickness == pytest.approx(0.001)

