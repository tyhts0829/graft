# どこで: `src/grafix/core/font_resolver.py`。
# 何を: `G.text(font=...)` のフォント探索・解決と、GUI 用の候補列挙を提供する。
# なぜ: repo 直下 `data/` 前提を排し、PyPI インストール環境でも確実に動く経路を用意するため。

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from grafix.core.runtime_config import runtime_config

DEFAULT_FONT_FILENAME = "GoogleSans-Regular.ttf"
_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")


@dataclass(frozen=True, slots=True)
class FontChoice:
    """フォント候補（GUI 表示用）。"""

    stem: str
    value: str
    is_ttc: bool
    search_key: str


_PACKAGED_FONT_DIR: Path | None = None
_FONT_FILES_CACHE: dict[tuple[str, ...], tuple[Path, ...]] = {}
_FONT_CHOICES_CACHE: dict[tuple[str, ...], tuple[FontChoice, ...]] = {}


def _packaged_font_dir() -> Path | None:
    global _PACKAGED_FONT_DIR
    if _PACKAGED_FONT_DIR is not None:
        return _PACKAGED_FONT_DIR

    try:
        base = resources.files("grafix")
    except Exception:
        _PACKAGED_FONT_DIR = None
        return None

    candidate = base.joinpath("resource", "font", "Google_Sans", "static")
    try:
        path = Path(candidate)  # type: ignore[arg-type]
    except TypeError:
        _PACKAGED_FONT_DIR = None
        return None

    _PACKAGED_FONT_DIR = path if path.is_dir() else None
    return _PACKAGED_FONT_DIR


def _search_dirs() -> tuple[Path, ...]:
    cfg = runtime_config()
    dirs: list[Path] = []
    for d in cfg.font_dirs:
        try:
            p = Path(d).expanduser()
        except Exception:
            continue
        dirs.append(p)

    packaged = _packaged_font_dir()
    if packaged is not None:
        dirs.append(packaged)

    return tuple(dirs)


def _list_font_files(*, dirs: tuple[Path, ...]) -> tuple[Path, ...]:
    key = tuple(str(d) for d in dirs)
    cached = _FONT_FILES_CACHE.get(key)
    if cached is not None:
        return cached

    seen: list[Path] = []
    for root in dirs:
        if not root.is_dir():
            continue
        for ext in _FONT_EXTENSIONS:
            for fp in root.glob(f"**/*{ext}"):
                try:
                    resolved = fp.resolve()
                except Exception:
                    continue
                if resolved.is_file():
                    seen.append(resolved)

    out = tuple(sorted(set(seen)))
    _FONT_FILES_CACHE[key] = out
    return out


def default_font_path() -> Path:
    """既定フォントの実体パスを返す。"""

    packaged = _packaged_font_dir()
    if packaged is not None:
        fp = packaged / DEFAULT_FONT_FILENAME
        if fp.is_file():
            return fp.resolve()

    # 同梱が無い場合も、探索ディレクトリ内に同名があれば拾う。
    for d in _search_dirs():
        fp = d / DEFAULT_FONT_FILENAME
        if fp.is_file():
            return fp.resolve()

    raise FileNotFoundError(
        "既定フォントが見つかりません"
        f": default={DEFAULT_FONT_FILENAME!r}, packaged_dir={packaged}"
    )


def resolve_font_path(font: str) -> Path:
    """`font` 指定を実体ファイルへ解決して返す。"""

    raw = str(font).strip()
    if not raw:
        return default_font_path()

    # 0) 直接パス（絶対/相対）を許容
    direct_path = Path(raw).expanduser()
    if direct_path.is_file():
        return direct_path.resolve()

    # 1) 探索ディレクトリ直下のファイル名一致
    for d in _search_dirs():
        fp = d / raw
        if fp.is_file():
            return fp.resolve()

    # 2) 部分一致（安定順: dirs の順 → ファイル名の安定ソート）
    dirs = _search_dirs()
    files = _list_font_files(dirs=dirs)
    key = raw.lower().replace(" ", "")
    for fp in files:
        name = fp.name.lower().replace(" ", "")
        stem = fp.stem.lower().replace(" ", "")
        if key in name or key in stem:
            return fp

    searched = ", ".join(str(d) for d in dirs) if dirs else "(none)"
    cfg = runtime_config()
    example_yaml = "font_dirs:\n  - \"~/Fonts\"\n"
    hint = (
        "フォントが見つかりません。"
        " `font` に実在パスを渡すか、config.yaml の `font_dirs` を設定してください"
        "（例: ./.grafix/config.yaml または ~/.config/grafix/config.yaml）。"
        f"\n\n{example_yaml}\nsearched_dirs={searched}, config_path={cfg.config_path}"
    )
    raise FileNotFoundError(hint)


def list_font_choices() -> tuple[tuple[str, str, bool, str], ...]:
    """GUI 用のフォント候補列を返す（キャッシュ）。"""

    dirs = _search_dirs()
    key = tuple(str(d) for d in dirs)
    cached = _FONT_CHOICES_CACHE.get(key)
    if cached is not None:
        return tuple((c.stem, c.value, c.is_ttc, c.search_key) for c in cached)

    files = _list_font_files(dirs=dirs)

    by_value: dict[str, FontChoice] = {}
    for fp in files:
        value = fp.name
        if value in by_value:
            continue
        stem = fp.stem
        is_ttc = fp.suffix.lower() == ".ttc"
        search_key = f"{value} {stem}".lower()
        by_value[value] = FontChoice(stem=stem, value=value, is_ttc=is_ttc, search_key=search_key)

    choices = tuple(by_value[v] for v in sorted(by_value.keys(), key=str))
    _FONT_CHOICES_CACHE[key] = choices
    return tuple((c.stem, c.value, c.is_ttc, c.search_key) for c in choices)


__all__ = [
    "DEFAULT_FONT_FILENAME",
    "FontChoice",
    "default_font_path",
    "list_font_choices",
    "resolve_font_path",
]
