from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from grafix import (
    G,
    ExportFormat,
    ExportResult,
    Frame,
    RenderOptions,
    RenderSession,
    render,
    save,
)
from grafix.export.capture_publish import capture_manifest_path_for
from grafix.runtime_config_loader import runtime_config
from grafix.export import capture as capture_module
from grafix.export.capture import CaptureService
from grafix.export.gcode import export_gcode


@pytest.fixture
def frame() -> Frame:
    geometry = G.line(
        center=(0.0, 0.0, 0.0),
        anchor="left",
        length=10.0,
        angle=0.0,
    )

    def draw(_t: float):
        return geometry

    with RenderSession(draw) as session:
        return session.render(1.25)


def test_export_infers_suffix_versions_existing_and_writes_manifest(
    frame: Frame,
    tmp_path: Path,
) -> None:
    base = tmp_path / "drawing.svg"
    base.write_bytes(b"existing artwork")

    result = save(frame, base)

    assert result.path == tmp_path / "drawing_001.svg"
    assert result.format is ExportFormat.SVG
    assert result.manifest_path == capture_manifest_path_for(result.path)
    assert base.read_bytes() == b"existing artwork"
    assert result.path.is_file()
    assert result.manifest_path is not None
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["output"]["format"] == "svg"
    assert payload["output"]["artifact_paths"] == [str(result.path)]
    assert payload["grafix"]["version"]
    assert payload["source"]["available"] is True
    assert payload["source"]["hash"]["algorithm"] == "sha256"
    assert "available" in payload["git"]
    assert payload["config"]["effective"]
    assert payload["parameters"]["source"] == "code"
    assert payload["parameters"]["snapshot_hash"]["algorithm"] == "sha256"
    assert payload["frame"] == {
        "t": 1.25,
        "index": 0,
        "quality": "final",
        "origin": "headless",
    }
    assert payload["output"] == {
        "format": "svg",
        "artifact_paths": [str(result.path)],
        "canvas_size": {"width": 800, "height": 800},
        "size": {"width": 800, "height": 800},
    }
    assert list(tmp_path.glob(".drawing.capture-*")) == []


