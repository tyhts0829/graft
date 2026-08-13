"""
Purpose:
    variation一式を、個別成果物と索引metadataを含む一つのdirectory世代として公開する。
Use when:
    batch capture、contact sheet、portable命名、directory単位の公開・rollbackを変更するとき。
Constraints:
    - encode中はprivate workspaceだけを変更し、完成後にdirectory世代として公開する。
    - manifest内のpathは公開先へrelocateし、workspace pathを永続metadataへ漏らさない。
    - 個別item失敗は結果へ記録し、世代全体の構造・公開失敗とは区別する。
    - no-clobber衝突は再encodeせず別世代名で再試行し、overwrite失敗時は旧世代を復元する。
Side effects:
    temporary directory、画像、JSON/CSV/HTMLを作成し、filesystem上の世代を置換し得る。
"""

from __future__ import annotations

import errno
import ctypes
import json
import math
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
from types import TracebackType
from typing import Literal

from grafix.core.export_format import ExportFormat
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
    positive_integer_pair,
)
from grafix.file_io import atomic_write_text, atomic_write_text_no_clobber

VariationRenderStatus = Literal["success", "failed"]

_FILENAME_COMPONENT_MAX_LENGTH = 64
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004


def _path(value: object, *, name: str) -> Path:
    """暗黙 Path 化を行わず Path instance を返す。"""

    if not isinstance(value, Path):
        raise TypeError(f"{name} は Path である必要があります")
    return value


def _optional_path(value: object, *, name: str) -> Path | None:
    """None または Path instance だけを受ける。"""

    return None if value is None else _path(value, name=name)


def _optional_exact_string(value: object, *, name: str) -> str | None:
    """None または exact str だけを受ける。"""

    return None if value is None else exact_string(value, name=name)


