"""
どこで: `util.fonts`。
何を: フォント探索・解決の共通ユーティリティ（検索ディレクトリの正規化、再帰列挙、安定ソート）。
なぜ: Text/HUD/DPG で重複していたロジックを集約し、保守性と一貫性を高めるため。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable, Sequence

from .utils import _find_project_root

EXTS_DEFAULT: tuple[str, ...] = (".ttf", ".otf", ".ttc")


def _normalize_dirs(cfg_dirs: Sequence[str | Path]) -> list[str | Path]:
    out: list[str | Path] = []
    for d in cfg_dirs:
        if isinstance(d, (str, Path)):
            out.append(d)
    return out


def resolve_search_dirs(cfg_dirs: Sequence[str | Path]) -> list[Path]:
    """設定由来の検索ディレクトリを正規化して返す（相対→ルート基準、~・環境変数を展開）。"""
    root = _find_project_root(Path(__file__).parent)
    result: list[Path] = []
    for raw in _normalize_dirs(cfg_dirs):
        try:
            p = Path(os.path.expandvars(os.path.expanduser(str(raw))))
            if not p.is_absolute():
                p = (root / p).resolve()
            if p.exists() and p.is_dir():
                result.append(p)
        except Exception:
            continue
    return result


def os_font_dirs() -> list[Path]:
    """OS 既定のフォントディレクトリ一覧（存在するもののみ）。"""
    home = Path.home()
    dirs: list[Path] = []
    if sys.platform == "darwin":
        dirs = [
            home / "Library" / "Fonts",
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
        ]
    elif sys.platform.startswith("linux"):
        dirs = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            home / ".fonts",
            home / ".local/share/fonts",
        ]
    else:
        windir = os.environ.get("WINDIR", r"C:\\Windows")
        dirs = [Path(windir) / "Fonts"]
    return [p for p in dirs if p.exists()]


def glob_font_files(dirs: Iterable[Path], exts: Sequence[str] | None = None) -> list[Path]:
    """与えられたディレクトリ配下のフォントファイルを再帰列挙（重複除去/安定ソート）。"""
    suffixes = tuple(exts) if exts is not None else EXTS_DEFAULT
    seen: set[Path] = set()
    for d in dirs:
        try:
            for ext in suffixes:
                for fp in d.glob(f"**/*{ext}"):
                    try:
                        seen.add(fp.resolve())
                    except Exception:
                        continue
        except Exception:
            continue
    return sorted(seen)


def filter_files_for_family(files: Sequence[Path], family: str | None) -> list[Path]:
    """簡易的な family 名によるファイルフィルタ（部分一致・大文字小文字無視）。"""
    if not family:
        return list(files)
    key = str(family).lower().replace(" ", "")
    out: list[Path] = []
    for fp in files:
        name = fp.stem.lower().replace(" ", "")
        if key in name:
            out.append(fp)
    return out


def debug_print(msg: str) -> None:
    """PXD_DEBUG_FONTS が真のときだけ出力する軽量デバッグ出力。"""
    try:
        from common.settings import get as _get_settings

        if _get_settings().DEBUG_FONTS:
            try:
                print(msg)
            except Exception:
                pass
    except Exception:
        if os.environ.get("PXD_DEBUG_FONTS"):
            try:
                print(msg)
            except Exception:
                pass
