import pytest

from grafix.api import preset
from grafix.core.geometry import Geometry
from grafix.core.parameters import ParameterKey, ParameterRow
from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.style import STYLE_OP
from grafix.interactive.parameter_gui.group_blocks import (
    GroupBlockLayout,
    GroupBlockLayoutItem,
)
from grafix.interactive.parameter_gui.grouping import GroupType
from grafix.interactive.parameter_gui.snippet import _py_literal, snippet_for_block


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


def _block(
    rows: list[ParameterRow],
    *,
    group_type: GroupType,
    group_key: object,
    header_id: str,
    header: str,
) -> GroupBlockLayout:
    return GroupBlockLayout(
        group_id=(group_type, group_key),
        header_id=header_id,
        header=header,
        items=tuple(
            GroupBlockLayoutItem(row_index=index, visible_label="")
            for index, _row_value in enumerate(rows)
        ),
    )


def test_py_literal_accepts_only_canonical_literal_values() -> None:
    assert _py_literal({"target": (None, True, 2, 0.5, "x")}) == (
        "{'target': (None, True, 2, 0.5, 'x')}"
    )

    for value in ([1], object()):
        with pytest.raises(TypeError, match="snippet の値"):
            _py_literal(value)

    with pytest.raises(ValueError, match="有限値"):
        _py_literal(float("nan"))


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

    block = _block(
        style_rows,
        group_type=GroupType.STYLE,
        group_key="global",
        header_id="style",
        header="Style",
    )

    out = snippet_for_block(
        block,
        style_rows,
        last_effective_by_key={},
        step_info_by_site={},
        raw_label_by_site={(LAYER_STYLE_OP, "layer:1"): "outline"},
    )

    assert out == (
        "    # --- run(...) ---\n"
        "    background_color=(1.0, 0.0, 0.0),\n"
        "    line_thickness=0.001,\n"
        "    line_color=(0.0, 0.0, 0.0),\n"
        "    \n"
        "    # --- L(name=...).layer(..., color/thickness) ---\n"
        "    # outline: paste into `L(name='outline').layer(...)`\n"
        "    color=(0.0, 0.5019607843137255, 1.0),\n"
        "    thickness=0.002,\n"
    )


def test_snippet_effect_chain_orders_steps_by_step_index() -> None:
    rows = [
        _row(op="scale", site_id="e:1", ordinal=1, arg="scale", ui_value=(2.0, 2.0, 2.0)),
        _row(op="rotate", site_id="e:2", ordinal=1, arg="rotation", ui_value=(0.0, 0.0, 45.0)),
    ]
    block = _block(
        rows,
        group_type=GroupType.EFFECT_CHAIN,
        group_key="chain:1",
        header_id="effect_chain:chain:1",
        header="xf",
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
        rows,
        last_effective_by_key=last,
        step_info_by_site=step_info,
    )

    assert out == (
        "    E.rotate(\n"
        "        rotation=(0.0, 0.0, 45.0),\n"
        "    ).scale(\n"
        "        scale=(2.0, 2.0, 2.0),\n"
        "    )\n"
    )


def test_snippet_component_uses_display_op_call_name() -> None:
    @preset(meta={"x": {"kind": "float"}})
    def snippet_logo(*, x: float = 1.0) -> Geometry:
        _ = x
        return Geometry.create(op="concat")

    row = _row(op="preset.snippet_logo", site_id="c:1", ordinal=1, arg="x", ui_value=1.0)
    block = _block(
        [row],
        group_type=GroupType.PRESET,
        group_key=("preset.snippet_logo", 1),
        header_id="preset:preset.snippet_logo#1",
        header="Logo",
    )
    out = snippet_for_block(
        block,
        [row],
        last_effective_by_key={ParameterKey("preset.snippet_logo", "c:1", "x"): 2.0},
    )

    assert out == "    P.snippet_logo(\n        x=2.0,\n    )\n"


def test_snippet_primitive_includes_name_when_raw_label_exists() -> None:
    rows = [
        _row(op="text", site_id="p:1", ordinal=1, arg="text", kind="str", ui_value="Hello"),
        _row(op="text", site_id="p:1", ordinal=1, arg="scale", kind="float", ui_value=2.0),
    ]
    block = _block(
        rows,
        group_type=GroupType.PRIMITIVE,
        group_key=("text", 1),
        header_id="primitive:text#1",
        header="text",
    )
    out = snippet_for_block(
        block,
        rows,
        raw_label_by_site={("text", "p:1"): "title1"},
    )

    assert out == ("    G(name='title1').text(\n        text='Hello',\n        scale=2.0,\n    )\n")


