from __future__ import annotations

import importlib
from pathlib import Path

from grafix import G, RenderOptions, export, render
from grafix.core.parameters import ParameterLoadState, ParamStore
from grafix.core.parameters.style import style_key
from grafix.core.parameters.style_ops import ensure_style_entries
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.parameter_storage import ParamStoreLoadResult


def test_export_uses_paramstore_background_color(monkeypatch, tmp_path: Path) -> None:
    render_module = importlib.import_module("grafix.api.render")
    from grafix.export import capture as capture_module

    store = ParamStore()
    ensure_style_entries(
        store,
        background_color_rgb01=(1.0, 1.0, 1.0),
        global_thickness=0.001,
        global_line_color_rgb01=(0.0, 0.0, 0.0),
    )

    bg_key = style_key("background_color")
    bg_meta = store.get_meta(bg_key)
    assert bg_meta is not None
    ok, _err = update_state_from_ui(store, bg_key, (0, 0, 0), meta=bg_meta)
    assert ok

    monkeypatch.setattr(
        render_module,
        "default_param_store_path",
        lambda *_a, **_k: tmp_path / "dummy.json",
    )
    monkeypatch.setattr(
        render_module,
        "read_param_store",
        lambda _path: ParamStoreLoadResult(
            store,
            "loaded",
            ParameterLoadState(),
        ),
    )

    captured: dict[str, object] = {}

    def _fake_rasterize(
        _svg_path,
        path,
        *,
        background_color_rgb01,
        **_kwargs,
    ):
        captured["background_color"] = background_color_rgb01
        Path(path).write_bytes(b"png")
        return Path(path)

    monkeypatch.setattr(capture_module, "rasterize_svg_to_png", _fake_rasterize)

    def draw(_t: float):
        return G.line(
            activate=True,
            center=(5.0, 5.0, 0.0),
            anchor="left",
            length=5.0,
            angle=0.0,
        )

    frame = render(
        draw,
        0.0,
        options=RenderOptions(
            canvas_size=(10, 10),
            background_color=(1.0, 1.0, 1.0),
        ),
        parameter_source="saved",
    )
    result = export(frame, tmp_path / "out.png")

    assert frame.style.bg_color_rgb01 == (0.0, 0.0, 0.0)
    assert captured["background_color"] == (0.0, 0.0, 0.0)
    assert result.path.read_bytes() == b"png"