@dataclass(frozen=True, slots=True, kw_only=True)
class VariationRenderResult:
    """1 named variation の capture 結果。"""

    variation_name: str
    seed: int | None
    t: float
    status: VariationRenderStatus
    thumbnail_path: Path | None = None
    manifest_path: Path | None = None
    error_type: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        name = exact_string(self.variation_name, name="variation_name")
        if not name.strip():
            raise ValueError("variation_name は空白だけの名前にできません")
        seed = None if self.seed is None else exact_integer(self.seed, name="seed")
        render_t = finite_real(self.t, name="t")
        status = exact_string_choice(
            self.status,
            name="status",
            choices=("success", "failed"),
        )
        thumbnail_path = _optional_path(self.thumbnail_path, name="thumbnail_path")
        manifest_path = _optional_path(self.manifest_path, name="manifest_path")
        error_type = _optional_exact_string(self.error_type, name="error_type")
        error_message = _optional_exact_string(self.error_message, name="error_message")
        if status == "success" and thumbnail_path is None:
            raise ValueError("success result には thumbnail_path が必要です")
        if status == "failed" and error_type is None:
            raise ValueError("failed result には error_type が必要です")

        object.__setattr__(self, "variation_name", name)
        object.__setattr__(self, "seed", seed)
        object.__setattr__(self, "t", render_t)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "thumbnail_path", thumbnail_path)
        object.__setattr__(self, "manifest_path", manifest_path)
        object.__setattr__(self, "error_type", error_type)
        object.__setattr__(self, "error_message", error_message)

    @property
    def succeeded(self) -> bool:
        """capture が成功していれば True。"""

        return self.status == "success"

    def as_dict(self, *, relative_to: Path | None = None) -> dict[str, object]:
        """partial failure summary 用の JSON 互換値を返す。"""

        return {
            "variation_name": self.variation_name,
            "seed": self.seed,
            "t": self.t,
            "status": self.status,
            "thumbnail_path": _summary_path(self.thumbnail_path, relative_to),
            "manifest_path": _summary_path(self.manifest_path, relative_to),
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class VariationBatchArtifacts:
    """API callback が batch workspace に生成した item artifact 群。"""

    items: tuple[VariationRenderResult, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not all(
            isinstance(item, VariationRenderResult) for item in self.items
        ):
            raise TypeError("items は VariationRenderResult の tuple である必要があります")


@dataclass(frozen=True, slots=True, kw_only=True)
class VariationBatchResult:
    """Named variation batch 全体の immutable summary。"""

    output_directory: Path
    items: tuple[VariationRenderResult, ...]
    contact_sheet_path: Path
    summary_path: Path

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not all(
            isinstance(item, VariationRenderResult) for item in self.items
        ):
            raise TypeError("items は VariationRenderResult の tuple である必要があります")
        _path(self.output_directory, name="output_directory")
        _path(self.contact_sheet_path, name="contact_sheet_path")
        _path(self.summary_path, name="summary_path")

    @property
    def success_count(self) -> int:
        """成功 variation 数を返す。"""

        return sum(item.succeeded for item in self.items)

    @property
    def failure_count(self) -> int:
        """失敗 variation 数を返す。"""

        return len(self.items) - self.success_count

    @property
    def ok(self) -> bool:
        """全 variation が成功していれば True。"""

        return self.failure_count == 0

    def as_dict(self) -> dict[str, object]:
        """保存用 structured summary を返す。"""

        directory = self.output_directory
        return {
            "schema": "grafix.variation-batch.v1",
            "output_directory": str(directory),
            "contact_sheet_path": _summary_path(self.contact_sheet_path, directory),
            "summary_path": _summary_path(self.summary_path, directory),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "items": [item.as_dict(relative_to=directory) for item in self.items],
        }


def portable_filename_component(value: str) -> str:
    """variation 名を portable な filename component へ正規化する。

    Unicode の英数・文字、underscore、dot、hyphen を保持し、それ以外の連続を
    underscore 一つへまとめる。前後の区切り文字を除き、最大64文字に制限する。
    有効な文字が残らない場合は ``"variation"`` を返す。
    """

    name = exact_string(value, name="variation name")
    component = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("_.-")
    component = component[:_FILENAME_COMPONENT_MAX_LENGTH].rstrip("_.-")
    return component or "variation"


def variation_thumbnail_name(
    *,
    index: int,
    variation_name: str,
    seed: int | None,
    image_format: ExportFormat,
) -> str:
    """Batch 内で一意な variation thumbnail 名を返す。"""

    item_index = exact_integer(index, name="index", minimum=1)
    item_seed = None if seed is None else exact_integer(seed, name="seed")
    if not isinstance(image_format, ExportFormat):
        raise TypeError("image_format は ExportFormat である必要があります")
    if image_format not in {ExportFormat.PNG, ExportFormat.SVG}:
        raise ValueError("image_format は PNG または SVG である必要があります")
    seed_component = "none" if item_seed is None else str(item_seed)
    return (
        f"{item_index:03d}_{portable_filename_component(variation_name)}_"
        f"seed-{seed_component}{image_format.suffix}"
    )


@dataclass(frozen=True, slots=True)
class _CaptureManifestSource:
    """再試行ごとに同じ staging manifest から relocation する入力。"""

    artifact_path: Path
    manifest_path: Path
    payload_text: str


@dataclass(slots=True)
class _VariationBatchWorkspace:
    """一つの batch staging directory の lifetime owner。"""

    directory: Path
    final_base: Path
    overwrite: bool
    _published: bool = False

    @classmethod
    def create(
        cls,
        output_root: Path,
        *,
        batch_name: str,
        overwrite: bool,
    ) -> _VariationBatchWorkspace:
        """Public root と sibling private staging directory を準備する。"""

        root = _path(output_root, name="output_root").expanduser()
        name = exact_string(batch_name, name="batch_name")
        if not name.strip() or name in {".", ".."} or Path(name).name != name:
            raise ValueError(
                "batch_name は path separator を含まない名前で指定してください"
            )
        replace_existing = exact_bool(overwrite, name="overwrite")
        root.mkdir(parents=True, exist_ok=True)
        final_base = root / name
        if replace_existing and os.path.lexists(final_base):
            if final_base.is_symlink() or not final_base.is_dir():
                raise FileExistsError(
                    "overwrite 対象の batch generation が directory ではありません: "
                    f"{final_base}"
                )
        staging_name = portable_filename_component(name)
        directory = Path(
            tempfile.mkdtemp(prefix=f".{staging_name}.batch-", dir=root)
        )
        return cls(
            directory=directory,
            final_base=final_base,
            overwrite=replace_existing,
        )

    def mark_published(self) -> None:
        """Staging directory が final path へ移動済みであることを記録する。"""

        self._published = True

    def close(self) -> None:
        """未公開 staging directory を best-effort で破棄する。"""

        if self._published:
            return
        shutil.rmtree(self.directory, ignore_errors=True)

    def __enter__(self) -> _VariationBatchWorkspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def export_variation_batch(
    output_root: Path,
    *,
    batch_name: str,
    overwrite: bool,
    thumbnail_size: tuple[int, int],
    columns: int | None,
    render_items: Callable[[Path], VariationBatchArtifacts],
) -> VariationBatchResult:
    """Callback が生成する batch artifacts を一つの generation として公開する。

    ``render_items`` には private working directory を一度だけ渡す。callback 成功後に
    contact sheet と summary を encode し、manifest path を final generation へ
    relocation してから directory を公開する。例外時は staging を破棄し、overwrite
    publish に失敗した場合は以前の generation を復元する。
    """

    root = _path(output_root, name="output_root")
    name = exact_string(batch_name, name="batch_name")
    replace_existing = exact_bool(overwrite, name="overwrite")
    size = positive_integer_pair(thumbnail_size, name="thumbnail_size")
    column_count = (
        None
        if columns is None
        else exact_integer(columns, name="columns", minimum=1)
    )
    if not callable(render_items):
        raise TypeError("render_items は callable である必要があります")

    with _VariationBatchWorkspace.create(
        root,
        batch_name=name,
        overwrite=replace_existing,
    ) as workspace:
        artifacts = render_items(workspace.directory)
        if not isinstance(artifacts, VariationBatchArtifacts):
            raise TypeError(
                "render_items は VariationBatchArtifacts を返す必要があります"
            )
        items = artifacts.items
        atomic_write_text_no_clobber(
            workspace.directory / "contact-sheet.svg",
            _contact_sheet_svg(
                items,
                output_directory=workspace.directory,
                thumbnail_size=size,
                columns=column_count,
            ),
        )
        manifest_sources = _capture_manifest_sources(
            items,
            source=workspace.directory,
        )

        if replace_existing:
            result = _encode_candidate(
                workspace,
                items=items,
                manifest_sources=manifest_sources,
                destination=workspace.final_base,
            )
            _publish_overwrite(workspace)
            return result

        generation_index = 0
        while True:
            destination, generation_index = _next_generation_path(
                workspace.final_base,
                start=generation_index,
            )
            result = _encode_candidate(
                workspace,
                items=items,
                manifest_sources=manifest_sources,
                destination=destination,
            )
            try:
                _publish_no_clobber(workspace.directory, destination)
            except FileExistsError:
                generation_index += 1
                continue
            workspace.mark_published()
            return result


def _next_generation_path(base: Path, *, start: int) -> tuple[Path, int]:
    """現在空いている no-clobber generation 候補と index を返す。"""

    index = start
    while True:
        candidate = base if index == 0 else base.with_name(f"{base.name}_{index:03d}")
        if not os.path.lexists(candidate):
            return candidate, index
        index += 1


def _encode_candidate(
    workspace: _VariationBatchWorkspace,
    *,
    items: tuple[VariationRenderResult, ...],
    manifest_sources: tuple[_CaptureManifestSource, ...],
    destination: Path,
) -> VariationBatchResult:
    """候補 final path に対応する manifest と summary を staging へ encode する。"""

    _write_relocated_capture_manifests(
        manifest_sources,
        source=workspace.directory,
        destination=destination,
    )
    final_items = _relocate_items(
        items,
        source=workspace.directory,
        destination=destination,
    )
    result = VariationBatchResult(
        output_directory=destination,
        items=final_items,
        contact_sheet_path=destination / "contact-sheet.svg",
        summary_path=destination / "summary.json",
    )
    atomic_write_text(
        workspace.directory / "summary.json",
        json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
    )
    return result


def _publish_no_clobber(source: Path, destination: Path) -> None:
    """Staging directory を既存 generation を上書きせず公開する。"""

    try:
        _atomic_rename_no_clobber(source, destination)
    except OSError as exc:
        if exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(destination) from exc
        raise


def _atomic_rename_no_clobber(source: Path, destination: Path) -> None:
    """同一 filesystem 上の directory を既存 path へ置換せず rename する。"""

    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)

    if sys.platform == "darwin":
        renamex = libc.renamex_np
        renamex.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex.restype = ctypes.c_int
        result = renamex(source_bytes, destination_bytes, _RENAME_EXCL)
    elif sys.platform.startswith("linux"):
        try:
            renameat2 = libc.renameat2
        except AttributeError as exc:
            raise OSError(
                errno.ENOTSUP,
                "atomic no-clobber directory rename is unavailable",
                destination,
            ) from exc
        renameat2.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        renameat2.restype = ctypes.c_int
        result = renameat2(
            _AT_FDCWD,
            source_bytes,
            _AT_FDCWD,
            destination_bytes,
            _RENAME_NOREPLACE,
        )
    elif os.name == "nt":
        os.rename(source, destination)
        return
    else:
        raise OSError(
            errno.ENOTSUP,
            "atomic no-clobber directory rename is unavailable",
            destination,
        )

    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


def _publish_overwrite(workspace: _VariationBatchWorkspace) -> None:
    """完成済み staging を公開し、失敗時は旧 generation を復元する。"""

    final = workspace.final_base
    if os.path.lexists(final) and (final.is_symlink() or not final.is_dir()):
        raise FileExistsError(
            "overwrite 対象の batch generation が directory ではありません: "
            f"{final}"
        )

    backup: Path | None = None
    if os.path.lexists(final):
        backup = Path(
            tempfile.mkdtemp(
                prefix=f".{portable_filename_component(final.name)}.backup-",
                dir=final.parent,
            )
        )
        backup.rmdir()
        os.replace(final, backup)
    try:
        os.replace(workspace.directory, final)
    except BaseException as publish_error:
        if backup is not None and os.path.lexists(backup):
            try:
                if not os.path.lexists(final):
                    os.replace(backup, final)
            except BaseException as rollback_error:
                publish_error.add_note(
                    "variation batch rollback に失敗しました: "
                    f"{type(rollback_error).__name__}: {rollback_error}"
                )
        raise

    workspace.mark_published()
    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError:
            # 新 generation は公開済み。private backup cleanup の失敗を
            # publish failure として誤報しない。
            pass


def _relocate_items(
    items: tuple[VariationRenderResult, ...],
    *,
    source: Path,
    destination: Path,
) -> tuple[VariationRenderResult, ...]:
    """Staging 内の成果物 path を公開後 generation path へ写像する。"""

    return tuple(
        replace(
            item,
            thumbnail_path=_relocate_path(item.thumbnail_path, source, destination),
            manifest_path=_relocate_path(item.manifest_path, source, destination),
        )
        for item in items
    )


def _relocate_path(
    path: Path | None,
    source: Path,
    destination: Path,
) -> Path | None:
    if path is None:
        return None
    try:
        relative = path.relative_to(source)
    except ValueError:
        return path
    return destination / relative


def _capture_manifest_sources(
    items: tuple[VariationRenderResult, ...],
    *,
    source: Path,
) -> tuple[_CaptureManifestSource, ...]:
    """Relocation 対象 manifest の元 payload を retry 前に固定する。"""

    sources: list[_CaptureManifestSource] = []
    for item in items:
        artifact = item.thumbnail_path
        manifest = item.manifest_path
        if artifact is None or manifest is None:
            continue
        try:
            artifact.relative_to(source)
            manifest.relative_to(source)
        except ValueError:
            continue
        payload_text = manifest.read_text(encoding="utf-8")
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError(f"capture manifest が object ではありません: {manifest}")
        sources.append(
            _CaptureManifestSource(
                artifact_path=artifact,
                manifest_path=manifest,
                payload_text=payload_text,
            )
        )
    return tuple(sources)


def _write_relocated_capture_manifests(
    manifests: tuple[_CaptureManifestSource, ...],
    *,
    source: Path,
    destination: Path,
) -> None:
    """Staging manifest の artifact path を候補 public path へ書き換える。"""

    for manifest in manifests:
        payload = json.loads(manifest.payload_text)
        if not isinstance(payload, dict):
            raise ValueError(
                f"capture manifest が object ではありません: {manifest.manifest_path}"
            )
        public_artifact = destination / manifest.artifact_path.relative_to(source)
        output = payload.get("output")
        if isinstance(output, dict):
            output_paths = output.get("artifact_paths")
            if isinstance(output_paths, list):
                staged_path = str(manifest.artifact_path)
                output["artifact_paths"] = [
                    str(public_artifact) if value == staged_path else value
                    for value in output_paths
                ]
        atomic_write_text(
            manifest.manifest_path,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )


def _summary_path(path: Path | None, relative_to: Path | None) -> str | None:
    canonical_path = _optional_path(path, name="path")
    canonical_relative_to = _optional_path(relative_to, name="relative_to")
    if canonical_path is None:
        return None
    if canonical_relative_to is None:
        return str(canonical_path)
    return Path(os.path.relpath(canonical_path, canonical_relative_to)).as_posix()


def _contact_sheet_svg(
    items: tuple[VariationRenderResult, ...],
    *,
    output_directory: Path,
    thumbnail_size: tuple[int, int],
    columns: int | None,
) -> str:
    count = max(1, len(items))
    column_count = (
        min(4, max(1, math.ceil(math.sqrt(count))))
        if columns is None
        else min(columns, count)
    )
    row_count = math.ceil(count / column_count)
    thumb_w, thumb_h = thumbnail_size
    padding = 16
    label_height = 58
    cell_w = thumb_w + 2 * padding
    cell_h = thumb_h + label_height + 2 * padding
    sheet_w = cell_w * column_count
    sheet_h = cell_h * row_count
    body: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{sheet_w}" '
            f'height="{sheet_h}" viewBox="0 0 {sheet_w} {sheet_h}">'
        ),
        '<rect width="100%" height="100%" fill="#E8E8E8"/>',
        '<g font-family="-apple-system, BlinkMacSystemFont, sans-serif">',
    ]
    for index, item in enumerate(items):
        column = index % column_count
        row = index // column_count
        x = column * cell_w + padding
        y = row * cell_h + padding
        body.append(
            f'<rect x="{x}" y="{y}" width="{thumb_w}" height="{thumb_h}" '
            'rx="4" fill="#FFFFFF"/>'
        )
        if item.thumbnail_path is not None:
            href = escape(
                _summary_path(item.thumbnail_path, output_directory) or "",
                quote=True,
            )
            body.append(
                f'<image x="{x}" y="{y}" width="{thumb_w}" height="{thumb_h}" '
                f'href="{href}" preserveAspectRatio="xMidYMid meet"/>'
            )
        else:
            body.append(
                f'<text x="{x + 12}" y="{y + thumb_h // 2}" '
                'font-size="14" fill="#B42318">Render failed</text>'
            )
        label_y = y + thumb_h + 24
        seed_text = "—" if item.seed is None else str(item.seed)
        body.append(
            f'<text x="{x}" y="{label_y}" font-size="15" font-weight="600" '
            f'fill="#171717">{escape(item.variation_name)}</text>'
        )
        body.append(
            f'<text x="{x}" y="{label_y + 22}" font-size="12" fill="#555555">'
            f'seed {escape(seed_text)}</text>'
        )
        if item.error_message:
            message = item.error_message.replace("\n", " ")[:80]
            body.append(
                f'<title>{escape(item.error_type or "Error")}: {escape(message)}</title>'
            )
    body.extend(("</g>", "</svg>", ""))
    return "\n".join(body)


__all__ = [
    "VariationBatchArtifacts",
    "VariationBatchResult",
    "VariationRenderResult",
    "VariationRenderStatus",
    "export_variation_batch",
    "portable_filename_component",
    "variation_thumbnail_name",
]