def test_public_export_returns_capture_service_result_without_rewrapping(
    frame: Frame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "drawing.svg"
    expected = ExportResult(
        path=output,
        format=ExportFormat.SVG,
        manifest_path=capture_manifest_path_for(output),
    )

    def fake_export(
        _service: CaptureService,
        _frame: object,
        _path: str | Path,
        **_kwargs: object,
    ) -> ExportResult:
        return expected

    monkeypatch.setattr(CaptureService, "export", fake_export)

    assert save(frame, output) is expected


def test_capture_service_returns_the_public_canonical_result(
    frame: Frame,
    tmp_path: Path,
) -> None:
    result = CaptureService().export(frame, tmp_path / "drawing.svg")

    assert type(result) is ExportResult
    assert capture_module.__all__ == ["CaptureFrame", "CaptureService"]


@pytest.mark.parametrize("capture_t", [True, "1.25"])
def test_capture_service_does_not_coerce_manifest_time(
    frame: Frame,
    tmp_path: Path,
    capture_t: object,
) -> None:
    staged = tmp_path / "staged.svg"
    staged.write_text("svg", encoding="utf-8")
    invalid_frame = SimpleNamespace(
        layers=frame.layers,
        canvas_size=frame.canvas_size,
        background_color_rgb01=frame.background_color_rgb01,
        t=capture_t,
        provenance=frame.provenance,
    )

    with pytest.raises(TypeError, match="t"):
        CaptureService().publish_staged(
            invalid_frame,
            tmp_path / "drawing.svg",
            (staged,),
            format=ExportFormat.SVG,
        )

    assert not (tmp_path / "drawing.svg").exists()


def test_manifest_only_collision_is_allocated_as_next_version(
    frame: Frame,
    tmp_path: Path,
) -> None:
    base = tmp_path / "drawing.svg"
    old_manifest = capture_manifest_path_for(base)
    old_manifest.write_bytes(b"external manifest")

    result = save(frame, base)

    assert result.path == tmp_path / "drawing_001.svg"
    assert old_manifest.read_bytes() == b"external manifest"


def test_late_collision_retries_without_reencoding_or_overwriting(
    frame: Frame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = CaptureService()
    base = tmp_path / "drawing.svg"
    real_publish = capture_module.publish_capture_generation
    encode_calls = 0
    real_encode = service.encode

    def count_encode(*args, **kwargs):
        nonlocal encode_calls
        encode_calls += 1
        return real_encode(*args, **kwargs)

    publish_calls = 0

    def collide_once(**kwargs):
        nonlocal publish_calls
        publish_calls += 1
        if publish_calls == 1:
            Path(kwargs["artifact_paths"][0]).write_bytes(b"external late capture")
        return real_publish(**kwargs)

    monkeypatch.setattr(service, "encode", count_encode)
    monkeypatch.setattr(capture_module, "publish_capture_generation", collide_once)

    result = service.export(frame, base)

    assert result.path == tmp_path / "drawing_001.svg"
    assert base.read_bytes() == b"external late capture"
    assert encode_calls == 1
    assert publish_calls == 2


def test_encoder_failure_removes_private_staging_and_publishes_nothing(
    frame: Frame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "broken.svg"

    def fail_svg(_layers, path, *, canvas_size):
        Path(path).write_bytes(b"partial")
        raise RuntimeError("encoder failed")

    monkeypatch.setattr(capture_module, "export_svg", fail_svg)

    with pytest.raises(RuntimeError, match="encoder failed"):
        save(frame, output)

    assert not output.exists()
    assert not capture_manifest_path_for(output).exists()
    assert list(tmp_path.glob(".broken.capture-*")) == []


def test_overwrite_replaces_artifact_and_manifest_as_requested(
    frame: Frame,
    tmp_path: Path,
) -> None:
    output = tmp_path / "drawing.svg"
    output.write_bytes(b"old artifact")
    manifest = capture_manifest_path_for(output)
    manifest.write_bytes(b"old manifest")

    result = save(frame, output, overwrite=True)

    assert result.path == output
    assert output.read_bytes().startswith(b"<?xml")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["output"]["artifact_paths"] == [str(output)]
    assert list(tmp_path.glob(".grafix-capture-backup-*")) == []


def test_export_rejects_non_boolean_overwrite(
    frame: Frame,
    tmp_path: Path,
) -> None:
    with pytest.raises(TypeError, match="overwrite"):
        save(
            frame,
            tmp_path / "drawing.svg",
            overwrite="false",  # type: ignore[arg-type]
        )

    assert not (tmp_path / "drawing.svg").exists()


def test_png_uses_private_svg_and_effective_background(
    frame: Frame,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intermediate_paths: list[Path] = []
    raster_backgrounds: list[tuple[float, float, float]] = []
    raster_sizes: list[tuple[int, int]] = []

    def fake_svg(_layers, path, *, canvas_size):
        svg_path = Path(path)
        intermediate_paths.append(svg_path)
        svg_path.write_text("svg", encoding="utf-8")
        return svg_path

    def fake_rasterize(
        svg_path,
        png_path,
        *,
        background_color_rgb01,
        output_size,
        **_kwargs,
    ):
        assert Path(svg_path).read_text(encoding="utf-8") == "svg"
        raster_backgrounds.append(background_color_rgb01)
        raster_sizes.append(output_size)
        Path(png_path).write_bytes(b"png")
        return Path(png_path)

    monkeypatch.setattr(capture_module, "export_svg", fake_svg)
    monkeypatch.setattr(capture_module, "rasterize_svg_to_png", fake_rasterize)

    result = CaptureService().export(
        frame,
        tmp_path / "drawing.png",
        output_size=(64, 48),
    )

    assert result.path.read_bytes() == b"png"
    assert raster_backgrounds == [frame.background_color_rgb01]
    assert raster_sizes == [(64, 48)]
    assert result.manifest_path is not None
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["output"]["size"] == {"width": 64, "height": 48}
    assert len(intermediate_paths) == 1
    assert not intermediate_paths[0].exists()
    assert intermediate_paths[0] != tmp_path / "drawing.svg"


def test_capture_service_requires_explicit_format_settings(
    frame: Frame,
    tmp_path: Path,
) -> None:
    service = CaptureService()

    with pytest.raises(ValueError, match="output_size"):
        service.export(frame, tmp_path / "missing-size.png")
    with pytest.raises(ValueError, match="gcode_params"):
        service.export(frame, tmp_path / "missing-config.gcode")

    assert list(tmp_path.glob(".missing-*.capture-*")) == []


def test_capture_service_rejects_string_format(frame: Frame, tmp_path: Path) -> None:
    service = CaptureService()
    string_format = cast(Any, "svg")

    with pytest.raises(TypeError, match="ExportFormat"):
        service.encode(frame, tmp_path / "drawing.svg", format=string_format)
    with pytest.raises(TypeError, match="ExportFormat"):
        service.final_paths(frame, tmp_path / "drawing.svg", format=string_format)
    with pytest.raises(TypeError, match="ExportFormat"):
        service.publish_staged(
            frame,
            tmp_path / "drawing.svg",
            (tmp_path / "staged.svg",),
            format=string_format,
        )


def test_capture_service_rejects_settings_for_another_format(
    frame: Frame,
    tmp_path: Path,
) -> None:
    config = frame.metadata.effective_config.gcode

    with pytest.raises(ValueError, match="PNG capture"):
        CaptureService().export(
            frame,
            tmp_path / "drawing.svg",
            output_size=(64, 48),
        )
    with pytest.raises(ValueError, match="G-code capture"):
        CaptureService().export(
            frame,
            tmp_path / "drawing.png",
            output_size=(64, 48),
            gcode_params=config,
        )


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_capture_service_rejects_coerced_publish_retry_count(value: object) -> None:
    with pytest.raises(TypeError, match="max_publish_retries"):
        CaptureService(max_publish_retries=cast(Any, value))


@pytest.mark.parametrize("value", [1, "true"])
def test_capture_service_requires_exact_split_layers_bool(
    frame: Frame,
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises(TypeError, match="split_gcode_layers"):
        CaptureService().encode(
            frame,
            tmp_path / "drawing.gcode",
            format=ExportFormat.GCODE,
            split_gcode_layers=cast(Any, value),
            gcode_params=frame.metadata.effective_config.gcode,
        )


def test_capture_service_rejects_layer_split_for_non_gcode(
    frame: Frame,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="split_gcode_layers"):
        CaptureService().encode(
            frame,
            tmp_path / "drawing.svg",
            format=ExportFormat.SVG,
            split_gcode_layers=True,
        )


@pytest.mark.parametrize("output_size", [(True, 48), (64.0, 48), [64, 48]])
def test_capture_service_rejects_coerced_output_size(
    frame: Frame,
    tmp_path: Path,
    output_size: object,
) -> None:
    with pytest.raises(TypeError, match="output_size"):
        CaptureService().encode(
            frame,
            tmp_path / "drawing.png",
            format=ExportFormat.PNG,
            output_size=cast(Any, output_size),
        )


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("timeout_s", True),
        ("timeout_s", "1"),
        ("deadline_monotonic", True),
        ("deadline_monotonic", "1"),
    ],
)
def test_capture_service_rejects_coerced_png_time_values(
    frame: Frame,
    tmp_path: Path,
    keyword: str,
    value: object,
) -> None:
    kwargs = {
        "format": ExportFormat.PNG,
        "output_size": (64, 48),
        keyword: value,
    }
    with pytest.raises(TypeError, match=keyword):
        CaptureService().encode(
            frame,
            tmp_path / "drawing.png",
            **cast(Any, kwargs),
        )


def test_gcode_capture_is_byte_identical_to_existing_encoder(
    frame: Frame,
    tmp_path: Path,
) -> None:
    expected = tmp_path / "expected.gcode"
    config = frame.metadata.effective_config.gcode
    export_gcode(
        frame.layers,
        expected,
        canvas_size=frame.canvas_size,
        params=config,
    )

    result = CaptureService().export(
        frame,
        tmp_path / "captured.gcode",
        gcode_params=config,
    )

    assert result.path.read_bytes() == expected.read_bytes()


def test_gcode_export_uses_closed_render_session_effective_config(
    tmp_path: Path,
) -> None:
    render_config_path = tmp_path / "render.yaml"
    render_config_path.write_text(
        "version: 1\nexport:\n  gcode:\n    z_up: 17.0\n    decimals: 1\n",
        encoding="utf-8",
    )

    default_config = runtime_config()
    frame = render(lambda _t: (), config_path=render_config_path)
    assert runtime_config() == default_config

    result = save(frame, tmp_path / "closed-session.gcode")
    assert result.format is ExportFormat.GCODE
    assert "G1 Z37.0" in result.path.read_text(encoding="utf-8")
    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["config"]["effective"]["gcode"]["z_up"] == 17.0
    assert manifest["config"]["effective"]["gcode"]["decimals"] == 1


def test_png_export_uses_closed_render_session_effective_scale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    render_config_path = tmp_path / "render.yaml"
    render_config_path.write_text(
        "version: 1\nexport:\n  png:\n    scale: 3.5\n",
        encoding="utf-8",
    )
    observed_sizes: list[tuple[int, int]] = []

    def fake_rasterize(
        _svg_path: Path,
        png_path: Path,
        *,
        output_size: tuple[int, int],
        **_kwargs: object,
    ) -> Path:
        observed_sizes.append(output_size)
        Path(png_path).write_bytes(b"png")
        return Path(png_path)

    monkeypatch.setattr(capture_module, "rasterize_svg_to_png", fake_rasterize)
    default_config = runtime_config()
    frame = render(
        lambda _t: (),
        options=RenderOptions(canvas_size=(10, 8)),
        config_path=render_config_path,
    )
    assert runtime_config() == default_config

    result = save(frame, tmp_path / "closed-session.png")
    assert result.format is ExportFormat.PNG
    assert observed_sizes == [(35, 28)]
    assert result.manifest_path is not None
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["config"]["effective"]["png_scale"] == 3.5
    assert manifest["output"]["size"] == {"width": 35, "height": 28}


def test_export_rejects_unsupported_suffix_before_creating_parent(
    frame: Frame,
    tmp_path: Path,
) -> None:
    parent = tmp_path / "missing"

    with pytest.raises(ValueError, match="suffix"):
        save(frame, parent / "drawing.jpg")

    assert not parent.exists()


def test_public_export_validates_frame_before_path_suffix(tmp_path: Path) -> None:
    parent = tmp_path / "missing"

    with pytest.raises(TypeError, match="frame は Frame"):
        save(object(), parent / "drawing.jpg")  # type: ignore[arg-type]

    assert not parent.exists()


def test_per_layer_option_keeps_existing_layer_naming(
    frame: Frame,
    tmp_path: Path,
) -> None:
    service = CaptureService()
    staged = service.encode(
        frame,
        tmp_path / "drawing.gcode",
        format=ExportFormat.GCODE,
        split_gcode_layers=True,
        gcode_params=frame.metadata.effective_config.gcode,
    )

    assert staged == (tmp_path / "drawing_layer001.gcode",)


def test_per_layer_publish_rejects_empty_scene(frame: Frame, tmp_path: Path) -> None:
    empty_frame = replace(frame, layers=())
    output = tmp_path / "drawing.gcode"

    with pytest.raises(ValueError, match="1 layer"):
        CaptureService().publish_staged(
            empty_frame,
            output,
            (),
            format=ExportFormat.GCODE,
            split_gcode_layers=True,
        )

    assert not output.exists()
    assert not capture_manifest_path_for(output).exists()


def test_publish_rejects_png_only_output_size_for_other_formats(
    frame: Frame,
    tmp_path: Path,
) -> None:
    output = tmp_path / "drawing.svg"

    with pytest.raises(ValueError, match="PNG publish"):
        CaptureService().publish_staged(
            frame,
            output,
            (tmp_path / "staged.svg",),
            format=ExportFormat.SVG,
            output_size=(100, 80),
        )

    assert not output.exists()
    assert not capture_manifest_path_for(output).exists()
