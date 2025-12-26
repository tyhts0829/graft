from grafix.interactive.parameter_gui.store_bridge import _order_rows_for_display
from grafix.core.parameters.view import ParameterRow


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


def test_order_rows_for_display_interleaves_primitive_and_effect_by_display_order():
    rows = [
        _row(op="polygon", site_id="p:1", ordinal=1, arg="n_sides"),
        _row(op="scale", site_id="e:1", ordinal=99, arg="auto_center"),
        _row(op="circle", site_id="c:1", ordinal=1, arg="r"),
    ]

    out = _order_rows_for_display(
        rows,
        step_info_by_site={("scale", "e:1"): ("chain:1", 0)},
        display_order_by_group={
            ("polygon", "p:1"): 1,
            ("scale", "e:1"): 2,
            ("circle", "c:1"): 3,
        },
    )
    assert [(r.op, r.site_id) for r in out] == [
        ("polygon", "p:1"),
        ("scale", "e:1"),
        ("circle", "c:1"),
    ]


def test_order_rows_for_display_effect_chain_is_placed_by_min_step_display_order():
    rows = [
        _row(op="polygon", site_id="p:1", ordinal=1, arg="n_sides"),
        _row(op="scale", site_id="e:scale", ordinal=99, arg="auto_center"),
        _row(op="rotate", site_id="e:rotate", ordinal=99, arg="deg"),
        _row(op="circle", site_id="c:1", ordinal=1, arg="r"),
    ]

    out = _order_rows_for_display(
        rows,
        step_info_by_site={
            ("scale", "e:scale"): ("chain:1", 0),
            ("rotate", "e:rotate"): ("chain:1", 1),
        },
        display_order_by_group={
            ("polygon", "p:1"): 1,
            ("rotate", "e:rotate"): 2,
            ("circle", "c:1"): 3,
            ("scale", "e:scale"): 5,
        },
    )
    assert [r.op for r in out] == ["polygon", "scale", "rotate", "circle"]

