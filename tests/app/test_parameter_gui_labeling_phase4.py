from src.app.parameter_gui.labeling import format_layer_style_row_label
from src.app.parameter_gui.store_bridge import _order_rows_for_display
from src.parameters.layer_style import LAYER_STYLE_OP
from src.parameters.style import STYLE_OP
from src.parameters.view import ParameterRow


def test_format_layer_style_row_label():
    assert (
        format_layer_style_row_label("bg", 1, "line_color") == "bg#1 line_color"
    )


def test_order_rows_for_display_places_style_layer_rows_under_style():
    def row(op: str, ordinal: int, arg: str, *, site_id: str = "s") -> ParameterRow:
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

    rows = [
        row(STYLE_OP, 1, "global_line_color"),
        row(LAYER_STYLE_OP, 2, "line_color", site_id="layer:2"),
        row(STYLE_OP, 1, "background_color"),
        row(LAYER_STYLE_OP, 2, "line_thickness", site_id="layer:2"),
        row(STYLE_OP, 1, "global_thickness"),
        row("polygon", 1, "n_sides", site_id="p:1"),
    ]

    out = _order_rows_for_display(rows, step_info_by_site={}, chain_ordinal_by_id={})
    assert [r.op for r in out[:5]] == [STYLE_OP, STYLE_OP, STYLE_OP, LAYER_STYLE_OP, LAYER_STYLE_OP]
    assert [r.arg for r in out[:5]] == [
        "background_color",
        "global_thickness",
        "global_line_color",
        "line_thickness",
        "line_color",
    ]
