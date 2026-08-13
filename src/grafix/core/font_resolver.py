"""
Purpose:
    logical な font 指定を EvaluationConfig と package asset から実 path へ解決し、GUI 用候補も同じ探索意味で作る。
Use when:
    font 探索順、basename/相対 path 解決、tree 変更検知、GUI 候補を変更・調査する場合。
Constraints:
    - resolver snapshot は evaluation owner の局所 state とし、process-global cache にしない。
    - search root・directory membership・font 候補 symlink の identity 変化を陳腐化した tree で隠さない。
    - direct path と search-root-relative path は呼び出し時点の filesystem で確認する。
Side effects:
    font directory tree の列挙と file/symlink stat を行う。
"""

from __future__ import annotations

import stat as stat_module
import threading
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


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    """探索 directory の存在状態と membership 変更を検知する identity。"""

    path: Path
    file_type: int | None
    device: int | None
    inode: int | None
    modified_ns: int | None
    changed_ns: int | None


@dataclass(frozen=True, slots=True)
class _SymlinkIdentity:
    """font候補symlink自体とresolve先のidentity。danglingも保持する。"""

    path: Path
    link_stat: tuple[int, int, int, int, int, int] | None
    target_path: Path | None
    target_stat: tuple[int, int, int, int, int, int] | None


@dataclass(frozen=True, slots=True)
class _FontEntry:
    """探索順と search-root-relative provenance を保持する font entry。"""

    path: Path
    relative_value: str
    normalized_name: str
    normalized_stem: str


@dataclass(frozen=True, slots=True)
class _FontTreeSnapshot:
    """一組の探索 root に対する immutable な font tree snapshot。"""

    search_dirs: tuple[Path, ...]
    directory_identities: tuple[_DirectoryIdentity, ...]
    symlink_identities: tuple[_SymlinkIdentity, ...]
    entries: tuple[_FontEntry, ...]


@dataclass(frozen=True, slots=True)
class _FontTreeScan:
    """scan 結果と、scan 中に directory tree が不変だったかを表す。"""

    snapshot: _FontTreeSnapshot
    stable: bool


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


def _normalize_font_key(value: str) -> str:
    """従来の部分一致規則で比較可能な文字列へ正規化する。"""

    return value.lower().replace(" ", "")


def _directory_identity(path: Path) -> _DirectoryIdentity:
    """directory、missing root、非 directory を同じ形式で識別する。"""

    try:
        value = path.stat()
    except OSError:
        return _DirectoryIdentity(
            path=path,
            file_type=None,
            device=None,
            inode=None,
            modified_ns=None,
            changed_ns=None,
        )
    return _DirectoryIdentity(
        path=path,
        file_type=stat_module.S_IFMT(value.st_mode),
        device=int(value.st_dev),
        inode=int(value.st_ino),
        modified_ns=int(value.st_mtime_ns),
        changed_ns=int(value.st_ctime_ns),
    )


def _path_stat_identity(
    path: Path,
    *,
    follow_symlinks: bool,
) -> tuple[int, int, int, int, int, int] | None:
    """通常fileとsymlinkの双方に使えるstat identityを返す。"""

    try:
        value = path.stat() if follow_symlinks else path.lstat()
    except OSError:
        return None
    return (
        stat_module.S_IFMT(value.st_mode),
        int(value.st_size),
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
    )


def _symlink_identity(path: Path) -> _SymlinkIdentity:
    """symlinkの付替えと外部targetの出現・消失を検知可能にする。"""

    link_stat = _path_stat_identity(path, follow_symlinks=False)
    try:
        target_path = path.resolve()
    except (OSError, RuntimeError):
        target_path = None
    target_stat = (
        None if target_path is None else _path_stat_identity(target_path, follow_symlinks=True)
    )
    return _SymlinkIdentity(
        path=path,
        link_stat=link_stat,
        target_path=target_path,
        target_stat=target_stat,
    )


def _ordered_unique_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """lexical path の重複を除き、比較可能な安定順へ並べる。"""

    unique = dict.fromkeys(paths)
    return tuple(sorted(unique, key=lambda path: path.as_posix()))


