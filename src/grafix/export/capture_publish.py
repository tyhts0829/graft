"""Capture artifact と manifest の atomic publish infrastructure。"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final

from grafix.core.capture_manifest import CaptureManifest
from grafix.core.lifecycle import CleanupErrors
from grafix.core.value_validation import exact_bool

_UTF8: Final = "utf-8"
_FileIdentity = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _OwnedCaptureGeneration:
    """一括公開した成果物群と manifest の削除 capability。"""

    artifact_paths: tuple[Path, ...]
    manifest_path: Path
    _identities: tuple[_FileIdentity, ...]

    @property
    def path(self) -> Path:
        """公開 generation の primary artifact path を返す。"""

        return self.artifact_paths[0]

    def discard(self) -> None:
        """外部差し替えを保持し、今回公開した member だけを削除する。"""

        member_paths = (*self.artifact_paths, self.manifest_path)
        errors = CleanupErrors()
        for path, identity in zip(
            member_paths,
            self._identities,
            strict=True,
        ):
            errors.attempt(
                partial(_unlink_owned_file, path, identity),
                f"discard capture generation member {path}",
            )
        errors.raise_if_any()


def capture_manifest_path_for(artifact_path: str | Path) -> Path:
    """成果物の拡張子も残した sibling manifest path を返す。"""

    artifact = Path(artifact_path)
    if not artifact.name:
        raise ValueError("artifact_path はファイル名を含む必要がある")
    return artifact.with_name(f"{artifact.name}.capture.json")


def _manifest_payload(manifest: CaptureManifest) -> bytes:
    text = json.dumps(
        manifest.as_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode(_UTF8)


def _stage_manifest(*, directory: Path, manifest: CaptureManifest) -> Path:
    """manifest を fsync 済みの private sibling file として作る。"""

    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        dir=directory,
        prefix=".grafix-capture-manifest-",
        suffix=".tmp",
    )
    staged_path = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_manifest_payload(manifest))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        _cleanup_staged_manifest(staged_path)
        raise
    return staged_path


def _cleanup_staged_manifest(path: Path) -> None:
    """Private manifest staging を best-effort で削除する。"""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _regular_file_identity(path: Path) -> _FileIdentity:
    """通常ファイルであることを確認し、rollback 用 identity を返す。"""

    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"staged capture が通常ファイルではありません: {path}")
    stat_result = path.stat(follow_symlinks=False)
    return int(stat_result.st_dev), int(stat_result.st_ino)


def _unlink_if_identity(path: Path, expected: _FileIdentity) -> None:
    """今回公開した inode のままなら unlink する。外部差し替えは保持する。"""

    try:
        stat_result = path.stat(follow_symlinks=False)
        identity = (int(stat_result.st_dev), int(stat_result.st_ino))
        if identity == expected:
            path.unlink()
    except OSError:
        pass


def _unlink_owned_file(path: Path, expected: _FileIdentity) -> None:
    """所有中の通常 file だけを unlink し、I/O error は呼び出し側へ返す。"""

    try:
        stat_result = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(stat_result.st_mode):
        return
    identity = (int(stat_result.st_dev), int(stat_result.st_ino))
    if identity != expected:
        return
    path.unlink()


def _private_backup_path(path: Path) -> Path:
    """overwrite transaction 用の一意な sibling backup path を返す。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".grafix-capture-backup-{path.name}-",
        suffix=".tmp",
    )
    os.close(fd)
    backup = Path(name)
    backup.unlink()
    return backup


def _publish_capture_generation_overwrite(
    *,
    sources: tuple[Path, ...],
    targets: tuple[Path, ...],
    source_identities: tuple[_FileIdentity, ...],
) -> None:
    """既存 generation を退避し、失敗時に元へ戻して置換する。"""

    backups: list[tuple[Path, Path]] = []
    committed: list[tuple[Path, tuple[int, int]]] = []
    directories = tuple(path.parent for path in targets)
    try:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not os.path.lexists(target):
                continue
            if target.is_dir() and not target.is_symlink():
                raise IsADirectoryError(f"capture の公開先が directory です: {target}")
            backup = _private_backup_path(target)
            os.replace(target, backup)
            backups.append((target, backup))

        for source, target, identity in zip(
            sources,
            targets,
            source_identities,
            strict=True,
        ):
            os.link(source, target, follow_symlinks=False)
            committed.append((target, identity))
        _fsync_directories(directories, best_effort=False)
    except BaseException:
        for target, identity in reversed(committed):
            _unlink_if_identity(target, identity)
        for target, backup in reversed(backups):
            # transaction 外から同名 path が作られた場合は上書きしない。通常の
            # rollback では target は空いており、元 generation を atomic に戻せる。
            if not os.path.lexists(target) and os.path.lexists(backup):
                os.replace(backup, target)
        _fsync_directories(directories, best_effort=True)
        raise
    else:
        for _target, backup in backups:
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                # generation は既に公開済み。private backup の後始末失敗を
                # publish failure と誤報して、呼び出し側に再試行させない。
                pass
        _fsync_directories(directories, best_effort=True)


