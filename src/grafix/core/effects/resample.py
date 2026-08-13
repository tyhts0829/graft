"""
Purpose:
    polylineをXYZ弧長に沿ったほぼ等間隔の頂点列へ再標本化する。
Use when:
    smoothingではなく標本密度だけを正規化する、またはopen/closed判定を変更する場合。
Constraints:
    - open線の両端を保ち、closed線は末尾を先頭のexact copyとして閉じる。
    - stepは目標間隔であり、open線の最終区間がstep未満になることを許す。
    - 出力頂点数をplanしてbudget検査してから一度だけ確保し、step 0とexact copyは入力を再利用する。
"""

from __future__ import annotations

import numpy as np

from grafix.core.operation_authoring import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple
from grafix.core.resource_budget import current_resource_budget, ensure_geometry_output

from grafix.core.geometry_kernels.resample import (
    RESAMPLE_CLOSED_DISTANCE_EPS,
    ResampleLinePlan,
    ResamplePlan,
    resample_closed_matches_source,
    resample_open_matches_source,
    resample_polylines,
)

_CLOSED_CHOICES = ("auto", "open", "closed")

resample_meta = {
    "step": ParamMeta(
        kind="float",
        ui_min=0.01,
        ui_max=20.0,
        description="再標本化後の隣接頂点間で目標とする弧長間隔。",
    ),
    "closed": ParamMeta(
        kind="choice",
        choices=_CLOSED_CHOICES,
        description="開曲線、閉曲線、端点距離による自動判定から再標本化方式を選ぶ。",
    ),
}


def _line_plan_is_exact_copy(
    coords: np.ndarray,
    line: ResampleLinePlan,
    *,
    step: float,
) -> bool:
    """line plan の出力が入力のbyte列と一致するならTrueを返す。"""

    input_count = int(line.input_stop - line.input_start)
    output_count = int(line.output_stop - line.output_start)
    if input_count != output_count:
        return False

    # 0/1 点は copy mode、2 点の open 出力は両端だけなので byte 単位で同じになる。
    if input_count <= 2 and not line.closed:
        return True

    vertices = coords[line.input_start : line.input_stop]
    if input_count == 0:
        return True

    # 全長 0 の線は、open/closed のどちらでも元と同じ座標列になる。
    if bool(np.all(vertices == vertices[0])):
        if not line.closed:
            return True
        return vertices[0].tobytes() == vertices[-1].tobytes()

    if not line.closed:
        return resample_open_matches_source(vertices, step=step)

    # 入力自身がbyte単位で閉じていない場合、末尾を先頭へ揃える処理は copy ではない。
    if (
        line.sample_stop != line.input_stop - 1
        or vertices[0].tobytes() != vertices[-1].tobytes()
    ):
        return False
    return resample_closed_matches_source(vertices[:-1], step=step)


@effect(meta=resample_meta)
def resample(
    g: GeomTuple,
    *,
    step: float = 0.5,
    closed: str = "auto",
) -> GeomTuple:
    """ポリライン列を XYZ 弧長に沿って再標本化する。

    ``step`` は厳密な分割長ではなく目標間隔である。開曲線では両端を維持するため、
    末尾に ``step`` 未満の区間が残ることがある。閉曲線では末尾を先頭の厳密な
    コピーにして closure を表す。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        再標本化する実体ジオメトリ（coords, offsets）。
    step : float, default 0.5
        隣接する出力頂点間で目標とする XYZ 弧長。
        0 なら入力をそのまま返す。
    closed : {"auto", "open", "closed"}, default "auto"
        ``"open"`` は各線を開曲線として扱い、``"closed"`` は 3 点以上の線を
        閉曲線として扱う。``"auto"`` は端点距離が 0.01 以下なら閉曲線とみなす。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        再標本化後の実体ジオメトリ（coords, offsets）。

    Raises
    ------
    ValueError
        `step` が負の場合。
    """

    if step < 0.0:
        raise ValueError("resample の step は 0 以上である必要がある")

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    if step == 0.0:
        return coords, offsets

    line_count = max(0, int(offsets.size) - 1)
    if line_count == 0:
        return coords, offsets

    budget = current_resource_budget()
    plan = ResamplePlan.from_geometry(
        coords,
        offsets,
        step=step,
        closed=closed,
        max_vertices=int(budget.max_output_vertices),
        closed_distance=RESAMPLE_CLOSED_DISTANCE_EPS,
    )
    if all(
        _line_plan_is_exact_copy(coords, line, step=plan.step)
        for line in plan.lines
    ):
        return coords, offsets

    ensure_geometry_output(
        "resample",
        vertices=plan.total_vertices,
        lines=line_count,
        hint="step を大きくすると出力頂点数を減らせます",
    )

    return resample_polylines(coords, plan)


__all__ = ["resample", "resample_meta"]
