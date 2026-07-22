"""Named variation を headless batch capture する小さな公開 API。"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path
from typing import Literal

from grafix.api.render import ExportFormat, RenderSession
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.variations import Variation, list_variations
from grafix.core.preview_quality import preview_quality_context
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string,
    exact_string_choice,
    finite_real,
    positive_integer_pair,
)
from grafix.export.capture import CaptureService

VariationRenderStatus = Literal["success", "failed"]


def _path(value: object, *, name: str) -> Path:
    """暗黙 Path 化を行わず Path instance を返す。"""

    if not isinstance(value, Path):
        raise TypeError(f"{name} は Path である必要があります")
    return value


def _optional_path(value: object, *, name: str) -> Path | None:
    """None または Path instance だけを受ける。"""

    return None if value is None else _path(value, name=name)


def _path_input(value: object, *, name: str) -> Path:
    """公開 path 入力の宣言型である exact str または Path だけを受ける。"""

    if type(value) is str:
        return Path(value)
    if isinstance(value, Path):
        return value
    raise TypeError(f"{name} は str または Path である必要があります")


def _optional_exact_string(value: object, *, name: str) -> str | None:
    """None または exact str だけを受ける。"""

    return None if value is None else exact_string(value, name=name)


@dataclass(frozen=True, slots=True)
class _BatchDirectory:
    """Public generation path と overwrite 用 private staging path。"""

    final: Path
    working: Path
    staged: bool


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
        seed = (
            None
            if self.seed is None
            else exact_integer(self.seed, name="seed")
        )
        render_t = finite_real(self.t, name="t")
        status = exact_string_choice(
            self.status,
            name="status",
            choices=("success", "failed"),
        )
        thumbnail_path = _optional_path(
            self.thumbnail_path,
            name="thumbnail_path",
        )
        manifest_path = _optional_path(
            self.manifest_path,
            name="manifest_path",
        )
        error_type = _optional_exact_string(self.error_type, name="error_type")
        error_message = _optional_exact_string(
            self.error_message,
            name="error_message",
        )
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
            raise TypeError(
                "items は VariationRenderResult の tuple である必要があります"
            )
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
            "contact_sheet_path": _summary_path(
                self.contact_sheet_path,
                directory,
            ),
            "summary_path": _summary_path(self.summary_path, directory),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "items": [item.as_dict(relative_to=directory) for item in self.items],
        }


def render_variation_batch(
    session: RenderSession,
    output_root: str | Path,
    *,
    variation_names: tuple[str, ...] | None = None,
    default_t: float = 0.0,
    thumbnail_format: ExportFormat = ExportFormat.PNG,
    thumbnail_size: tuple[int, int] = (320, 320),
    columns: int | None = None,
    batch_name: str = "variations",
    overwrite: bool = False,
    capture_service: CaptureService | None = None,
) -> VariationBatchResult:
    """RenderSession 内の named variations を順に復元・capture する。

    Parameters
    ----------
    session : RenderSession
        named variations を持つ ``ParamStore`` と render cache を所有する session。
    output_root : str or Path
        batch directory を作る親 directory。
    variation_names : tuple[str, ...] or None, optional
        描画順。None は保存順の全 variation。未知名は partial failure として残す。
    default_t : float, optional
        variation に ``t`` が無い場合の評価時刻。
    thumbnail_format : ExportFormat, optional
        CaptureService へ渡す thumbnail 形式。
    thumbnail_size : tuple[int, int], optional
        PNG thumbnail の出力解像度と、contact sheet 上の表示サイズ。
        SVG は解像度を持たないため表示サイズにだけ使う。
    columns : int or None, optional
        contact sheet 列数。None は件数から決める。
    batch_name : str, optional
        ``output_root`` 内の batch directory 名。
    overwrite : bool, optional
        False は batch directory 自体を連番化する。True の場合だけ既存 generation
        を完成済み staging generation で一括置換する。公開失敗時は旧版へ戻す。
    capture_service : CaptureService or None, optional
        capture backend。省略時は新しい CaptureService を使う。

    Returns
    -------
    VariationBatchResult
        variation 単位の成否、contact sheet、structured summary。

    Notes
    -----
    各 variation の前に batch 呼び出し時の exact store snapshot へ戻して
    variation snapshot を merge する。そのため、前の render で新たに発見した
    parameter も次の variation へ引き継がない。成否にかかわらず終了時は
    revision/runtime/UI state/named variations を含む呼び出し前の状態へ戻す。
    """

    store = session.param_store
    if not isinstance(store, ParamStore):
        raise TypeError("session.param_store は ParamStore である必要があります")
    output_root_path = _path_input(output_root, name="output_root")
    overwrite = exact_bool(overwrite, name="overwrite")
    render_t = finite_real(default_t, name="default_t")
    image_size = _positive_size(thumbnail_size)
    column_count = (
        None
        if columns is None
        else exact_integer(columns, name="columns", minimum=1)
    )
    if not isinstance(thumbnail_format, ExportFormat):
        raise TypeError("thumbnail_format は ExportFormat である必要があります")
    image_format = thumbnail_format
    if image_format not in {ExportFormat.PNG, ExportFormat.SVG}:
        raise ValueError("thumbnail_format は PNG または SVG である必要があります")
    requests = _variation_requests(store, variation_names)
    if not requests:
        raise ValueError("render 対象の named variation がありません")

    batch_directory = _prepare_batch_directory(
        output_root_path,
        batch_name=batch_name,
        overwrite=overwrite,
    )
    try:
        output_directory = batch_directory.working
        service = CaptureService() if capture_service is None else capture_service
        items: list[VariationRenderResult] = []
        for index, (requested_name, variation) in enumerate(requests, start=1):
            # item ごとに batch 呼び出し時の論理状態を退避し、render/export の
            # 成否にかかわらず次 item の前に正確に戻す。
            with store.begin_transient_rollback():
                if variation is None:
                    items.append(
                        VariationRenderResult(
                            variation_name=requested_name,
                            seed=None,
                            t=render_t,
                            status="failed",
                            error_type="KeyError",
                            error_message=f"unknown variation: {requested_name!r}",
                        )
                    )
                    continue

                item_t = render_t if variation.t is None else variation.t
                try:
                    store.apply_adjustment_snapshot(variation.parameter_snapshot)
                    with preview_quality_context("final"):
                        frame = session.render(
                            item_t,
                            provenance_seed=variation.seed,
                        )
                    requested_thumbnail = output_directory / _thumbnail_name(
                        index,
                        variation,
                        image_format,
                    )
                    captured = service.export(
                        frame,
                        requested_thumbnail,
                        overwrite=False,
                        output_size=(
                            image_size if image_format is ExportFormat.PNG else None
                        ),
                    )
                except Exception as exc:
                    items.append(
                        VariationRenderResult(
                            variation_name=variation.name,
                            seed=variation.seed,
                            t=item_t,
                            status="failed",
                            error_type=type(exc).__name__,
                            error_message=str(exc) or type(exc).__name__,
                        )
                    )
                    continue

                items.append(
                    VariationRenderResult(
                        variation_name=variation.name,
                        seed=variation.seed,
                        t=item_t,
                        status="success",
                        thumbnail_path=captured.path,
                        manifest_path=captured.manifest_path,
                    )
                )

        _publish_text(
            output_directory / "contact-sheet.svg",
            _contact_sheet_svg(
                tuple(items),
                output_directory=output_directory,
                thumbnail_size=image_size,
                columns=column_count,
            ),
            overwrite=False,
        )
        _relocate_capture_manifests(
            tuple(items),
            source=output_directory,
            destination=batch_directory.final,
        )
        final_items = _relocate_items(
            tuple(items),
            source=output_directory,
            destination=batch_directory.final,
        )
        result = VariationBatchResult(
            output_directory=batch_directory.final,
            items=final_items,
            contact_sheet_path=batch_directory.final / "contact-sheet.svg",
            summary_path=batch_directory.final / "summary.json",
        )
        _publish_text(
            output_directory / "summary.json",
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            overwrite=False,
        )
        _publish_batch_directory(batch_directory)
        return result
    finally:
        if batch_directory.staged and batch_directory.working.exists():
            shutil.rmtree(batch_directory.working)


def _positive_size(value: tuple[int, int]) -> tuple[int, int]:
    if type(value) is not tuple:
        raise TypeError("thumbnail_size は2要素の tuple である必要があります")
    return positive_integer_pair(value, name="thumbnail_size")


def _variation_requests(
    store: ParamStore,
    names: tuple[str, ...] | None,
) -> tuple[tuple[str, Variation | None], ...]:
    variations = list_variations(store)
    if names is None:
        return tuple((variation.name, variation) for variation in variations)
    if type(names) is not tuple:
        raise TypeError("variation_names は文字列の tuple で指定してください")
    by_name = {variation.name: variation for variation in variations}
    requests: list[tuple[str, Variation | None]] = []
    for name in names:
        name = exact_string(name, name="variation_names の各要素")
        if not name.strip():
            raise ValueError("variation_names に空白だけの名前は指定できません")
        requests.append((name, by_name.get(name)))
    return tuple(requests)


def _prepare_batch_directory(
    output_root: Path,
    *,
    batch_name: str,
    overwrite: bool,
) -> _BatchDirectory:
    name = exact_string(batch_name, name="batch_name")
    if not name.strip() or name in {".", ".."} or Path(name).name != name:
        raise ValueError("batch_name は path separator を含まない名前で指定してください")
    root = output_root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    base = root / name
    if overwrite:
        if os.path.lexists(base) and (base.is_symlink() or not base.is_dir()):
            raise FileExistsError(
                "overwrite 対象の batch generation が directory ではありません: "
                f"{base}"
            )
        working = Path(
            tempfile.mkdtemp(prefix=f".{name}.batch-", dir=root)
        )
        return _BatchDirectory(final=base, working=working, staged=True)

    index = 0
    while True:
        candidate = (
            base
            if index == 0
            else base.with_name(f"{base.name}_{index:03d}")
        )
        try:
            candidate.mkdir(parents=False, exist_ok=False)
        except FileExistsError:
            index += 1
            continue
        return _BatchDirectory(final=candidate, working=candidate, staged=False)


def _relocate_items(
    items: tuple[VariationRenderResult, ...],
    *,
    source: Path,
    destination: Path,
) -> tuple[VariationRenderResult, ...]:
    """Staging 内の成果物 path を公開後 generation path へ写像する。"""

    def relocate(path: Path | None) -> Path | None:
        if path is None:
            return None
        try:
            relative = path.relative_to(source)
        except ValueError:
            return path
        return destination / relative

    return tuple(
        replace(
            item,
            thumbnail_path=relocate(item.thumbnail_path),
            manifest_path=relocate(item.manifest_path),
        )
        for item in items
    )


def _relocate_capture_manifests(
    items: tuple[VariationRenderResult, ...],
    *,
    source: Path,
    destination: Path,
) -> None:
    """Staging 内 manifest の artifact path を公開後 path へ書き換える。"""

    if source == destination:
        return
    for item in items:
        artifact = item.thumbnail_path
        manifest = item.manifest_path
        if artifact is None or manifest is None:
            continue
        try:
            public_artifact = destination / artifact.relative_to(source)
            manifest.relative_to(source)
        except ValueError:
            continue
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"capture manifest が object ではありません: {manifest}")
        old_path = str(artifact)
        new_path = str(public_artifact)
        output = payload.get("output")
        if isinstance(output, dict):
            output_paths = output.get("artifact_paths")
            if isinstance(output_paths, list):
                output["artifact_paths"] = [
                    new_path if value == old_path else value for value in output_paths
                ]
        _publish_text(
            manifest,
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            overwrite=True,
        )


def _publish_batch_directory(batch: _BatchDirectory) -> None:
    """Staging generation を完成後だけ公開し、失敗時は旧版を戻す。"""

    if not batch.staged:
        return

    backup: Path | None = None
    if os.path.lexists(batch.final):
        backup = Path(
            tempfile.mkdtemp(
                prefix=f".{batch.final.name}.backup-",
                dir=batch.final.parent,
            )
        )
        backup.rmdir()
        os.replace(batch.final, backup)
    try:
        os.replace(batch.working, batch.final)
    except BaseException:
        if backup is not None and os.path.lexists(backup):
            os.replace(backup, batch.final)
        raise
    if backup is not None:
        try:
            shutil.rmtree(backup)
        except OSError:
            # 新 generation は既に公開済み。private backup の後始末失敗を
            # publish failure として誤報し、再実行で新 generation を壊さない。
            pass


def _filename_slug(value: str) -> str:
    value = exact_string(value, name="variation name")
    slug = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("_.-")
    return (slug or "variation")[:64].rstrip("_.-") or "variation"


def _thumbnail_name(
    index: int,
    variation: Variation,
    image_format: ExportFormat,
) -> str:
    seed = "none" if variation.seed is None else str(variation.seed)
    return (
        f"{index:03d}_{_filename_slug(variation.name)}_seed-{seed}"
        f"{image_format.suffix}"
    )


def _summary_path(path: Path | None, relative_to: Path | None) -> str | None:
    canonical_path = _optional_path(path, name="path")
    canonical_relative_to = _optional_path(relative_to, name="relative_to")
    if canonical_path is None:
        return None
    if canonical_relative_to is None:
        return str(canonical_path)
    return Path(
        os.path.relpath(canonical_path, canonical_relative_to)
    ).as_posix()


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


def _publish_text(path: Path, text: str, *, overwrite: bool) -> None:
    """完成済み sibling temp を atomic/no-clobber で公開する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "VariationBatchResult",
    "VariationRenderResult",
    "VariationRenderStatus",
    "render_variation_batch",
]
