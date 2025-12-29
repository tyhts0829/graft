from grafix.api import component
from grafix.core.parameters import ParameterKey, ParameterRow
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.style import STYLE_OP
from grafix.interactive.parameter_gui.group_blocks import GroupBlock, GroupBlockItem
from grafix.interactive.parameter_gui.snippet import snippet_for_block


def _row(
    *,
    op: str,
    site_id: str,
    ordinal: int,
    arg: str,
    kind: str = "float",
    ui_value: object,
) -> ParameterRow:
    return ParameterRow(
        label="",
        op=op,
        site_id=site_id,
        arg=arg,
        kind=kind,
        ui_value=ui_value,
        ui_min=None,
        ui_max=None,
        choices=None,
        cc_key=None,
        override=True,
        ordinal=int(ordinal),
    )


def test_snippet_style_converts_rgb255_to_rgb01_and_maps_keys() -> None:
    style_rows = [
        _row(
            op=STYLE_OP,
            site_id="__global__",
            ordinal=1,
            arg="background_color",
            kind="rgb",
            ui_value=(255, 0, 0),
        ),
        _row(
            op=STYLE_OP,
            site_id="__global__",
            ordinal=1,
            arg="global_thickness",
            kind="float",
            ui_value=0.001,
        ),
        _row(
            op=STYLE_OP,
            site_id="__global__",
            ordinal=1,
            arg="global_line_color",
            kind="rgb",
            ui_value=(0, 0, 0),
        ),
        _row(
            op=LAYER_STYLE_OP,
            site_id="layer:1",
            ordinal=1,
            arg="line_color",
            kind="rgb",
            ui_value=(0, 128, 255),
        ),
        _row(
            op=LAYER_STYLE_OP,
            site_id="layer:1",
            ordinal=1,
            arg="line_thickness",
            kind="float",
            ui_value=0.002,
        ),
    ]

    block = GroupBlock(
        group_id=("style", "global"),
        header_id="style",
        header="Style",
        items=[GroupBlockItem(row=r, visible_label="") for r in style_rows],
    )

    out = snippet_for_block(
        block,
        layer_style_name_by_site_id={"layer:1": "outline"},
        last_effective_by_key={},
        step_info_by_site={},
    )

    assert "background_color=(1.0, 0.0, 0.0)" in out
    assert "line_thickness=0.001" in out
    assert "line_color=(0.0, 0.0, 0.0)" in out
    assert "# Layer style: outline#1" in out
    assert "thickness=0.002" in out


def test_snippet_effect_chain_orders_steps_by_step_index() -> None:
    rows = [
        _row(op="scale", site_id="e:1", ordinal=1, arg="scale", ui_value=(2.0, 2.0, 2.0)),
        _row(op="rotate", site_id="e:2", ordinal=1, arg="rotation", ui_value=(0.0, 0.0, 45.0)),
    ]
    block = GroupBlock(
        group_id=("effect_chain", "chain:1"),
        header_id="effect_chain:chain:1",
        header="xf",
        items=[GroupBlockItem(row=r, visible_label="") for r in rows],
    )

    last = {
        ParameterKey("scale", "e:1", "scale"): (2.0, 2.0, 2.0),
        ParameterKey("rotate", "e:2", "rotation"): (0.0, 0.0, 45.0),
    }
    step_info = {
        ("rotate", "e:2"): ("chain:1", 0),
        ("scale", "e:1"): ("chain:1", 1),
    }
    out = snippet_for_block(
        block,
        last_effective_by_key=last,
        step_info_by_site=step_info,
    )

    assert out.index("E.rotate") < out.index(".scale")


def test_snippet_component_uses_display_op_call_name() -> None:
    @component(meta={"x": {"kind": "float"}})
    def logo(*, x: float = 1.0, name=None, key=None):
        _ = (x, name, key)
        return None

    row = _row(op="component.logo", site_id="c:1", ordinal=1, arg="x", ui_value=1.0)
    block = GroupBlock(
        group_id=("component", ("component.logo", 1)),
        header_id="component:component.logo#1",
        header="Logo",
        items=[GroupBlockItem(row=row, visible_label="")],
    )
    out = snippet_for_block(
        block,
        last_effective_by_key={ParameterKey("component.logo", "c:1", "x"): 2.0},
    )

    assert "logo(" in out
    assert "x=2.0" in out

