from __future__ import annotations

from pathlib import Path

from grafix.core.evaluation_config import EvaluationConfig, bind_evaluation_config
from grafix.core.font_resolver import (
    DEFAULT_FONT_FILENAME,
    default_font_path,
    list_font_choices,
    resolve_font_path,
)
from grafix.runtime_config_loader import load_runtime_config


def _config_with_font_dirs(tmp_path: Path, *font_dirs: Path):
    cfg_path = tmp_path / f"config-{len(tuple(tmp_path.iterdir()))}.yaml"
    rows = ["version: 1", "paths:", '  output_dir: "data/output"', "  font_dirs:"]
    rows.extend(f'    - "{directory}"' for directory in font_dirs)
    cfg_path.write_text("\n".join((*rows, "")), encoding="utf-8")
    config = load_runtime_config(cfg_path)
    return EvaluationConfig(font_dirs=config.font_dirs)


def test_default_font_path_exists() -> None:
    path = default_font_path()
    assert isinstance(path, Path)
    assert path.is_file()
    assert path.name == DEFAULT_FONT_FILENAME


def test_list_font_choices_contains_default() -> None:
    choices = list_font_choices()
    assert any(value == DEFAULT_FONT_FILENAME for _stem, value, _is_ttc, _search_key in choices)


def test_resolve_font_path_respects_priority_explicit_path_over_config(tmp_path) -> None:
    bundled = default_font_path()

    # config font dir（同名のフォントを置く）
    font_dir = tmp_path / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    copied = font_dir / DEFAULT_FONT_FILENAME
    copied.write_bytes(bundled.read_bytes())

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "\n".join(
            [
                "version: 1",
                "paths:",
                '  output_dir: "data/output"',
                "  font_dirs:",
                f'    - "{font_dir}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    runtime_config = load_runtime_config(cfg_path)
    with bind_evaluation_config(
        EvaluationConfig(font_dirs=runtime_config.font_dirs)
    ):
        # 1) name 指定は config の font_dirs が優先される
        assert resolve_font_path(DEFAULT_FONT_FILENAME) == copied.resolve()

        # 2) 実在パスは config より優先される
        assert resolve_font_path(str(bundled)) == bundled.resolve()


def test_resolve_font_path_accepts_explicit_fixed_config(tmp_path: Path) -> None:
    bundled = default_font_path()
    font_a = tmp_path / "a"
    font_b = tmp_path / "b"
    font_a.mkdir()
    font_b.mkdir()
    path_a = font_a / "Shared.ttf"
    path_b = font_b / "Shared.ttf"
    path_a.write_bytes(bundled.read_bytes())
    path_b.write_bytes(bundled.read_bytes())
    config_a = _config_with_font_dirs(tmp_path, font_a)
    config_b = _config_with_font_dirs(tmp_path, font_b)

    assert resolve_font_path("Shared.ttf", config=config_a) == path_a.resolve()
    assert resolve_font_path("Shared.ttf", config=config_b) == path_b.resolve()


def test_partial_match_rechecks_search_dirs_on_every_lookup(tmp_path: Path) -> None:
    preferred = tmp_path / "preferred"
    fallback = tmp_path / "fallback"
    preferred.mkdir()
    fallback.mkdir()
    fallback_font = fallback / "DynamicFallback.ttf"
    preferred_font = preferred / "DynamicPreferred.ttf"
    fallback_font.write_bytes(b"fallback")
    config = _config_with_font_dirs(tmp_path, preferred, fallback)

    assert resolve_font_path("Dynamic", config=config) == fallback_font.resolve()

    preferred_font.write_bytes(b"preferred")
    assert resolve_font_path("Dynamic", config=config) == preferred_font.resolve()

    preferred_font.unlink()
    assert resolve_font_path("Dynamic", config=config) == fallback_font.resolve()


def test_list_font_choices_observes_files_appearing_after_first_call(
    tmp_path: Path,
) -> None:
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    config = _config_with_font_dirs(tmp_path, font_dir)

    before = list_font_choices(config=config)
    added = font_dir / "AppearedLater.ttf"
    added.write_bytes(b"font")
    after = list_font_choices(config=config)

    assert all(value != added.name for _stem, value, _ttc, _key in before)
    assert any(value == added.name for _stem, value, _ttc, _key in after)


def test_resolve_font_path_error_message_contains_hints() -> None:
    try:
        resolve_font_path("___no_such_font___")
    except FileNotFoundError as exc:
        msg = str(exc)
        assert "searched_dirs=" in msg
        assert "font_dirs:" in msg
    else:  # pragma: no cover
        raise AssertionError("resolve_font_path は FileNotFoundError を送出する必要がある")
