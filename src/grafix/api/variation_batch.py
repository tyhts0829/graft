"""Named variation を headless batch capture する公開 composition API。"""

from __future__ import annotations

from pathlib import Path

from grafix.api.render import ExportFormat, RenderSession
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.variations import Variation, list_variations
from grafix.core.preview_quality import preview_quality_context
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string,
    finite_real,
    positive_integer_pair,
)
from grafix.export.capture import CaptureService
from grafix.export.variation_batch import (
    VariationBatchArtifacts,
    VariationBatchResult,
    VariationRenderResult,
    VariationRenderStatus,
    export_variation_batch,
    variation_thumbnail_name,
)


def _path_input(value: object, *, name: str) -> Path:
    """公開 path 入力の宣言型である exact str または Path だけを受ける。"""

    if type(value) is str:
        return Path(value)
    if isinstance(value, Path):
        return value
    raise TypeError(f"{name} は str または Path である必要があります")


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
    replace_existing = exact_bool(overwrite, name="overwrite")
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
    name = _batch_name(batch_name)

    def render_items(output_directory: Path) -> VariationBatchArtifacts:
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
                    requested_thumbnail = output_directory / variation_thumbnail_name(
                        index=index,
                        variation_name=variation.name,
                        seed=variation.seed,
                        image_format=image_format,
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
        return VariationBatchArtifacts(items=tuple(items))

    return export_variation_batch(
        output_root_path,
        batch_name=name,
        overwrite=replace_existing,
        thumbnail_size=image_size,
        columns=column_count,
        render_items=render_items,
    )


def _positive_size(value: tuple[int, int]) -> tuple[int, int]:
    if type(value) is not tuple:
        raise TypeError("thumbnail_size は2要素の tuple である必要があります")
    return positive_integer_pair(value, name="thumbnail_size")


def _batch_name(value: object) -> str:
    """Batch directory に使える一つの exact path component を返す。"""

    name = exact_string(value, name="batch_name")
    if not name.strip() or name in {".", ".."} or Path(name).name != name:
        raise ValueError("batch_name は path separator を含まない名前で指定してください")
    return name


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


__all__ = [
    "VariationBatchResult",
    "VariationRenderResult",
    "VariationRenderStatus",
    "render_variation_batch",
]