def _enumerate_tree(
    *,
    dirs: tuple[Path, ...],
) -> tuple[tuple[Path, ...], tuple[tuple[Path, Path], ...]]:
    """空 directory を含む tree membership と font 候補を列挙する。"""

    directory_paths: list[Path] = []
    candidates: list[tuple[Path, Path]] = []
    for root in dirs:
        directory_paths.append(root)
        if not root.is_dir():
            continue
        try:
            descendants = tuple(root.glob("**/*"))
        except OSError:
            continue

        root_candidates: list[Path] = []
        for descendant in descendants:
            try:
                is_directory = descendant.is_dir()
            except OSError:
                continue
            if is_directory:
                directory_paths.append(descendant)
            elif descendant.suffix in _FONT_EXTENSIONS:
                root_candidates.append(descendant)
        candidates.extend(
            (root, candidate)
            for candidate in sorted(root_candidates, key=lambda path: path.as_posix())
        )

    return _ordered_unique_paths(tuple(directory_paths)), tuple(candidates)


def _capture_directory_identities(
    paths: tuple[Path, ...],
) -> tuple[_DirectoryIdentity, ...]:
    """指定済み directory path 列の現在 identity を固定する。"""

    return tuple(_directory_identity(path) for path in paths)


def _capture_symlink_identities(
    candidates: tuple[tuple[Path, Path], ...],
) -> tuple[_SymlinkIdentity, ...]:
    """font suffixを持つsymlink候補を、danglingを含めて固定する。"""

    paths = _ordered_unique_paths(
        tuple(candidate for _root, candidate in candidates if candidate.is_symlink())
    )
    return tuple(_symlink_identity(path) for path in paths)


def _build_font_entries(
    candidates: tuple[tuple[Path, Path], ...],
) -> tuple[_FontEntry, ...]:
    """探索順を維持しつつ canonical path の重複を除く。"""

    seen: set[Path] = set()
    entries: list[_FontEntry] = []
    for root, candidate in candidates:
        try:
            resolved = candidate.resolve()
            relative_value = candidate.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if not resolved.is_file() or resolved in seen:
            continue
        seen.add(resolved)
        entries.append(
            _FontEntry(
                path=resolved,
                relative_value=relative_value,
                normalized_name=_normalize_font_key(resolved.name),
                normalized_stem=_normalize_font_key(resolved.stem),
            )
        )
    return tuple(entries)


def _scan_font_tree(*, dirs: tuple[Path, ...]) -> _FontTreeScan:
    """font tree を走査し、scan 前後の membership identity も固定する。"""

    before_paths, before_candidates = _enumerate_tree(dirs=dirs)
    before_identities = _capture_directory_identities(before_paths)
    before_symlink_identities = _capture_symlink_identities(before_candidates)

    after_paths, candidates = _enumerate_tree(dirs=dirs)
    entries = _build_font_entries(candidates)
    after_identities = _capture_directory_identities(after_paths)
    after_symlink_identities = _capture_symlink_identities(candidates)
    snapshot = _FontTreeSnapshot(
        search_dirs=dirs,
        directory_identities=after_identities,
        symlink_identities=after_symlink_identities,
        entries=entries,
    )
    return _FontTreeScan(
        snapshot=snapshot,
        stable=(
            before_identities == after_identities
            and before_symlink_identities == after_symlink_identities
        ),
    )


def _directory_identities_are_current(
    identities: tuple[_DirectoryIdentity, ...],
) -> bool:
    """再帰列挙せず既知 directory の stat だけでsnapshot freshnessを判定する。"""

    return all(_directory_identity(identity.path) == identity for identity in identities)


def _symlink_identities_are_current(
    identities: tuple[_SymlinkIdentity, ...],
) -> bool:
    """候補symlinkと外部targetがsnapshot時点から不変かを判定する。"""

    return all(_symlink_identity(identity.path) == identity for identity in identities)


