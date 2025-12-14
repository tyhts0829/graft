from src.app.parameter_gui.group_blocks import group_blocks_from_rows
from src.parameters.style import STYLE_OP
from src.parameters.view import ParameterRow


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


def test_group_blocks_from_rows_merges_contiguous_same_group():
    rows = [
        _row(op="polygon", site_id="p:1", ordinal=1, arg="n_sides"),
        _row(op="polygon", site_id="p:1", ordinal=1, arg="r"),
    ]
    blocks = group_blocks_from_rows(
        rows,
        primitive_header_by_group={("polygon", 1): "P"},
    )
    assert len(blocks) == 1

    block = blocks[0]
    assert block.group_id == ("primitive", ("polygon", 1))
    assert block.header_id == "primitive:polygon#1"
    assert block.header == "P"
    assert [it.visible_label for it in block.items] == [
        "polygon#1 n_sides",
        "polygon#1 r",
    ]


def test_group_blocks_from_rows_splits_when_group_changes():
    rows = [
        _row(op="polygon", site_id="p:1", ordinal=1, arg="n_sides"),
        _row(op="circle", site_id="c:1", ordinal=1, arg="r"),
    ]
    blocks = group_blocks_from_rows(
        rows,
        primitive_header_by_group={("polygon", 1): "P", ("circle", 1): "C"},
    )
    assert [b.header for b in blocks] == ["P", "C"]
    assert [b.header_id for b in blocks] == ["primitive:polygon#1", "primitive:circle#1"]


def test_group_blocks_from_rows_preserves_effect_visible_label():
    rows = [
        _row(op="scale", site_id="e:1", ordinal=99, arg="auto_center"),
        _row(op="rotate", site_id="e:2", ordinal=99, arg="deg"),
    ]
    blocks = group_blocks_from_rows(
        rows,
        step_info_by_site={("scale", "e:1"): ("chain:1", 0), ("rotate", "e:2"): ("chain:1", 1)},
        effect_chain_header_by_id={"chain:1": "xf"},
        effect_step_ordinal_by_site={("scale", "e:1"): 1, ("rotate", "e:2"): 1},
    )
    assert len(blocks) == 1
    assert blocks[0].group_id == ("effect_chain", "chain:1")
    assert blocks[0].header == "xf"
    assert [it.visible_label for it in blocks[0].items] == [
        "scale#1 auto_center",
        "rotate#1 deg",
    ]


def test_group_blocks_from_rows_style_is_single_block():
    rows = [
        _row(op=STYLE_OP, site_id="__global__", ordinal=1, arg="background_color"),
        _row(op=STYLE_OP, site_id="__global__", ordinal=1, arg="global_thickness"),
    ]
    blocks = group_blocks_from_rows(rows)
    assert len(blocks) == 1
    assert blocks[0].header == "Style"
