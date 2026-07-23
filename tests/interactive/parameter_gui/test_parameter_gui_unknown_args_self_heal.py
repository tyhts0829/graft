from grafix.api import preset
from grafix.core.geometry import Geometry
from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.merge_ops import merge_frame_params
import grafix.interactive.parameter_gui.table_commit as table_commit_module
from grafix.interactive.parameter_gui.session_state import WidgetSessionState
from grafix.interactive.parameter_gui.table import TableEdits
from grafix.interactive.parameter_gui.table_commit import render_store_parameter_table
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)

# 登録（meta 取得）に必要なので、対象モジュールを明示的に import する。
from grafix.core.primitives import line as _primitive_line  # noqa: F401


@preset(meta={"center": ParamMeta(kind="vec3")})
def _logo_component(*, center=(0.0, 0.0, 0.0)) -> Geometry:
    return Geometry.create(op="concat")


def test_render_store_parameter_table_filters_unknown_arg(
    monkeypatch,
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store = ParamStore()
    known = ParameterKey(op="line", site_id="p:1", arg="length")
    unknown = ParameterKey(op="line", site_id="p:1", arg="__unknown__")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=known,
                base=1.0,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=2.0),
                effective=1.0,
                source="code",
                explicit=False,
            ),
            FrameParamRecord(
                key=unknown,
                base=0.1,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.1,
                source="code",
                explicit=False,
            ),
        ],
    )

    captured_rows: list[object] = []

    def _fake_render_parameter_table(render_input, **_kwargs):
        rows = [
            render_input.model_rows[item.row_index]
            for block in render_input.group_layout
            for item in block.items
        ]
        captured_rows[:] = list(rows)
        return TableEdits(
            rows=tuple(rows),
            collapsed_headers=render_input.collapsed_headers,
            midi_learn_state=render_input.midi_learn_state,
        )

    monkeypatch.setattr(
        table_commit_module,
        "render_parameter_table",
        _fake_render_parameter_table,
    )

    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    render_store_parameter_table(
        store,
        table_view=view,
        widget_state=WidgetSessionState(),
    )

    args = [r.arg for r in captured_rows if getattr(r, "op", None) == "line"]
    assert args == ["length"]


def test_render_store_parameter_table_filters_unknown_arg_for_component(
    monkeypatch,
    parameter_table_cache: ParameterTableViewCache,
) -> None:
    store = ParamStore()
    known = ParameterKey(op="preset._logo_component", site_id="c:1", arg="center")
    unknown = ParameterKey(op="preset._logo_component", site_id="c:1", arg="__unknown__")
    merge_frame_params(
        store,
        [
            FrameParamRecord(
                key=known,
                base=(1.0, 2.0, 3.0),
                meta=ParamMeta(kind="vec3"),
                effective=(1.0, 2.0, 3.0),
                source="code",
                explicit=False,
            ),
            FrameParamRecord(
                key=unknown,
                base=0.1,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                effective=0.1,
                source="code",
                explicit=False,
            ),
        ],
    )

    captured_rows: list[object] = []

    def _fake_render_parameter_table(render_input, **_kwargs):
        rows = [
            render_input.model_rows[item.row_index]
            for block in render_input.group_layout
            for item in block.items
        ]
        captured_rows[:] = list(rows)
        return TableEdits(
            rows=tuple(rows),
            collapsed_headers=render_input.collapsed_headers,
            midi_learn_state=render_input.midi_learn_state,
        )

    monkeypatch.setattr(
        table_commit_module,
        "render_parameter_table",
        _fake_render_parameter_table,
    )

    view = parameter_table_view_for_store(
        store,
        cache=parameter_table_cache,
        show_inactive_params=True,
    )
    render_store_parameter_table(
        store,
        table_view=view,
        widget_state=WidgetSessionState(),
    )

    args = [r.arg for r in captured_rows if getattr(r, "op", None) == "preset._logo_component"]
    assert args == ["center"]
