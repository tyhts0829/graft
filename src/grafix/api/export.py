"""render 済み ``Frame`` を安全に保存する公開関数を提供する。"""

from __future__ import annotations

from pathlib import Path

from grafix.api.render import ExportFormat, ExportResult, Frame
from grafix.export.capture import CaptureService
from grafix.export.image import png_output_size


def save(
    frame: Frame,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> ExportResult:
    """``Frame`` を path suffix の形式で保存し、実保存結果を返す。

    Parameters
    ----------
    frame : Frame
        :func:`grafix.render` または :class:`grafix.RenderSession` が返したフレーム。
    path : str or Path
        ``.svg``、``.png``、``.gcode`` のいずれかで終わる要求 path。
    overwrite : bool, optional
        ``False`` では既存成果物を避けて連番 path に保存する。``True`` の場合だけ
        artifact と manifest の既存 generation を置換する。

    Returns
    -------
    ExportResult
        連番付与を含む実 artifact path、形式、manifest path。
    """

    if not isinstance(frame, Frame):
        raise TypeError("frame は Frame である必要があります")
    if type(overwrite) is not bool:
        raise TypeError("overwrite は bool である必要があります")
    artifact_format = ExportFormat.from_path(path)
    config = frame.metadata.effective_config
    output_size = (
        png_output_size(frame.canvas_size, scale=config.png_scale)
        if artifact_format is ExportFormat.PNG
        else None
    )
    return CaptureService().export(
        frame,
        path,
        overwrite=overwrite,
        output_size=output_size,
        gcode_params=(
            config.gcode if artifact_format is ExportFormat.GCODE else None
        ),
    )


__all__ = ["save"]
