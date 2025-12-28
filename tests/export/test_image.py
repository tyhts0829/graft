from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grafix.export import image
from grafix.core.runtime_config import runtime_config, set_config_path


# `grafix.export.image`（SVG→PNG / resvg）をテストする。

@pytest.fixture(autouse=True)
def _reset_runtime_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_config_path(None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    set_config_path(None)
    yield
    set_config_path(None)


def test_default_png_output_path_uses_data_dir_and_script_stem():
    def draw(t: float) -> None:
        return None

    path = image.default_png_output_path(draw)
    assert path.parts[0] == "data"
    assert path.parts[1] == "output"
    assert path.parts[2] == "png"
    assert path.name == f"{Path(__file__).stem}.png"
    assert path.suffix == ".png"


def test_png_output_size_scales_canvas_by_png_scale():
    scale = float(runtime_config().png_scale)
    expected = (int(300 * scale), int(300 * scale))
    assert image.png_output_size((300, 300)) == expected


def test_rasterize_svg_to_png_invokes_resvg_with_resized_svg(tmp_path, monkeypatch: pytest.MonkeyPatch):
    src_svg = tmp_path / "in.svg"
    src_svg.write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" width="300" height="300">',
                '  <path d="M 0 0 L 1 1" fill="none" stroke="#000000" stroke-width="1" stroke-linecap="round" stroke-linejoin="round" />',
                "</svg>",
                "",
            ]
        ),
        encoding="utf-8",
    )

    out_png = tmp_path / "out.png"

    def fake_run(cmd, *, capture_output: bool, text: bool, check: bool):
        assert capture_output is True
        assert text is True
        assert check is False
        assert cmd[0] == "resvg"

        assert "--width" in cmd
        assert cmd[cmd.index("--width") + 1] == "1200"
        assert "--height" in cmd
        assert cmd[cmd.index("--height") + 1] == "1200"
        assert "--background" in cmd
        assert cmd[cmd.index("--background") + 1] == "#FFFFFF"

        temp_svg = Path(cmd[-2])
        assert temp_svg == src_svg
        assert Path(cmd[-1]) == out_png
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(image.subprocess, "run", fake_run)

    path = image.rasterize_svg_to_png(src_svg, out_png, output_size=(1200, 1200))
    assert path == out_png


def test_rasterize_svg_to_png_raises_when_resvg_is_missing(tmp_path, monkeypatch: pytest.MonkeyPatch):
    src_svg = tmp_path / "in.svg"
    src_svg.write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" width="10" height="10">',
                "</svg>",
                "",
            ]
        ),
        encoding="utf-8",
    )

    out_png = tmp_path / "out.png"

    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(image.subprocess, "run", missing)

    with pytest.raises(RuntimeError, match="resvg が見つかりません"):
        image.rasterize_svg_to_png(src_svg, out_png, output_size=(10, 10))
