from grafix.interactive.parameter_gui.store_bridge import _order_rows_for_display
from grafix.core.parameters.view import ParameterRow

# 登録（param_order 取得）に必要なので、対象モジュールを明示的に import する。
from grafix.core.effects import scale as _effect_scale  # noqa: F401
from grafix.core.primitives import polygon as _primitive_polygon  # noqa: F401


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


def test_order_rows_for_display_primitive_uses_signature_arg_order():
    rows = [
        _row(op="polygon", site_id="p:1", ordinal=1, arg="scale"),
        _row(op="polygon", site_id="p:1", ordinal=1, arg="center"),
        _row(op="polygon", site_id="p:1", ordinal=1, arg="phase"),
        _row(op="polygon", site_id="p:1", ordinal=1, arg="n_sides"),
    ]
    out = _order_rows_for_display(
        rows,
        step_info_by_site={},
        display_order_by_group={("polygon", "p:1"): 1},
    )
    assert [r.arg for r in out] == ["n_sides", "phase", "center", "scale"]


def test_order_rows_for_display_effect_step_uses_bypass_then_signature_arg_order():
    rows = [
        _row(op="scale", site_id="e:1", ordinal=1, arg="scale"),
        _row(op="scale", site_id="e:1", ordinal=1, arg="pivot"),
        _row(op="scale", site_id="e:1", ordinal=1, arg="auto_center"),
        _row(op="scale", site_id="e:1", ordinal=1, arg="mode"),
        _row(op="scale", site_id="e:1", ordinal=1, arg="bypass"),
    ]
    out = _order_rows_for_display(
        rows,
        step_info_by_site={("scale", "e:1"): ("chain:1", 0)},
        display_order_by_group={("scale", "e:1"): 1},
    )
    assert [r.arg for r in out] == ["bypass", "mode", "auto_center", "pivot", "scale"]


def test_order_rows_for_display_places_unknown_arg_last_for_primitive():
    rows = [
        _row(op="polygon", site_id="p:1", ordinal=1, arg="n_sides"),
        _row(op="polygon", site_id="p:1", ordinal=1, arg="__unknown__"),
    ]
    out = _order_rows_for_display(
        rows,
        step_info_by_site={},
        display_order_by_group={("polygon", "p:1"): 1},
    )
    assert [r.arg for r in out] == ["n_sides", "__unknown__"]


def test_order_rows_for_display_places_unknown_arg_last_for_effect():
    rows = [
        _row(op="scale", site_id="e:1", ordinal=1, arg="bypass"),
        _row(op="scale", site_id="e:1", ordinal=1, arg="__unknown__"),
    ]
    out = _order_rows_for_display(
        rows,
        step_info_by_site={("scale", "e:1"): ("chain:1", 0)},
        display_order_by_group={("scale", "e:1"): 1},
    )
    assert [r.arg for r in out] == ["bypass", "__unknown__"]
