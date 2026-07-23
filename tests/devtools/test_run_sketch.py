from __future__ import annotations

# ruff: noqa: E402 -- pyglet option must be set before importing runner modules.

import importlib
from pathlib import Path

import pyglet
import pytest

# runner import時のshadow GL contextをCI/headless環境では作らない。
pyglet.options["shadow_window"] = False

from grafix import __main__ as grafix_main
from grafix.core.runtime_config import RuntimeConfigFallback
from grafix.devtools import run_sketch


def _write_sketch(path: Path) -> None:
    path.write_text("def draw(t):\n    return []\n", encoding="utf-8")


def test_run_cli_passes_transactional_controller_only_with_watch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sketch = tmp_path / "art.py"
    _write_sketch(sketch)
    calls: list[dict[str, object]] = []
    runner_module = importlib.import_module("grafix.api._runner_application")
    source_reload_module = importlib.import_module(
        "grafix.interactive.runtime.source_reload"
    )

    def fake_run(_draw: object, **kwargs: object) -> None:
        calls.append(
            {
                **kwargs,
                "source_reload": source_reload_module.current_source_reload(),
            }
        )

    monkeypatch.setattr(runner_module, "_run_interactive_application", fake_run)

    assert run_sketch.main([str(sketch), "--watch", "--no-parameter-gui"]) == 0
    assert calls[0]["parameter_gui"] is False
    controller = calls[0]["source_reload"]
    assert controller is not None
    assert controller.path == sketch.resolve()

    calls.clear()
    assert run_sketch.main([str(sketch), "--no-parameter-gui"]) == 0
    assert calls[0]["source_reload"] is None


def test_main_dispatches_run_subcommand(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        seen.append(argv)
        return 7

    monkeypatch.setattr(run_sketch, "main", fake_main)

    assert grafix_main.main(["run", "--", "sketch.py", "--watch"]) == 7
    assert seen == [["sketch.py", "--watch"]]


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("none", None),
        ("off", "off"),
        ("NONE", "NONE"),
        (" none ", " none "),
        ("", ""),
    ],
)
def test_run_cli_uses_only_exact_none_to_disable_midi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    expected: str | None,
) -> None:
    sketch = tmp_path / "art.py"
    _write_sketch(sketch)
    seen: list[str | None] = []
    runner_module = importlib.import_module("grafix.api._runner_application")

    def fake_run(_draw: object, **kwargs: object) -> None:
        value = kwargs["midi_port_name"]
        assert value is None or isinstance(value, str)
        seen.append(value)

    monkeypatch.setattr(runner_module, "_run_interactive_application", fake_run)

    assert run_sketch.main([str(sketch), "--midi-port", token]) == 0
    assert seen == [expected]


def test_run_cli_reports_initial_source_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sketch = tmp_path / "broken.py"
    sketch.write_text("def draw(:\n", encoding="utf-8")

    assert run_sketch.main([str(sketch), "--no-parameter-gui"]) == 1
    captured = capsys.readouterr()
    assert "SyntaxError" in captured.err


def test_run_cli_binds_explicit_config_during_initial_source_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = (tmp_path / "configured-output").resolve()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "paths:\n  output_dir: configured-output\n",
        encoding="utf-8",
    )
    sketch = tmp_path / "config_probe.py"
    sketch.write_text(
        "from grafix.core.runtime_config import current_runtime_config\n"
        f"assert str(current_runtime_config().output_dir) == {str(output_dir)!r}\n"
        "def draw(t):\n"
        "    return []\n",
        encoding="utf-8",
    )
    runner_module = importlib.import_module("grafix.api._runner_application")
    monkeypatch.setattr(
        runner_module,
        "_run_interactive_application",
        lambda *_args, **_kwargs: None,
    )

    assert run_sketch.main(
        [
            str(sketch),
            "--config",
            str(config_path),
            "--no-parameter-gui",
        ]
    ) == 0


def test_run_cli_forwards_invalid_config_fallback_to_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("unknown_section: true\n", encoding="utf-8")
    sketch = tmp_path / "art.py"
    _write_sketch(sketch)
    seen: list[dict[str, object]] = []
    runner_module = importlib.import_module("grafix.api._runner_application")
    monkeypatch.setattr(
        runner_module,
        "_run_interactive_application",
        lambda _draw, **kwargs: seen.append(kwargs),
    )

    assert run_sketch.main(
        [
            str(sketch),
            "--config",
            str(config_path),
            "--no-parameter-gui",
        ]
    ) == 0

    fallback = seen[0]["config_fallback"]
    assert isinstance(fallback, RuntimeConfigFallback)
    assert fallback.source == config_path.resolve()
