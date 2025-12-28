from pathlib import Path

import pytest

from grafix.core.runtime_config import output_root_dir, set_config_path


@pytest.fixture(autouse=True)
def _reset_runtime_config() -> None:
    set_config_path(None)
    yield
    set_config_path(None)


def test_output_root_dir_defaults_to_data_output(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    set_config_path(config_path)

    assert output_root_dir() == Path("data") / "output"


def test_output_root_dir_uses_output_dir_from_config(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text('output_dir: "./out"\n', encoding="utf-8")
    set_config_path(config_path)

    assert output_root_dir() == Path("out")


def test_output_root_dir_ignores_data_root_in_config(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text('data_root: "./somewhere"\n', encoding="utf-8")
    set_config_path(config_path)

    assert output_root_dir() == Path("data") / "output"


def test_output_root_dir_uses_output_dir_env_when_not_in_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    set_config_path(config_path)

    out_dir = tmp_path / "out_env"
    monkeypatch.setenv("GRAFIX_OUTPUT_DIR", str(out_dir))
    set_config_path(config_path)  # clear cache

    assert output_root_dir() == out_dir


def test_output_root_dir_ignores_grafix_data_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("", encoding="utf-8")
    set_config_path(config_path)

    monkeypatch.setenv("GRAFIX_DATA_ROOT", str(tmp_path / "data_root_env"))
    set_config_path(config_path)  # clear cache

    assert output_root_dir() == Path("data") / "output"


def test_output_root_dir_config_overrides_output_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text('output_dir: "./out_cfg"\n', encoding="utf-8")
    set_config_path(config_path)

    monkeypatch.setenv("GRAFIX_OUTPUT_DIR", str(tmp_path / "out_env"))
    set_config_path(config_path)  # clear cache

    assert output_root_dir() == Path("out_cfg")

