from src.app.parameter_gui.rules import ui_rules_for_row
from src.parameters.layer_style import LAYER_STYLE_OP
from src.parameters.style import STYLE_OP
from src.parameters.view import ParameterRow


def _row(*, op: str, arg: str, kind: str) -> ParameterRow:
    return ParameterRow(
        label="",
        op=op,
        site_id="s",
        arg=arg,
        kind=kind,
        ui_value=None,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=1,
    )


def test_ui_rules_for_row_defaults_by_kind():
    assert ui_rules_for_row(_row(op="circle", arg="r", kind="float")).minmax == "float_range"
    assert ui_rules_for_row(_row(op="circle", arg="n", kind="int")).minmax == "int_range"
    assert ui_rules_for_row(_row(op="circle", arg="p", kind="vec3")).minmax == "float_range"
    assert ui_rules_for_row(_row(op="circle", arg="c", kind="rgb")).minmax == "none"

    assert ui_rules_for_row(_row(op="circle", arg="r", kind="float")).cc_key == "int"
    assert ui_rules_for_row(_row(op="circle", arg="n", kind="int")).cc_key == "int"
    assert ui_rules_for_row(_row(op="circle", arg="p", kind="vec3")).cc_key == "int3"
    assert ui_rules_for_row(_row(op="circle", arg="c", kind="rgb")).cc_key == "int3"

    assert ui_rules_for_row(_row(op="circle", arg="b", kind="bool")).cc_key == "none"
    assert ui_rules_for_row(_row(op="circle", arg="b", kind="bool")).show_override is False
    assert ui_rules_for_row(_row(op="circle", arg="s", kind="string")).cc_key == "none"
    assert ui_rules_for_row(_row(op="circle", arg="s", kind="string")).show_override is False
    assert ui_rules_for_row(_row(op="circle", arg="c", kind="choice")).cc_key == "none"
    assert ui_rules_for_row(_row(op="circle", arg="c", kind="choice")).show_override is False


def test_ui_rules_for_row_minmax_exceptions_by_key():
    style_thickness = _row(op=STYLE_OP, arg="global_thickness", kind="float")
    assert ui_rules_for_row(style_thickness).minmax == "none"
    assert ui_rules_for_row(style_thickness).cc_key == "int"
    assert ui_rules_for_row(style_thickness).show_override is True

    layer_thickness = _row(op=LAYER_STYLE_OP, arg="line_thickness", kind="float")
    assert ui_rules_for_row(layer_thickness).minmax == "none"
    assert ui_rules_for_row(layer_thickness).cc_key == "int"
    assert ui_rules_for_row(layer_thickness).show_override is True

