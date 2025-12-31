from pathlib import Path

import pytest

from grafix.core.runtime_config import output_root_dir, runtime_config, set_config_path


@pytest.fixture(autouse=True)
def _reset_runtime_config() -> None:
    set_config_path(None)
    yield
    set_config_path(None)


def _isolate_config_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))


def test_output_root_dir_uses_packaged_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_config_discovery(tmp_path, monkeypatch)

    assert output_root_dir() == Path("data") / "output"
    cfg = runtime_config()
    assert cfg.config_path is None
    assert cfg.output_dir == Path("data") / "output"
    assert cfg.sketch_dir == Path("sketch")
    assert cfg.font_dirs == (Path("data") / "input" / "font",)
    assert cfg.window_pos_draw == (25, 25)
    assert cfg.window_pos_parameter_gui == (950, 25)
    assert cfg.parameter_gui_window_size == (800, 1000)
    assert cfg.png_scale == 8.0


def test_discovered_config_overrides_packaged_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_config_discovery(tmp_path, monkeypatch)

    discovered = tmp_path / ".grafix" / "config.yaml"
    discovered.parent.mkdir(parents=True, exist_ok=True)
    discovered.write_text(
        'paths:\n  output_dir: "./out_discovered"\n  font_dirs:\n    - "./fonts_discovered"\n',
        encoding="utf-8",
    )

    assert output_root_dir() == Path("out_discovered")
    cfg = runtime_config()
    assert cfg.config_path == discovered
    assert cfg.output_dir == Path("out_discovered")
    assert cfg.sketch_dir is None
    assert cfg.font_dirs == (Path("fonts_discovered"),)


def test_discovered_sketch_dir_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_config_discovery(tmp_path, monkeypatch)

    discovered = tmp_path / ".grafix" / "config.yaml"
    discovered.parent.mkdir(parents=True, exist_ok=True)
    discovered.write_text(
        'paths:\n  output_dir: "./out_discovered"\n  sketch_dir: "./sketch"\n  font_dirs:\n    - "./fonts_discovered"\n',
        encoding="utf-8",
    )

    cfg = runtime_config()
    assert cfg.sketch_dir == Path("sketch")


def test_explicit_config_overrides_discovered_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_config_discovery(tmp_path, monkeypatch)

    discovered = tmp_path / ".grafix" / "config.yaml"
    discovered.parent.mkdir(parents=True, exist_ok=True)
    discovered.write_text(
        'paths:\n  output_dir: "./out_discovered"\n  font_dirs:\n    - "./fonts_discovered"\n',
        encoding="utf-8",
    )

    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        'paths:\n  output_dir: "./out_explicit"\n  font_dirs:\n    - "./fonts_discovered"\n',
        encoding="utf-8",
    )
    set_config_path(explicit)

    assert output_root_dir() == Path("out_explicit")
    cfg = runtime_config()
    assert cfg.config_path == explicit
    assert cfg.output_dir == Path("out_explicit")
    assert cfg.sketch_dir is None
    assert cfg.font_dirs == (Path("fonts_discovered"),)


def test_environment_variables_are_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_config_discovery(tmp_path, monkeypatch)

    monkeypatch.setenv("GRAFIX_OUTPUT_DIR", str(tmp_path / "out_env"))
    monkeypatch.setenv("GRAFIX_FONT_DIRS", str(tmp_path / "fonts_env"))

    assert output_root_dir() == Path("data") / "output"
    cfg = runtime_config()
    assert cfg.output_dir == Path("data") / "output"
    assert cfg.sketch_dir == Path("sketch")
    assert cfg.font_dirs == (Path("data") / "input" / "font",)


def test_explicit_config_path_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _isolate_config_discovery(tmp_path, monkeypatch)

    missing = tmp_path / "missing.yaml"
    set_config_path(missing)

    with pytest.raises(FileNotFoundError):
        output_root_dir()
