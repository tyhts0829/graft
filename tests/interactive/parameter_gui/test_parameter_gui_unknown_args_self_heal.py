from grafix.core.parameters import ParamMeta, ParamStore, ParameterKey
from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.interactive.parameter_gui import store_bridge

# 登録（meta 取得）に必要なので、対象モジュールを明示的に import する。
from grafix.core.primitives import line as _primitive_line  # noqa: F401


def test_render_store_parameter_table_filters_unknown_arg(monkeypatch) -> None:
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
                explicit=False,
            ),
            FrameParamRecord(
                key=unknown,
                base=0.1,
                meta=ParamMeta(kind="float", ui_min=0.0, ui_max=1.0),
                explicit=False,
            ),
        ],
    )

    captured_rows: list[object] = []

    def _fake_render_parameter_table(rows, **_kwargs):
        captured_rows[:] = list(rows)
        return False, list(rows)

    monkeypatch.setattr(store_bridge, "render_parameter_table", _fake_render_parameter_table)

    store_bridge.render_store_parameter_table(store)

    args = [r.arg for r in captured_rows if getattr(r, "op", None) == "line"]
    assert args == ["length"]

