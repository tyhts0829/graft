"""EvaluationConfig に基づく stateless なフォント探索を提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from grafix.core.evaluation_config import (
    EvaluationConfig,
    current_evaluation_config,
)

DEFAULT_FONT_FILENAME = "GoogleSans-Regular.ttf"
_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")


@dataclass(frozen=True, slots=True)
class FontChoice:
    """フォント候補（GUI 表示用）。"""

    stem: str
    value: str
    is_ttc: bool
    search_key: str


def _effective_config(config: EvaluationConfig | None) -> EvaluationConfig:
    """明示 config、または現在の評価設定を返す。"""

    if config is None:
        return current_evaluation_config()
    if type(config) is not EvaluationConfig:
        raise TypeError("config は exact EvaluationConfig または None です")
    return config


def _packaged_font_dirs() -> tuple[Path, ...]:
    """インストール済み package に含まれるフォントディレクトリを返す。"""

    try:
        base = resources.files("grafix")
    except (ModuleNotFoundError, TypeError):
        return ()

    candidates = (
        base.joinpath("resource", "font", "Google_Sans", "static"),
        base.joinpath("resource", "font", "Noto_Sans_JP", "static"),
    )
    directories: list[Path] = []
    for candidate in candidates:
        try:
            path = Path(candidate)  # type: ignore[arg-type]
        except TypeError:
            continue
        if path.is_dir():
            directories.append(path)
    return tuple(directories)


def _search_dirs(config: EvaluationConfig) -> tuple[Path, ...]:
    """config 優先順を保った探索ディレクトリ列を返す。"""

    return (*config.font_dirs, *_packaged_font_dirs())


def _list_font_files(*, dirs: tuple[Path, ...]) -> tuple[Path, ...]:
    """ディレクトリ優先順と各ディレクトリ内の安定順でフォントを列挙する。"""

    seen: set[Path] = set()
    files: list[Path] = []
    for root in dirs:
        if not root.is_dir():
            continue
        candidates = sorted(
            (path for extension in _FONT_EXTENSIONS for path in root.glob(f"**/*{extension}")),
            key=lambda path: path.as_posix(),
        )
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not resolved.is_file() or resolved in seen:
                continue
            seen.add(resolved)
            files.append(resolved)
    return tuple(files)


def default_font_path(*, config: EvaluationConfig | None = None) -> Path:
    """既定フォントの実体パスを返す。"""

    effective_config = _effective_config(config)
    packaged_dirs = _packaged_font_dirs()
    for packaged in packaged_dirs:
        candidate = packaged / DEFAULT_FONT_FILENAME
        if candidate.is_file():
            return candidate.resolve()

    # package data を利用できない環境では明示 config の同名 asset を使う。
    for directory in effective_config.font_dirs:
        candidate = directory / DEFAULT_FONT_FILENAME
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "既定フォントが見つかりません"
        f": default={DEFAULT_FONT_FILENAME!r}, packaged_dirs={packaged_dirs}"
    )


def resolve_font_path(
    font: str,
    *,
    config: EvaluationConfig | None = None,
) -> Path:
    """``font`` 指定を固定済み config で実体ファイルへ解決する。

    ``config`` を渡した評価経路では ambient config を参照しない。省略は GUI と
    devtool の convenience 用であり、その呼び出し時点の config を一度だけ取得する。
    """

    effective_config = _effective_config(config)
    raw = str(font).strip()
    if not raw:
        return default_font_path(config=effective_config)

    direct_path = Path(raw).expanduser()
    if direct_path.is_file():
        return direct_path.resolve()

    directories = _search_dirs(effective_config)
    for directory in directories:
        candidate = directory / raw
        if candidate.is_file():
            return candidate.resolve()

    key = raw.lower().replace(" ", "")
    for candidate in _list_font_files(dirs=directories):
        name = candidate.name.lower().replace(" ", "")
        stem = candidate.stem.lower().replace(" ", "")
        if key in name or key in stem:
            return candidate

    searched = ", ".join(str(directory) for directory in directories) or "(none)"
    example_yaml = 'font_dirs:\n  - "~/Fonts"\n'
    hint = (
        "フォントが見つかりません。"
        " `font` に実在パスを渡すか、config.yaml の `font_dirs` を設定してください"
        "（例: ./.grafix/config.yaml または ~/.config/grafix/config.yaml）。"
        f"\n\n{example_yaml}\nsearched_dirs={searched}"
    )
    raise FileNotFoundError(hint)


def list_font_choices(
    *,
    config: EvaluationConfig | None = None,
) -> tuple[tuple[str, str, bool, str], ...]:
    """呼び出し時点の filesystem を反映した GUI 用フォント候補を返す。"""

    effective_config = _effective_config(config)
    files = _list_font_files(dirs=_search_dirs(effective_config))
    by_value: dict[str, FontChoice] = {}
    for path in files:
        value = path.name
        if value in by_value:
            continue
        stem = path.stem
        by_value[value] = FontChoice(
            stem=stem,
            value=value,
            is_ttc=path.suffix.lower() == ".ttc",
            search_key=f"{value} {stem}".lower(),
        )

    choices = tuple(by_value[value] for value in sorted(by_value))
    return tuple(
        (choice.stem, choice.value, choice.is_ttc, choice.search_key) for choice in choices
    )


__all__ = [
    "DEFAULT_FONT_FILENAME",
    "FontChoice",
    "default_font_path",
    "list_font_choices",
    "resolve_font_path",
]
