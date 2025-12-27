from grafix.interactive.parameter_gui.group_blocks import GroupBlock, GroupBlockItem
from grafix.interactive.parameter_gui.table import _collapse_key_for_block
from grafix.core.parameters.style import STYLE_OP
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


def test_collapse_key_for_block_style():
    block = GroupBlock(
        group_id=("style", "global"),
        header_id="style",
        header="Style",
        items=[
            GroupBlockItem(
                row=_row(op=STYLE_OP, site_id="__global__", ordinal=1, arg="background_color"),
                visible_label="background_color",
            )
        ],
    )
    assert _collapse_key_for_block(block) == "style:global"


def test_collapse_key_for_block_primitive_uses_site_id():
    block = GroupBlock(
        group_id=("primitive", ("circle", 1)),
        header_id="primitive:circle#1",
        header="circle#1",
        items=[
            GroupBlockItem(
                row=_row(op="circle", site_id="c:1", ordinal=1, arg="r"),
                visible_label="circle#1 r",
            )
        ],
    )
    assert _collapse_key_for_block(block) == "primitive:circle:c:1"


def test_collapse_key_for_block_effect_chain_uses_chain_id():
    block = GroupBlock(
        group_id=("effect_chain", "chain:1"),
        header_id="effect_chain:chain:1",
        header="effect#1",
        items=[
            GroupBlockItem(
                row=_row(op="scale", site_id="e:1", ordinal=99, arg="auto_center"),
                visible_label="scale#1 auto_center",
            )
        ],
    )
    assert _collapse_key_for_block(block) == "effect_chain:chain:1"

