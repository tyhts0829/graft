from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grafix.export import image
from grafix.runtime_config_loader import runtime_config


# `grafix.export.image`（SVG→PNG / resvg）をテストする。


@pytest.fixture(autouse=True)
def _isolate_runtime_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_default_png_output_path_uses_script_stem_and_output_size():
    def draw(t: float) -> None:
        return None

    path = image.default_png_output_path(draw, scale=8.0, canvas_size=(800, 600))
    assert path.parts[:3] == ("data", "output", "png")
    assert path.name == f"{Path(__file__).stem}_6400x4800.png"
    assert path.suffix == ".png"


def test_png_output_size_scales_canvas_by_png_scale():
    scale = float(runtime_config().png_scale)
    expected = (int(300 * scale), int(300 * scale))
    assert image.png_output_size((300, 300), scale=scale) == expected


@pytest.mark.parametrize(
    ("canvas_size", "scale"),
    [
        ((True, 300), 1.0),
        ((300.5, 300), 1.0),
        (("300", 300), 1.0),
        ((300, 300), True),
        ((300, 300), "1.0"),
    ],
)
def test_png_output_size_rejects_implicit_numeric_coercion(
    canvas_size: object,
    scale: object,
) -> None:
    with pytest.raises(TypeError):
        image.png_output_size(
            canvas_size,  # type: ignore[arg-type]
            scale=scale,  # type: ignore[arg-type]
        )


def test_rasterize_svg_to_png_invokes_resvg_with_resized_svg(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
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

    def fake_run(cmd, *, capture_output: bool, text: bool, check: bool, timeout: float):
        assert capture_output is True
        assert text is True
        assert check is False
        assert timeout == 30.0
        assert cmd[0] == "resvg"

        assert "--width" in cmd
        assert cmd[cmd.index("--width") + 1] == "1200"
        assert "--height" in cmd
        assert cmd[cmd.index("--height") + 1] == "1200"
        assert "--background" in cmd
        assert cmd[cmd.index("--background") + 1] == "#FFFFFF"

        temp_svg = Path(cmd[-2])
        assert temp_svg == src_svg
        temp_png = Path(cmd[-1])
        assert temp_png != out_png
        assert temp_png.parent == out_png.parent
        assert temp_png.suffix == ".png"
        temp_png.write_bytes(b"png")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(image.subprocess, "run", fake_run)

    path = image.rasterize_svg_to_png(src_svg, out_png, output_size=(1200, 1200))
    assert path == out_png
    assert out_png.read_bytes() == b"png"


def test_rasterize_svg_to_png_raises_when_resvg_is_missing(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
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


def test_rasterize_svg_to_png_times_out_resvg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src_svg = tmp_path / "in.svg"
    src_svg.write_text("<svg></svg>\n", encoding="utf-8")
    out_png = tmp_path / "out.png"
    out_png.write_bytes(b"original")

    def timeout_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(image.subprocess, "run", timeout_run)

    with pytest.raises(TimeoutError, match="0.25 秒以内"):
        image.rasterize_svg_to_png(
            src_svg,
            out_png,
            output_size=(10, 10),
            timeout_s=0.25,
        )

    assert out_png.read_bytes() == b"original"
    assert list(tmp_path.glob(".out.*.tmp.png")) == []


@pytest.mark.parametrize("timeout_s", [True, "1.0"])
def test_rasterize_rejects_implicit_timeout_coercion(
    tmp_path: Path,
    timeout_s: object,
) -> None:
    src_svg = tmp_path / "source.svg"
    src_svg.write_text("<svg/>", encoding="utf-8")

    with pytest.raises(TypeError, match="timeout_s"):
        image.rasterize_svg_to_png(
            src_svg,
            tmp_path / "out.png",
            output_size=(10, 10),
            timeout_s=timeout_s,  # type: ignore[arg-type]
        )
