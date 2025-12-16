from src.core.parameters import ParamStore
from src.core.parameters.layer_style import (
    LAYER_STYLE_LINE_COLOR,
    LAYER_STYLE_LINE_THICKNESS,
    LAYER_STYLE_OP,
    layer_style_key,
    layer_style_records,
)


def test_layer_style_records_can_be_merged_by_param_store():
    store = ParamStore()
    store.store_frame_params(
        layer_style_records(
            layer_site_id="layer:1",
            base_line_thickness=0.01,
            base_line_color_rgb01=(1.0, 0.0, 0.0),
            explicit_line_thickness=False,
            explicit_line_color=True,
        )
    )
    store.set_label(LAYER_STYLE_OP, "layer:1", "bg")

    key_th = layer_style_key("layer:1", LAYER_STYLE_LINE_THICKNESS)
    key_color = layer_style_key("layer:1", LAYER_STYLE_LINE_COLOR)

    state_th = store.get_state(key_th)
    meta_th = store.get_meta(key_th)
    assert state_th is not None
    assert meta_th is not None
    assert meta_th.kind == "float"
    assert meta_th.ui_min == 1e-6
    assert meta_th.ui_max == 0.01
    assert state_th.ui_value == 0.01
    assert state_th.override is True

    state_color = store.get_state(key_color)
    meta_color = store.get_meta(key_color)
    assert state_color is not None
    assert meta_color is not None
    assert meta_color.kind == "rgb"
    assert meta_color.ui_min == 0
    assert meta_color.ui_max == 255
    assert state_color.ui_value == (255, 0, 0)
    assert state_color.override is False

    assert store.get_label(LAYER_STYLE_OP, "layer:1") == "bg"