def test_snippet_primitive_does_not_include_name_without_raw_label() -> None:
    rows = [
        _row(op="text", site_id="p:1", ordinal=1, arg="text", kind="str", ui_value="Hello"),
    ]
    block = _block(
        rows,
        group_type=GroupType.PRIMITIVE,
        group_key=("text", 1),
        header_id="primitive:text#1",
        header="text",
    )
    out = snippet_for_block(block, rows)

    assert out.startswith("    ")
    assert "G.text(" in out
    assert "G(name=" not in out


def test_snippet_can_emit_explicit_key_for_important_or_loop_group() -> None:
    row = _row(
        op="text",
        site_id="loop-site",
        ordinal=1,
        arg="text",
        kind="str",
        ui_value="Hello",
    )
    block = _block(
        [row],
        group_type=GroupType.PRIMITIVE,
        group_key=("text", 1),
        header_id="primitive:text#1",
        header="text",
    )

    out = snippet_for_block(
        block,
        [row],
        explicit_key_by_site={("text", "loop-site"): "title-loop"},
    )

    assert "key='title-loop'" in out


def test_snippet_effect_chain_includes_name_when_raw_label_exists() -> None:
    rows = [
        _row(op="scale", site_id="e:1", ordinal=1, arg="scale", ui_value=(2.0, 2.0, 2.0)),
        _row(op="rotate", site_id="e:2", ordinal=1, arg="rotation", ui_value=(0.0, 0.0, 45.0)),
    ]
    block = _block(
        rows,
        group_type=GroupType.EFFECT_CHAIN,
        group_key="chain:1",
        header_id="effect_chain:chain:1",
        header="xf",
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
        rows,
        last_effective_by_key=last,
        step_info_by_site=step_info,
        raw_label_by_site={("scale", "e:1"): "xf"},
    )

    assert out.startswith("    ")
    assert out.index("E(name='xf').rotate") < out.index(".scale")


def test_snippet_style_layer_dict_includes_name_when_raw_label_exists() -> None:
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

    block = _block(
        style_rows,
        group_type=GroupType.STYLE,
        group_key="global",
        header_id="style",
        header="Style",
    )

    out = snippet_for_block(
        block,
        style_rows,
        raw_label_by_site={(LAYER_STYLE_OP, "layer:1"): "outline"},
    )

    assert out.startswith("    ")
    assert "dict(" not in out
    assert "L(name='outline').layer" in out


def test_snippet_preset_includes_name_only_when_raw_label_differs() -> None:
    @preset(meta={"x": {"kind": "float"}})
    def snippet_badge(*, x: float = 1.0) -> Geometry:
        _ = x
        return Geometry.create(op="concat")

    row = _row(op="preset.snippet_badge", site_id="c:1", ordinal=1, arg="x", ui_value=1.0)
    block = _block(
        [row],
        group_type=GroupType.PRESET,
        group_key=("preset.snippet_badge", 1),
        header_id="preset:preset.snippet_badge#1",
        header="Badge",
    )

    # raw label が display_op と同じなら name= は出さない
    out1 = snippet_for_block(
        block,
        [row],
        raw_label_by_site={("preset.snippet_badge", "c:1"): "snippet_badge"},
    )
    assert out1.startswith("    ")
    assert "P.snippet_badge(" in out1
    assert "P(name=" not in out1

    # raw label が display_op と異なるなら name= を出す
    out2 = snippet_for_block(
        block,
        [row],
        raw_label_by_site={("preset.snippet_badge", "c:1"): "Badge"},
    )
    assert out2.startswith("    ")
    assert "P(name='Badge').snippet_badge(" in out2


def test_snippet_rejects_unknown_group_type() -> None:
    row = _row(
        op="text",
        site_id="p:1",
        ordinal=1,
        arg="text",
        kind="str",
        ui_value="Hello",
    )
    block = GroupBlockLayout(
        group_id=("unknown", "unknown"),  # type: ignore[arg-type]
        header_id="unknown",
        header="Unknown",
        items=(GroupBlockLayoutItem(row_index=0, visible_label="Text"),),
    )

    with pytest.raises(AssertionError):
        snippet_for_block(block, [row])