def _fsync_directories(directories: tuple[Path, ...], *, best_effort: bool) -> None:
    """重複を除いた publish directory を同期する。"""

    seen: set[str] = set()
    for directory in directories:
        key = os.path.normcase(os.path.abspath(os.fspath(directory)))
        if key in seen:
            continue
        seen.add(key)
        fd: int | None = None
        try:
            flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
            fd = os.open(directory, flags)
            os.fsync(fd)
        except OSError:
            if not best_effort:
                raise
        finally:
            if fd is not None:
                os.close(fd)


def publish_capture_generation(
    *,
    staged_artifact_paths: tuple[Path, ...],
    artifact_paths: tuple[Path, ...],
    manifest_path: str | Path,
    manifest: CaptureManifest,
    overwrite: bool = False,
) -> _OwnedCaptureGeneration:
    """成果物と manifest を no-clobber generation として公開する。

    全ファイルは完成済み sibling staging から ``os.link`` で排他的に公開する。
    途中で late collision や I/O error が起きた場合は、この呼び出しが公開した inode
    だけを逆順で rollback する。したがって allocation 後に外部 process が作成・
    差し替えたファイルを上書きも削除もしない。

    複数 path を filesystem として完全に同時に見せることはできないが、正常 return
    では成果物と manifest が全て存在し、例外 return では今回分を残さない。
    process crash の回復 journal は別機能として扱う。
    """

    overwrite = exact_bool(overwrite, name="overwrite")
    staged = tuple(Path(path) for path in staged_artifact_paths)
    finals = tuple(Path(path) for path in artifact_paths)
    target_manifest = Path(manifest_path)
    if not staged or len(staged) != len(finals):
        raise ValueError("staged_artifact_paths と artifact_paths は同じ非ゼロ件数が必要です")
    if tuple(manifest.artifact_paths) != finals:
        raise ValueError("manifest.artifact_paths は公開先 artifact_paths と一致する必要があります")
    all_targets = (*finals, target_manifest)
    normalized_targets = {
        os.path.normcase(os.path.abspath(os.fspath(path))) for path in all_targets
    }
    if len(normalized_targets) != len(all_targets):
        raise ValueError("capture generation の公開先 path は全て一意である必要があります")

    # manifest の staging は target と同じ directory に作り、hard-link publish が
    # cross-device にならないようにする。artifact staging も通常は同じ sibling dir。
    staged_manifest = _stage_manifest(
        directory=target_manifest.parent,
        manifest=manifest,
    )
    sources = (*staged, staged_manifest)
    committed: list[tuple[Path, tuple[int, int]]] = []
    target_directories = tuple(path.parent for path in all_targets)
    try:
        source_identities = tuple(_regular_file_identity(path) for path in sources)
        owned_generation = _OwnedCaptureGeneration(
            artifact_paths=finals,
            manifest_path=target_manifest,
            _identities=source_identities,
        )
        # writer が close 済みでも durability を揃えるため、artifact も publish 前に fsync。
        for source in staged:
            with source.open("rb") as stream:
                os.fsync(stream.fileno())

        if overwrite:
            _publish_capture_generation_overwrite(
                sources=sources,
                targets=all_targets,
                source_identities=source_identities,
            )
        else:
            for source, target, identity in zip(
                sources,
                all_targets,
                source_identities,
                strict=True,
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.link(source, target, follow_symlinks=False)
                committed.append((target, identity))
            _fsync_directories(target_directories, best_effort=False)
    except BaseException:
        for target, identity in reversed(committed):
            _unlink_if_identity(target, identity)
        # rollback directory entry も可能な範囲で durability を揃える。元の例外を優先。
        _fsync_directories(target_directories, best_effort=True)
        raise
    finally:
        # generation の成否は既に確定している。private staging の cleanup
        # failure で成功を失敗に変えたり、元の publish error を隠したりしない。
        _cleanup_staged_manifest(staged_manifest)

    return owned_generation


def write_capture_manifest(path: str | Path, manifest: CaptureManifest) -> Path:
    """manifest を上書きせず atomic に公開し、その path を返す。

    既存 path（broken symlink を含む）がある場合は ``FileExistsError`` を送出し、
    その内容には触れない。capture artifact と一括確定する場合は
    :func:`publish_capture_generation` を使う。
    """

    target = Path(path)
    staged = _stage_manifest(directory=target.parent, manifest=manifest)
    identity = _regular_file_identity(staged)
    committed = False
    try:
        os.link(staged, target, follow_symlinks=False)
        committed = True
        _fsync_directories((target.parent,), best_effort=False)
    except BaseException:
        if committed:
            _unlink_if_identity(target, identity)
            _fsync_directories((target.parent,), best_effort=True)
        raise
    finally:
        staged.unlink(missing_ok=True)
    return target


__all__ = [
    "capture_manifest_path_for",
    "publish_capture_generation",
    "write_capture_manifest",
]
