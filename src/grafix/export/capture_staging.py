"""Capture staging と late-collision retry の共通 lifecycle。"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Generic, TypeVar

from grafix.core.value_validation import exact_integer, exact_string
from grafix.export.capture_publish import capture_manifest_path_for
from grafix.export.output_paths import VersionedPathAllocator

_T = TypeVar("_T")


@dataclass(slots=True)
class CaptureStaging:
    """一つの capture encode が使う private sibling directory。"""

    directory: Path
    work_path: Path
    _closed: bool = False

    @classmethod
    def create(cls, output_path: Path, *, purpose: str) -> CaptureStaging:
        """正式 path と同じ directory に private work path を作る。"""

        if not isinstance(output_path, Path):
            raise TypeError("output_path は Path である必要があります")
        purpose_name = exact_string(purpose, name="purpose")
        if not purpose_name or purpose_name != purpose_name.strip():
            raise ValueError("purpose は前後空白のない非空文字列である必要があります")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        directory = Path(
            tempfile.mkdtemp(
                prefix=f".{output_path.stem}.{purpose_name}-",
                dir=output_path.parent,
            )
        )
        return cls(directory=directory, work_path=directory / output_path.name)

    def close(self) -> None:
        """staging directory を一度だけ best-effort で削除する。"""

        if self._closed:
            return
        self._closed = True
        shutil.rmtree(self.directory, ignore_errors=True)

    def __enter__(self) -> CaptureStaging:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def cleanup_capture_staging(directory: Path) -> None:
    """job-private staging directory を best-effort で削除する。"""

    if not isinstance(directory, Path):
        raise TypeError("directory は Path である必要があります")
    shutil.rmtree(directory, ignore_errors=True)


def capture_staging_work_path(directory: Path, output_path: Path) -> Path:
    """job-private directory を用意し、encode 用 work path を返す。"""

    if not isinstance(directory, Path) or not isinstance(output_path, Path):
        raise TypeError("directory と output_path は Path である必要があります")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / output_path.name


def validate_capture_staged_outputs(
    directory: Path,
    paths: Sequence[str | Path],
) -> tuple[Path, ...]:
    """成果物が指定 staging directory の直下または子孫だけを指すと検証する。"""

    if not isinstance(directory, Path):
        raise TypeError("directory は Path である必要があります")
    root = directory.resolve(strict=False)
    staged_paths = tuple(Path(path) for path in paths)
    for path in staged_paths:
        resolved = path.resolve(strict=False)
        if resolved == root or not resolved.is_relative_to(root):
            raise ValueError(
                "export backend は staging directory 内の path だけを返せます: "
                f"path={path}, staging={directory}"
            )
    return staged_paths


def _paths_available(paths: Sequence[Path]) -> bool:
    return all(not os.path.lexists(path) for path in paths)


def allocate_capture_generation_path(
    allocator: VersionedPathAllocator,
    base_path: Path,
    *,
    artifact_paths_for: Callable[[Path], Sequence[Path]] | None = None,
    candidate_is_occupied: Callable[[Path], bool] | None = None,
) -> Path:
    """artifact family と manifest が全て空いている version path を予約する。"""

    if not isinstance(allocator, VersionedPathAllocator):
        raise TypeError("allocator は VersionedPathAllocator である必要があります")
    if not isinstance(base_path, Path):
        raise TypeError("base_path は Path である必要があります")
    targets_for = (
        (lambda candidate: (candidate,))
        if artifact_paths_for is None
        else artifact_paths_for
    )
    while True:
        candidate = allocator.allocate(base_path)
        if candidate_is_occupied is not None and candidate_is_occupied(candidate):
            continue
        targets = tuple(Path(path) for path in targets_for(candidate))
        if not targets:
            raise ValueError("artifact path family は1件以上必要です")
        if _paths_available((*targets, capture_manifest_path_for(candidate))):
            return candidate


@dataclass(frozen=True, slots=True)
class CapturePublishResult(Generic[_T]):
    """retry が選んだ正式 path と publish backend の結果。"""

    output_path: Path
    value: _T


def publish_with_late_collision_retry(
    *,
    allocator: VersionedPathAllocator,
    base_path: Path,
    publish: Callable[[Path], _T],
    max_retries: int,
    artifact_paths_for: Callable[[Path], Sequence[Path]] | None = None,
    candidate_is_occupied: Callable[[Path], bool] | None = None,
    initial_path: Path | None = None,
) -> CapturePublishResult[_T]:
    """完成済み staging を再 encode せず、別 version へ no-clobber publish する。"""

    retries = exact_integer(max_retries, name="max_retries", minimum=1)
    if not callable(publish):
        raise TypeError("publish は callable である必要があります")
    candidate = initial_path
    last_collision: FileExistsError | None = None
    for _attempt in range(retries):
        if candidate is None:
            candidate = allocate_capture_generation_path(
                allocator,
                base_path,
                artifact_paths_for=artifact_paths_for,
                candidate_is_occupied=candidate_is_occupied,
            )
        try:
            return CapturePublishResult(candidate, publish(candidate))
        except FileExistsError as exc:
            last_collision = exc
            candidate = None
    raise FileExistsError(
        "capture publish が late collision の再試行上限に達しました: "
        f"retries={retries}"
    ) from last_collision


__all__ = [
    "CapturePublishResult",
    "CaptureStaging",
    "allocate_capture_generation_path",
    "capture_staging_work_path",
    "cleanup_capture_staging",
    "publish_with_late_collision_retry",
    "validate_capture_staged_outputs",
]
