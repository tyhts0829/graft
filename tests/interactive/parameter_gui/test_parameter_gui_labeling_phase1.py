from src.interactive.parameter_gui.labeling import (
    dedup_display_names_in_order,
    format_param_row_label,
    primitive_header_display_names_from_snapshot,
)
from src.core.parameters import ParameterKey


def test_format_param_row_label():
    assert format_param_row_label("polygon", 1, "n_sides") == "polygon#1 n_sides"


def test_dedup_display_names_in_order_only_when_conflict():
    assert dedup_display_names_in_order([(("polygon", 1), "poly")]) == {
        ("polygon", 1): "poly"
    }
    assert dedup_display_names_in_order(
        [
            (("polygon", 1), "poly"),
            (("polygon", 2), "poly"),
        ]
    ) == {
        ("polygon", 1): "poly#1",
        ("polygon", 2): "poly#2",
    }


def test_primitive_header_display_names_from_snapshot_uses_label_and_dedups():
    snap = {
        ParameterKey("polygon", "s1", "n_sides"): (None, None, 1, "A"),
        ParameterKey("circle", "s2", "r"): (None, None, 1, "A"),
        ParameterKey("scale", "s3", "s"): (None, None, 1, "A"),
    }

    out = primitive_header_display_names_from_snapshot(
        snap,
        is_primitive_op=lambda op: op in {"polygon", "circle"},
    )
    assert out == {
        ("circle", 1): "A#1",
        ("polygon", 1): "A#2",
    }


def test_primitive_header_display_names_from_snapshot_fallbacks_to_op():
    snap = {
        ParameterKey("polygon", "s1", "n_sides"): (None, None, 1, None),
        ParameterKey("polygon", "s2", "n_sides"): (None, None, 2, None),
    }

    out = primitive_header_display_names_from_snapshot(
        snap,
        is_primitive_op=lambda op: op == "polygon",
    )
    assert out == {
        ("polygon", 1): "polygon#1",
        ("polygon", 2): "polygon#2",
    }