def _font_not_found(font_dirs: tuple[Path, ...]) -> FileNotFoundError:
    """従来の設定 hint を含む解決失敗を構築する。"""

    searched = ", ".join(str(directory) for directory in font_dirs) or "(none)"
    example_yaml = 'font_dirs:\n  - "~/Fonts"\n'
    hint = (
        "フォントが見つかりません。"
        " `font` に実在パスを渡すか、config.yaml の `font_dirs` を設定してください"
        "（例: ./.grafix/config.yaml または ~/.config/grafix/config.yaml）。"
        f"\n\n{example_yaml}\nsearched_dirs={searched}"
    )
    return FileNotFoundError(hint)


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


class FontPathResolver:
    """一 evaluation owner だけが所有する最新1件のfont tree resolver。

    direct path と search-root-relative path は毎回直接確認する。部分一致だけは
    directory identity と font 候補 symlink の identity が変わるまで、
    immutable tree snapshot を再利用する。
    """

    __slots__ = ("_lock", "_snapshot")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot: _FontTreeSnapshot | None = None

    def resolve(self, font: str, *, config: EvaluationConfig) -> Path:
        """``font`` を解決し、同一owner内ではstable tree scanを再利用する。"""

        if type(config) is not EvaluationConfig:
            raise TypeError("config は exact EvaluationConfig です")
        directories = _search_dirs(config)
        raw = str(font).strip()
        with self._lock:
            if self._snapshot is not None and self._snapshot.search_dirs != directories:
                self._snapshot = None

            if not raw:
                return default_font_path(config=config)

            direct_path = Path(raw).expanduser()
            if direct_path.is_file():
                return direct_path.resolve()

            for directory in directories:
                candidate = directory / raw
                if candidate.is_file():
                    return candidate.resolve()

            snapshot = self._current_snapshot(directories)
            key = _normalize_font_key(raw)
            for entry in snapshot.entries:
                if key in entry.normalized_name or key in entry.normalized_stem:
                    return entry.path

        raise _font_not_found(directories)

    def _current_snapshot(self, directories: tuple[Path, ...]) -> _FontTreeSnapshot:
        """fresh snapshotを返し、不安定なscan結果はownerへ保持しない。"""

        snapshot = self._snapshot
        if (
            snapshot is not None
            and snapshot.search_dirs == directories
            and _directory_identities_are_current(snapshot.directory_identities)
            and _symlink_identities_are_current(snapshot.symlink_identities)
        ):
            return snapshot

        self._snapshot = None
        scan = _scan_font_tree(dirs=directories)
        if scan.stable:
            self._snapshot = scan.snapshot
        return scan.snapshot

    def clear(self) -> None:
        """owner-local tree snapshotを破棄する。"""

        with self._lock:
            self._snapshot = None


def resolve_font_path(
    font: str,
    *,
    config: EvaluationConfig | None = None,
) -> Path:
    """``font`` 指定を呼び出し時点のfilesystemから解決する。

    convenience API 自体はsnapshotを所有しない。評価hot pathでは
    :class:`FontPathResolver`をsession ownerへ保持して使う。
    """

    effective_config = _effective_config(config)
    return FontPathResolver().resolve(font, config=effective_config)


def list_font_choices(
    *,
    config: EvaluationConfig | None = None,
) -> tuple[tuple[str, str, bool, str], ...]:
    """呼び出し時点のfilesystemを反映したGUI用フォント候補を返す。

    nested font のvalueは探索rootからのrelative POSIX pathとなる。同じrelative
    valueは探索順の先頭だけを公開し、異なるrelative pathは同basenameでも保持する。
    """

    effective_config = _effective_config(config)
    scan = _scan_font_tree(dirs=_search_dirs(effective_config))
    by_value: dict[str, FontChoice] = {}
    for entry in scan.snapshot.entries:
        value = entry.relative_value
        if value in by_value:
            continue
        stem = entry.path.stem
        by_value[value] = FontChoice(
            stem=stem,
            value=value,
            is_ttc=entry.path.suffix.lower() == ".ttc",
            search_key=f"{value} {stem}".lower(),
        )

    choices = tuple(by_value[value] for value in sorted(by_value))
    return tuple(
        (choice.stem, choice.value, choice.is_ttc, choice.search_key) for choice in choices
    )


__all__ = [
    "DEFAULT_FONT_FILENAME",
    "FontChoice",
    "FontPathResolver",
    "default_font_path",
    "list_font_choices",
    "resolve_font_path",
]
