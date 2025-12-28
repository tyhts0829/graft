from __future__ import annotations

from pathlib import Path

from grafix.core.font_resolver import DEFAULT_FONT_FILENAME, default_font_path, list_font_choices, resolve_font_path
from grafix.core.runtime_config import set_config_path


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

    set_config_path(cfg_path)
    try:
        # 1) name 指定は config の font_dirs が優先される
        assert resolve_font_path(DEFAULT_FONT_FILENAME) == copied.resolve()

        # 2) 実在パスは config より優先される
        assert resolve_font_path(str(bundled)) == bundled.resolve()
    finally:
        set_config_path(None)


def test_resolve_font_path_error_message_contains_hints() -> None:
    set_config_path(None)
    try:
        try:
            resolve_font_path("___no_such_font___")
        except FileNotFoundError as exc:
            msg = str(exc)
            assert "searched_dirs=" in msg
            assert "font_dirs:" in msg
        else:  # pragma: no cover
            raise AssertionError("resolve_font_path は FileNotFoundError を送出する必要がある")
    finally:
        set_config_path(None)
