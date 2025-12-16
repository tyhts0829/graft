import pytest

from graft.core.parameters import ParameterKey, ParamMeta, ParamState, rows_from_snapshot


def test_rows_sorted_by_op_and_ordinal():
    snap = {
        ParameterKey("b", "s1", "x"): (
            ParamMeta(kind="float", ui_min=0, ui_max=1),
            ParamState(ui_value=0.1, override=False, cc_key=None),
            2,
            None,
        ),
        ParameterKey("a", "s0", "y"): (
            ParamMeta(kind="int", ui_min=0, ui_max=10),
            ParamState(ui_value=5, override=True, cc_key=1),
            1,
            None,
        ),
        ParameterKey("a", "s0", "x"): (
            ParamMeta(kind="float", ui_min=-1, ui_max=1),
            ParamState(ui_value=0.0, override=False, cc_key=None),
            2,
            None,
        ),
    }

    rows = rows_from_snapshot(snap)

    assert [r.op for r in rows] == ["a", "a", "b"]
    assert [r.ordinal for r in rows] == [1, 2, 2]
    assert [r.arg for r in rows] == ["y", "x", "x"]
    # label includes ordinal and arg
    assert rows[0].label.startswith("1:")


def test_rows_skip_missing_meta():
    # meta None は snapshot 側で除外される仕様だが念のため空 dict のときは空リスト
    rows = rows_from_snapshot({})
    assert rows == []
