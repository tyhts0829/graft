from grafix.api import preset
from grafix.interactive.parameter_gui.grouping import group_info_for_row
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.style import STYLE_OP
from grafix.core.parameters.view import ParameterRow


@preset(meta={"center": ParamMeta(kind="vec3")})
def logo(*, center=(0.0, 0.0, 0.0), name=None, key=None):
    return None


def _row(*, op: str, site_id: str, ordinal: int, arg: str) -> ParameterRow:
    return ParameterRow(
        label="",
        op=op,
        site_id=site_id,
        arg=arg,
        kind="float",
        ui_value=0.0,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=int(ordinal),
    )


def test_group_info_for_row_style_global():
    row = _row(op=STYLE_OP, site_id="__global__", ordinal=1, arg="background_color")
    info = group_info_for_row(row)
    assert info.group_id == ("style", "global")
    assert info.header_id == "style"
    assert info.header == "Style"
    assert info.visible_label == "background_color"


def test_group_info_for_row_style_layer():
    row = _row(op=LAYER_STYLE_OP, site_id="layer:2", ordinal=2, arg="line_color")
    info = group_info_for_row(
        row,
        layer_style_name_by_site_id={"layer:2": "bg"},
    )
    assert info.group_id == ("style", "global")
    assert info.header == "Style"
    assert info.visible_label == "bg#2 line_color"


def test_group_info_for_row_primitive():
    row = _row(op="polygon", site_id="p:1", ordinal=1, arg="n_sides")
    info = group_info_for_row(
        row,
        primitive_header_by_group={("polygon", 1): "P"},
    )
    assert info.group_id == ("primitive", ("polygon", 1))
    assert info.header_id == "primitive:polygon#1"
    assert info.header == "P"
    assert info.visible_label == "polygon#1 n_sides"


def test_group_info_for_row_effect_chain_uses_step_ordinal():
    row = _row(op="scale", site_id="e:1", ordinal=99, arg="auto_center")
    info = group_info_for_row(
        row,
        step_info_by_site={("scale", "e:1"): ("chain:1", 0)},
        effect_chain_header_by_id={"chain:1": "xf"},
        effect_step_ordinal_by_site={("scale", "e:1"): 1},
    )
    assert info.group_id == ("effect_chain", "chain:1")
    assert info.header_id == "effect_chain:chain:1"
    assert info.header == "xf"
    assert info.visible_label == "scale#1 auto_center"


def test_group_info_for_row_preset_shows_header_and_uses_display_op():
    row = _row(op="preset.logo", site_id="comp:1", ordinal=1, arg="center")
    info = group_info_for_row(
        row,
        primitive_header_by_group={("preset.logo", 1): "Logo"},
    )
    assert info.group_id == ("preset", ("preset.logo", 1))
    assert info.header_id == "preset:preset.logo#1"
    assert info.header == "Logo"
    assert info.visible_label == "logo#1 center"
