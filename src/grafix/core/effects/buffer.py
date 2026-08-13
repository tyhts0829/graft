"""
Purpose:
    平面へ写したpolylineの周囲から、指定距離に対応するbuffer輪郭を生成する。
Use when:
    線の太らせ・内側輪郭、join形状、複数lineのunion bufferを扱う場合。
Constraints:
    - union時は全lineで一つのcanonical frameを共有し、非union時はlineごとのframeを使う。
    - linear入力には決定的な補助平面を与え、有限なrank 2/3入力はbest-fit planeで扱う。
    - 距離0はno-opとし、keep_originalなしで輪郭を作れない正距離は入力、負距離はemptyへfallbackする。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

import numpy as np

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry  # type: ignore[import-not-found, import-untyped]

from grafix.core.operation_authoring import effect
from grafix.core.realized_geometry import GeomTuple
from grafix.core.parameters.meta import ParamMeta
from grafix.core.geometry_kernels.packed import empty_packed_geometry
from grafix.core.geometry_kernels.planar import (
    canonical_planar_frame,
    close_curve,
)

buffer_meta = {
    "join": ParamMeta(
        kind="choice",
        choices=("mitre", "round", "bevel"),
        description="輪郭をオフセットしたときの角の接続形状を選ぶ。",
    ),
    "distance": ParamMeta(
        kind="float",
        ui_min=-25.0,
        ui_max=25.0,
        description="輪郭を膨張または収縮させる距離で、正なら外側、負なら内側へ動かす。",
    ),
    "quad_segs": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=100,
        description="丸い角や端を近似する四分円あたりの分割数。",
    ),
    "union": ParamMeta(
        kind="bool",
        description="複数の入力ポリラインを統合してから一度に輪郭を生成する。",
    ),
    "keep_original": ParamMeta(
        kind="bool",
        description="生成した輪郭に元のポリラインを加えて出力する。",
    ),
}

_AUTO_CLOSE_THRESHOLD = 1e-3
_QUAD_SEGS_MAX = 256


def _extract_vertices_2d(
    buffered: BaseGeometry,
    *,
    which: str,
) -> list[np.ndarray]:
    """Shapely geometry から輪郭頂点列（Nx2）を抽出して返す。"""
    if buffered.is_empty:
        return []

    # ローカル import（effect 未使用時に shapely import を避ける）
    from shapely.geometry import (  # type: ignore[import-not-found, import-untyped]
        LineString,
        MultiLineString,
        MultiPolygon,
        Polygon,
    )

    out: list[np.ndarray] = []
    if which == "exterior":
        if isinstance(buffered, Polygon):
            out.append(np.asarray(buffered.exterior.coords, dtype=np.float64))
            return out
        if isinstance(buffered, MultiPolygon):
            for poly in buffered.geoms:
                if not poly.is_empty:
                    out.append(np.asarray(poly.exterior.coords, dtype=np.float64))
            return out
        if isinstance(buffered, LineString):
            out.append(np.asarray(buffered.coords, dtype=np.float64))
            return out
        if isinstance(buffered, MultiLineString):
            for line in buffered.geoms:
                out.append(np.asarray(line.coords, dtype=np.float64))
            return out
    elif which == "interior":
        if isinstance(buffered, Polygon):
            for ring in buffered.interiors:
                out.append(np.asarray(ring.coords, dtype=np.float64))
            return out
        if isinstance(buffered, MultiPolygon):
            for poly in buffered.geoms:
                if poly.is_empty:
                    continue
                for ring in poly.interiors:
                    out.append(np.asarray(ring.coords, dtype=np.float64))
            return out
    else:
        raise ValueError(f"unknown which: {which!r}")

    # GeometryCollection 等の可能性は浅く処理する（未知型は黙って捨てる）。
    geoms = getattr(buffered, "geoms", None)
    if geoms is not None:
        for g in geoms:
            out.extend(_extract_vertices_2d(g, which=which))
    return out


@effect(meta=buffer_meta)
def buffer(
    g: GeomTuple,
    *,
    join: str = "round",  # "mitre" | "round" | "bevel"
    quad_segs: int = 12,  # Shapely の quad_segs（1/4 円あたりの分割）
    distance: float = 5.0,
    union: bool = False,
    keep_original: bool = False,
) -> GeomTuple:
    """Shapely の buffer を用いて輪郭を生成する。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力実体ジオメトリ（coords, offsets）。
    join : str, default "round"
        角の処理。`"mitre" | "round" | "bevel"` を指定。
    quad_segs : int, default 12
        円弧近似分割数（Shapely の `quad_segs` 相当）。
    distance : float, default 5.0
        buffer 距離 [mm]。

        - `distance > 0`: 外側輪郭（buffer 結果の exterior）
        - `distance < 0`: 内側輪郭（buffer 結果の holes / interiors）
        - `distance == 0`: no-op
    union : bool, default False
        True のとき、入力内の複数ポリラインを同一平面へ射影して統合し、
        1回の buffer で重なりをまとめた輪郭を返す。
    keep_original : bool, default False
        True のとき buffer 結果に加えて元のポリラインも出力に含める。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        buffer 後の実体ジオメトリ（coords, offsets）。

    Notes
    -----
    - 端点が近い線は自動で閉じる（閾値 `1e-3`）。
    - distance==0 は no-op 扱いとする。
    - rank 1 の直線には world axis から決めた補助平面を使う。
    - rank 2/3 の有限入力は canonical best-fit plane へ射影して処理する。
      `partition` と異なり、平面残差による拒否は行わない。
    """
    if not 1 <= quad_segs <= _QUAD_SEGS_MAX:
        raise ValueError(
            f"buffer: quad_segs は 1 以上 {_QUAD_SEGS_MAX} 以下である必要がある"
        )

    coords, offsets = g
    if coords.shape[0] == 0 or distance == 0.0:
        return coords, offsets
    abs_distance = abs(distance)

    join_style = cast(Literal["mitre", "round", "bevel"], join)

    # ローカル import（effect 未使用時に shapely import を避ける）
    from shapely.geometry import LineString, MultiLineString  # type: ignore[import-not-found]

    out_lines: list[np.ndarray] = []
    if union:
        frame = canonical_planar_frame(
            coords,
            offsets,
            allow_linear=True,
        )

        lines2: list[np.ndarray] = []
        if frame.valid:
            for i in range(int(offsets.size) - 1):
                s = int(offsets[i])
                e = int(offsets[i + 1])
                line3 = coords[s:e]
                if line3.shape[0] < 2:
                    continue
                line3 = close_curve(line3, _AUTO_CLOSE_THRESHOLD)
                lines2.append(frame.project(line3))

        if lines2:
            buffered = MultiLineString(lines2).buffer(  # type: ignore[arg-type]
                abs_distance,
                quad_segs=quad_segs,
                join_style=join_style,
            )
            which = "exterior" if distance > 0.0 else "interior"
            for v2 in _extract_vertices_2d(buffered, which=which):
                if v2.shape[0] < 2:
                    continue
                v3 = frame.lift(v2[:, :2]).astype(np.float32, copy=False)
                out_lines.append(v3)
    else:
        for i in range(int(offsets.size) - 1):
            s = int(offsets[i])
            e = int(offsets[i + 1])
            line3 = coords[s:e]
            if line3.shape[0] < 2:
                continue

            line3 = close_curve(line3, _AUTO_CLOSE_THRESHOLD)
            frame = canonical_planar_frame(line3, allow_linear=True)
            if not frame.valid:
                continue
            line2 = frame.project(line3)

            buffered = LineString(line2).buffer(  # type: ignore[arg-type]
                abs_distance,
                quad_segs=quad_segs,
                join_style=join_style,
            )
            which = "exterior" if distance > 0.0 else "interior"
            for v2 in _extract_vertices_2d(buffered, which=which):
                if v2.shape[0] < 2:
                    continue
                v3 = frame.lift(v2[:, :2]).astype(np.float32, copy=False)
                out_lines.append(v3)

    if keep_original:
        for i in range(int(offsets.size) - 1):
            s = int(offsets[i])
            e = int(offsets[i + 1])
            original = coords[s:e]
            if original.shape[0] > 0:
                out_lines.append(original.astype(np.float32, copy=False))

    if not out_lines:
        return (coords, offsets) if distance > 0.0 else empty_packed_geometry()

    out_coords = np.concatenate(out_lines, axis=0).astype(np.float32, copy=False)
    out_offsets = np.empty((len(out_lines) + 1,), dtype=np.int32)
    out_offsets[0] = 0
    acc = 0
    for i, line in enumerate(out_lines):
        acc += int(line.shape[0])
        out_offsets[i + 1] = acc

    return out_coords, out_offsets
