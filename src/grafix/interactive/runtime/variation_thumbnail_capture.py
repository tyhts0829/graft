"""variation thumbnail の capture policy を runtime service へ適合する。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import stat
from typing import TypeAlias

from grafix.core.lifecycle import CleanupErrors
from grafix.core.value_validation import positive_integer_pair
from grafix.core.export_result import ExportResult
from grafix.export.capture import CaptureFrame, CaptureService
from grafix.export.variation_batch import portable_filename_component
from grafix.interactive.parameter_gui.variation_panel import (
    VariationThumbnailArtifact,
)

_THUMBNAIL_LONG_EDGE = 320
_FileIdentity: TypeAlias = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _OwnedVariationThumbnail:
    """今回 publish した PNG と manifest の rollback 所有権。"""

    path: Path
    manifest_path: Path
    _path_identity: _FileIdentity
    _manifest_identity: _FileIdentity

    def discard(self) -> None:
        """外部差し替えを保持しつつ、所有中の artifact family を破棄する。"""

        errors = CleanupErrors()
        errors.attempt(
            lambda: _unlink_if_identity(self.path, self._path_identity),
            "discard variation thumbnail PNG",
        )
        errors.attempt(
            lambda: _unlink_if_identity(
                self.manifest_path,
                self._manifest_identity,
            ),
            "discard variation thumbnail manifest",
        )
        errors.raise_if_any()


def _regular_file_identity(path: Path) -> _FileIdentity:
    """通常ファイルの device/inode identity を取得する。"""

    value = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):
        raise RuntimeError(
            f"variation thumbnail artifact is not a regular file: {path}"
        )
    return int(value.st_dev), int(value.st_ino)


def _unlink_if_identity(path: Path, expected: _FileIdentity) -> None:
    """今回 publish した file identity のままの場合だけ unlink する。"""

    try:
        value = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(value.st_mode):
        return
    if (int(value.st_dev), int(value.st_ino)) != expected:
        return
    path.unlink()


def _new_owned_thumbnail(
    *,
    path: Path,
    manifest_path: Path,
    path_identity: _FileIdentity,
    manifest_identity: _FileIdentity,
) -> VariationThumbnailArtifact:
    """検証済み publish 結果を GUI の最小 ownership contract へ変換する。"""

    return _OwnedVariationThumbnail(
        path=path,
        manifest_path=manifest_path,
        _path_identity=path_identity,
        _manifest_identity=manifest_identity,
    )


def _owned_thumbnail(result: ExportResult) -> VariationThumbnailArtifact:
    """CaptureService の exact 出力を rollback 可能な owner にする。"""

    if type(result) is not ExportResult:
        raise TypeError("thumbnail capture must return an ExportResult")
    path = result.path
    manifest_path = result.manifest_path
    if path == manifest_path:
        primary_error = ValueError(
            "thumbnail path and manifest path must be distinct"
        )
        cleanup = CleanupErrors(initial_error=primary_error)
        try:
            shared_identity = _regular_file_identity(path)
        except BaseException as identity_error:
            # 所有確認できない path は削除せず、contract error を primary に保つ。
            cleanup.record(
                identity_error,
                "acquire same-path thumbnail identity",
            )
        else:
            cleanup.attempt(
                lambda: _unlink_if_identity(path, shared_identity),
                "rollback same-path thumbnail artifact",
            )
        cleanup.raise_if_any()
        raise AssertionError("unreachable")

    identity_errors = CleanupErrors()
    path_identity: _FileIdentity | None
    manifest_identity: _FileIdentity | None
    try:
        path_identity = _regular_file_identity(path)
    except BaseException as error:
        path_identity = None
        identity_errors.record(error, "acquire variation thumbnail PNG identity")
    try:
        manifest_identity = _regular_file_identity(manifest_path)
    except BaseException as error:
        manifest_identity = None
        identity_errors.record(
            error,
            "acquire variation thumbnail manifest identity",
        )

    if path_identity is None or manifest_identity is None:
        if path_identity is not None:
            identity_errors.attempt(
                lambda: _unlink_if_identity(path, path_identity),
                "rollback thumbnail PNG after identity acquisition failure",
            )
        if manifest_identity is not None:
            identity_errors.attempt(
                lambda: _unlink_if_identity(manifest_path, manifest_identity),
                "rollback thumbnail manifest after identity acquisition failure",
            )
        identity_errors.raise_if_any()
        raise AssertionError("unreachable")

    try:
        return _new_owned_thumbnail(
            path=path,
            manifest_path=manifest_path,
            path_identity=path_identity,
            manifest_identity=manifest_identity,
        )
    except BaseException as error:
        cleanup = CleanupErrors(initial_error=error)
        cleanup.attempt(
            lambda: _unlink_if_identity(path, path_identity),
            "rollback thumbnail PNG after owner construction failure",
        )
        cleanup.attempt(
            lambda: _unlink_if_identity(manifest_path, manifest_identity),
            "rollback thumbnail manifest after owner construction failure",
        )
        cleanup.raise_if_any()
        raise AssertionError("unreachable")


def variation_thumbnail_output_path(base_path: Path, name: str) -> Path:
    """portable な variation 名を付けた PNG 保存先を返す。"""

    if not isinstance(base_path, Path):
        raise TypeError("base_path は Path である必要があります")
    component = portable_filename_component(name)
    return base_path.with_name(
        f"{base_path.stem}_{component}{base_path.suffix}"
    )


def variation_thumbnail_size(canvas_size: tuple[int, int]) -> tuple[int, int]:
    """canvas 比率を保つ長辺320pxの thumbnail 寸法を返す。"""

    width, height = positive_integer_pair(canvas_size, name="canvas_size")
    scale = _THUMBNAIL_LONG_EDGE / float(max(width, height))
    return (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )


def make_variation_thumbnail_capture(
    capture_service: CaptureService,
    *,
    frame_provider: Callable[[], CaptureFrame | None],
    base_path: Path,
    canvas_size: tuple[int, int],
) -> Callable[[str], VariationThumbnailArtifact]:
    """live frame を rollback ownership 付き no-clobber PNG として保存する。"""

    if not callable(frame_provider):
        raise TypeError("frame_provider は callable である必要があります")
    output_size = variation_thumbnail_size(canvas_size)

    def capture(name: str) -> VariationThumbnailArtifact:
        frame = frame_provider()
        if frame is None:
            raise RuntimeError("No rendered frame is available for a thumbnail.")
        result = capture_service.export(
            frame,
            variation_thumbnail_output_path(base_path, name),
            overwrite=False,
            output_size=output_size,
        )
        return _owned_thumbnail(result)

    return capture


__all__ = [
    "make_variation_thumbnail_capture",
    "variation_thumbnail_output_path",
    "variation_thumbnail_size",
]
