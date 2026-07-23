from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from grafix import ExportFormat, ExportResult, RenderOptions
from grafix.core.runtime_config import RuntimeConfig, current_runtime_config
from grafix.devtools import export_frame


def _draw(_t: float) -> None:
    return None


class _FakeSession:
    def __init__(self, draw: object, **kwargs: object) -> None:
        self.draw = draw
        self.kwargs = kwargs
        self.rendered: list[float] = []
        self.config = SimpleNamespace(png_scale=8.0)

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        return None

    def render(self, t: float) -> SimpleNamespace:
        self.rendered.append(float(t))
        return SimpleNamespace(t=float(t))


def test_main_passes_render_inputs_and_prints_actual_capture_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sessions: list[_FakeSession] = []
    capture_calls: list[dict[str, Any]] = []

    def make_session(draw: object, **kwargs: object) -> _FakeSession:
        session = _FakeSession(draw, **kwargs)
        sessions.append(session)
        return session

    requested_path = tmp_path / "requested.svg"
    actual_path = tmp_path / "actual.svg"
    manifest_path = tmp_path / "actual.svg.capture.json"

    def fake_export(frame: object, path: Path, *, overwrite: bool) -> ExportResult:
        capture_calls.append(
            {"frame": frame, "path": Path(path), "overwrite": overwrite}
        )
        return ExportResult(
            path=actual_path,
            format=ExportFormat.SVG,
            manifest_path=manifest_path,
        )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "paths:\n  output_dir: configured-output\n",
        encoding="utf-8",
    )
    parameter_path = tmp_path / "parameters.json"
    observed_configs: list[RuntimeConfig] = []

    def resolve_callable(_spec: str):
        observed_configs.append(current_runtime_config())
        return _draw

    monkeypatch.setattr(export_frame, "_resolve_callable", resolve_callable)
    monkeypatch.setattr(export_frame, "RenderSession", make_session)
    monkeypatch.setattr(export_frame, "save", fake_export)

    code = export_frame.main(
        [
            "--callable",
            "example:draw",
            "--t",
            "1.25",
            "--canvas",
            "100",
            "200",
            "--out",
            str(requested_path),
            "--run-id",
            "take-a",
            "--parameter-source",
            str(parameter_path),
            "--config",
            str(config_path),
            "--seed",
            "1847",
            "--overwrite",
        ]
    )

    assert code == 0
    assert len(sessions) == 1
    session = sessions[0]
    assert session.draw is _draw
    assert session.rendered == [1.25]
    assert session.kwargs["parameter_source"] == parameter_path
    config = session.kwargs["config"]
    assert isinstance(config, RuntimeConfig)
    assert config.config_path == config_path.resolve()
    assert observed_configs == [config]
    assert session.kwargs["run_id"] == "take-a"
    assert session.kwargs["seed"] == 1847
    options = session.kwargs["options"]
    assert isinstance(options, RenderOptions)
    assert options.canvas_size == (100, 200)
    assert options.line_thickness == pytest.approx(0.001)
    assert capture_calls == [
        {
            "frame": SimpleNamespace(t=1.25),
            "path": requested_path,
            "overwrite": True,
        }
    ]
    output = capsys.readouterr().out
    assert f"Saved SVG: {actual_path}" in output
    assert f"Manifest: {manifest_path}" in output
    assert str(requested_path) not in output


@pytest.mark.parametrize("format_name", ["svg", "png", "gcode"])
def test_main_batch_supports_all_formats_and_no_clobber_by_default(
    format_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: list[_FakeSession] = []
    capture_calls: list[tuple[float, Path, bool]] = []

    def make_session(draw: object, **kwargs: object) -> _FakeSession:
        session = _FakeSession(draw, **kwargs)
        sessions.append(session)
        return session

    def fake_default_path(*_args: object, **_kwargs: object) -> Path:
        return Path(f"base.{format_name}")

    def fake_export(frame: object, path: Path, *, overwrite: bool) -> ExportResult:
        capture_calls.append((float(getattr(frame, "t")), Path(path), overwrite))
        return ExportResult(
            path=Path(path),
            format=ExportFormat(format_name),
            manifest_path=Path(f"{path}.capture.json"),
        )

    monkeypatch.setattr(export_frame, "_resolve_callable", lambda _spec: _draw)
    monkeypatch.setattr(export_frame, "RenderSession", make_session)
    monkeypatch.setattr(export_frame, "_default_output_path", fake_default_path)
    monkeypatch.setattr(export_frame, "save", fake_export)

    output_dir = tmp_path / "frames"
    code = export_frame.main(
        [
            "--callable",
            "example:draw",
            "--format",
            format_name,
            "--t",
            "0",
            "2",
            "--out-dir",
            str(output_dir),
            "--parameter-source",
            "recovery",
        ]
    )

    assert code == 0
    assert sessions[0].kwargs["parameter_source"] == "recovery"
    assert capture_calls == [
        (0.0, output_dir / f"base_f001.{format_name}", False),
        (2.0, output_dir / f"base_f002.{format_name}", False),
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("code", "code"),
        ("SAVED", "saved"),
        ("recovery", "recovery"),
        ("state/custom.json", Path("state/custom.json")),
    ],
)
def test_parameter_source_accepts_modes_and_explicit_path(
    text: str,
    expected: str | Path,
) -> None:
    assert export_frame._parameter_source(text) == expected


def test_explicit_format_must_match_output_suffix() -> None:
    with pytest.raises(SystemExit):
        export_frame._parse_args(
            [
                "--callable",
                "example:draw",
                "--format",
                "png",
                "--out",
                "drawing.svg",
            ]
        )
