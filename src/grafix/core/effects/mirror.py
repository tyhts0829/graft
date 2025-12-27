"""入力ポリライン列を対称変換で複製し、ミラー対称パターンを作る effect。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import RealizedGeometry

EPS = 1e-6
INCLUDE_BOUNDARY = True

mirror_meta = {
    "n_mirror": ParamMeta(kind="int", ui_min=1, ui_max=12),
    "cx": ParamMeta(kind="float", ui_min=-100.0, ui_max=100.0),
    "cy": ParamMeta(kind="float", ui_min=-100.0, ui_max=100.0),
    "source_positive_x": ParamMeta(kind="bool"),
    "source_positive_y": ParamMeta(kind="bool"),
    "show_planes": ParamMeta(kind="bool"),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@effect(meta=mirror_meta)
def mirror(
    inputs: Sequence[RealizedGeometry],
    *,
    n_mirror: int = 1,
    cx: float = 0.0,
    cy: float = 0.0,
    source_positive_x: bool = True,
    source_positive_y: bool = True,
    show_planes: bool = False,
) -> RealizedGeometry:
    """XY 平面でのミラー複製を行う。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力の実体ジオメトリ列。通常は 1 要素。
    n_mirror : int, default 1
        1: x=cx による半空間ミラー。2: x=cx と y=cy による象限ミラー。
        3 以上: 中心 (cx,cy) を基準に放射状の 2n 対称（楔を回転 + 反転で複製）。
    cx, cy : float, default 0.0
        対称の中心座標（z は不変）。
    source_positive_x : bool, default True
        n_mirror=1/2 のときの x 側ソース選択。True なら x>=cx 側、False なら x<=cx 側。
    source_positive_y : bool, default True
        n_mirror=2 のときの y 側ソース選択。True なら y>=cy 側、False なら y<=cy 側。
    show_planes : bool, default False
        対称面（または放射状境界）を可視化用ラインとして出力へ追加する。

    Returns
    -------
    RealizedGeometry
        ミラー後の実体ジオメトリ。

    Notes
    -----
    - クリップは線分の半空間交差で行い、重心判定はしない。
    - 境界は内側扱い（include boundary）とし、EPS=1e-6 を使用する。
    """
    if not inputs:
        return _empty_geometry()

    base_geom = inputs[0]
    coords = base_geom.coords
    offsets = base_geom.offsets
    if coords.shape[0] == 0:
        return base_geom

    n = int(n_mirror)
    if n < 1:
        return base_geom

    cx_f = float(cx)
    cy_f = float(cy)
    if not (np.isfinite(cx_f) and np.isfinite(cy_f)):
        return base_geom

    sx = 1 if bool(source_positive_x) else -1
    sy = 1 if bool(source_positive_y) else -1

    out_lines: list[np.ndarray] = []
    src_lines: list[np.ndarray] = []

    if n == 1:
        for li in range(int(offsets.size) - 1):
            v = coords[int(offsets[li]) : int(offsets[li + 1])]
            if v.shape[0] == 0:
                continue
            pieces = _clip_polyline_halfspace(v, axis=0, thresh=cx_f, side=sx)
            for p in pieces:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32, copy=False))

        for p in src_lines:
            out_lines.append(p)
            out_lines.append(_reflect_x(p, cx_f))

    elif n == 2:
        for li in range(int(offsets.size) - 1):
            v = coords[int(offsets[li]) : int(offsets[li + 1])]
            if v.shape[0] == 0:
                continue
            pieces = _clip_polyline_quadrant(v, cx=cx_f, cy=cy_f, sx=sx, sy=sy)
            for p in pieces:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32, copy=False))

        for p in src_lines:
            out_lines.append(p)
            px = _reflect_x(p, cx_f)
            py = _reflect_y(p, cy_f)
            pxy = _reflect_y(px, cy_f)
            out_lines.extend([px, py, pxy])

    else:
        delta = float(np.pi / n)
        step = float(2.0 * np.pi / n)

        # 楔 [0, delta) をソース領域とする（中心 (cx,cy) を原点として角度で定義）。
        # 法線は単位長（sin^2+cos^2=1）。クリップは include-boundary。
        n0x, n0y = 0.0, 1.0  # y>=cy
        n1x = float(-np.sin(delta))
        n1y = float(np.cos(delta))

        wedge_pieces: list[np.ndarray] = []
        need_dedup = False

        for li in range(int(offsets.size) - 1):
            v = coords[int(offsets[li]) : int(offsets[li + 1])]
            if v.shape[0] == 0:
                continue

            c0_coords, c0_offsets = _clip_polyline_halfplane_nb(v, cx_f, cy_f, n0x, n0y)
            for i0 in range(int(c0_offsets.size) - 1):
                s0 = int(c0_offsets[i0])
                e0 = int(c0_offsets[i0 + 1])
                p0 = c0_coords[s0:e0]
                if p0.shape[0] == 0:
                    continue

                c1_coords, c1_offsets = _clip_polyline_halfplane_nb(
                    p0, cx_f, cy_f, -n1x, -n1y
                )
                for i1 in range(int(c1_offsets.size) - 1):
                    s1 = int(c1_offsets[i1])
                    e1 = int(c1_offsets[i1 + 1])
                    p1 = c1_coords[s1:e1]
                    if p1.shape[0] == 0:
                        continue
                    wedge_pieces.append(p1)
                    if (not need_dedup) and _has_wedge_boundary_segment(
                        p1, cx=cx_f, cy=cy_f, n1x=n1x, n1y=n1y
                    ):
                        need_dedup = True

        if not wedge_pieces:
            return _empty_geometry()

        angles = (np.arange(n, dtype=np.float32) * np.float32(step)).astype(
            np.float32, copy=False
        )
        cos = np.cos(angles).astype(np.float32, copy=False)
        sin = np.sin(angles).astype(np.float32, copy=False)

        n_lines = len(wedge_pieces) * 2 * n
        total_vertices = sum(int(p.shape[0]) for p in wedge_pieces) * 2 * n
        out_coords_arr = np.empty((total_vertices, 3), dtype=np.float32)
        out_offsets_arr = np.empty((n_lines + 1,), dtype=np.int32)
        out_offsets_arr[0] = 0

        v_cursor = 0
        o_cursor = 0
        for p in wedge_pieces:
            ln = int(p.shape[0])
            block = 2 * n * ln
            _fill_wedge_mirror_copies_nb(
                p.astype(np.float32, copy=False),
                np.float32(cx_f),
                np.float32(cy_f),
                cos,
                sin,
                out_coords_arr[v_cursor : v_cursor + block],
            )
            # 各コピーは同じ頂点数なので offsets は等差で埋める。
            offset_base = int(out_offsets_arr[o_cursor])
            out_offsets_arr[o_cursor + 1 : o_cursor + 2 * n + 1] = (
                offset_base + ln * np.arange(1, 2 * n + 1, dtype=np.int32)
            )
            o_cursor += 2 * n
            v_cursor += block

        # 通常ケースは重複が出にくいので、dedup は必要時のみ。
        if need_dedup:
            lines = [
                out_coords_arr[int(out_offsets_arr[i]) : int(out_offsets_arr[i + 1])]
                for i in range(int(out_offsets_arr.size) - 1)
            ]
            uniq = _dedup_lines(lines)
            if not uniq:
                return _empty_geometry()
            out_coords_arr = np.vstack(uniq).astype(np.float32, copy=False)
            out_offsets_arr = np.zeros((len(uniq) + 1,), dtype=np.int32)
            acc = 0
            for i, ln0 in enumerate(uniq, start=1):
                acc += int(ln0.shape[0])
                out_offsets_arr[i] = acc

        if show_planes:
            out_coords_arr, out_offsets_arr = _append_wedge_planes(
                out_coords_arr,
                out_offsets_arr,
                n=n,
                cx=cx_f,
                cy=cy_f,
            )

        return RealizedGeometry(coords=out_coords_arr, offsets=out_offsets_arr)

    uniq = _dedup_lines(out_lines)

    if show_planes:
        if uniq:
            all_pts = np.vstack(uniq).astype(np.float32, copy=False)
        else:
            all_pts = coords.astype(np.float32, copy=False)

        if all_pts.size == 0:
            x_min, x_max = cx_f - 1.0, cx_f + 1.0
            y_min, y_max = cy_f - 1.0, cy_f + 1.0
        else:
            x_min0 = float(np.min(all_pts[:, 0]))
            x_max0 = float(np.max(all_pts[:, 0]))
            y_min0 = float(np.min(all_pts[:, 1]))
            y_max0 = float(np.max(all_pts[:, 1]))
            if n >= 1:
                x_min = min(x_min0, 2.0 * cx_f - x_max0)
                x_max = max(x_max0, 2.0 * cx_f - x_min0)
            else:
                x_min, x_max = x_min0, x_max0
            if n >= 2:
                y_min = min(y_min0, 2.0 * cy_f - y_max0)
                y_max = max(y_max0, 2.0 * cy_f - y_min0)
            else:
                y_min, y_max = y_min0, y_max0

        plane_lines: list[np.ndarray] = []
        if n == 1:
            plane_lines.append(
                np.array([[cx_f, y_min, 0.0], [cx_f, y_max, 0.0]], dtype=np.float32)
            )
        elif n == 2:
            plane_lines.extend(
                [
                    np.array(
                        [[cx_f, y_min, 0.0], [cx_f, y_max, 0.0]], dtype=np.float32
                    ),
                    np.array(
                        [[x_min, cy_f, 0.0], [x_max, cy_f, 0.0]], dtype=np.float32
                    ),
                ]
            )
        else:
            delta = float(np.pi / n)
            step = float(2.0 * np.pi / n)
            if all_pts.size == 0:
                r = 1.0
            else:
                dx = all_pts[:, 0] - cx_f
                dy = all_pts[:, 1] - cy_f
                r = float(np.sqrt(np.max(dx * dx + dy * dy)))
                if not np.isfinite(r) or r <= 0.0:
                    r = 1.0
            r *= 1.05
            for k in range(n):
                for ang in (k * step, k * step + delta):
                    cth = float(np.cos(ang))
                    sth = float(np.sin(ang))
                    p0 = np.array(
                        [cx_f - r * cth, cy_f - r * sth, 0.0], dtype=np.float32
                    )
                    p1 = np.array(
                        [cx_f + r * cth, cy_f + r * sth, 0.0], dtype=np.float32
                    )
                    plane_lines.append(
                        np.vstack([p0, p1]).astype(np.float32, copy=False)
                    )

        if plane_lines:
            uniq.extend(plane_lines)

    if not uniq:
        return _empty_geometry()

    all_coords = np.vstack(uniq).astype(np.float32, copy=False)
    new_offsets = np.zeros((len(uniq) + 1,), dtype=np.int32)
    acc = 0
    for i, line in enumerate(uniq, start=1):
        acc += int(line.shape[0])
        new_offsets[i] = acc
    return RealizedGeometry(coords=all_coords, offsets=new_offsets)


def _is_inside(val: float, thresh: float, side: int) -> bool:
    d = float(side) * (float(val) - float(thresh))
    return d >= (-EPS if INCLUDE_BOUNDARY else EPS)


def _intersect_axis(
    a: np.ndarray, b: np.ndarray, axis: int, thresh: float
) -> np.ndarray:
    da = float(a[axis])
    db = float(b[axis])
    denom = db - da
    if abs(denom) < 1e-20:
        return a.astype(np.float32, copy=True)
    t = (float(thresh) - da) / denom
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    p = a.astype(np.float32) + (
        b.astype(np.float32) - a.astype(np.float32)
    ) * np.float32(t)
    p[axis] = np.float32(thresh)
    return p.astype(np.float32, copy=False)


def _clip_polyline_halfspace(
    vertices: np.ndarray,
    *,
    axis: int,
    thresh: float,
    side: int,
) -> list[np.ndarray]:
    n = int(vertices.shape[0])
    if n == 0:
        return []
    if n == 1:
        v0 = vertices[0]
        return (
            [vertices.astype(np.float32, copy=True)]
            if _is_inside(float(v0[axis]), thresh, side)
            else []
        )

    out: list[np.ndarray] = []
    cur: list[np.ndarray] = []
    prev = vertices[0].astype(np.float32, copy=False)
    prev_in = _is_inside(float(prev[axis]), thresh, side)
    if prev_in:
        cur.append(prev.copy())

    for i in range(1, n):
        pt = vertices[i].astype(np.float32, copy=False)
        now_in = _is_inside(float(pt[axis]), thresh, side)
        if prev_in and now_in:
            cur.append(pt.copy())
        elif prev_in and not now_in:
            ip = _intersect_axis(prev, pt, axis, thresh)
            if len(cur) == 0 or not np.allclose(cur[-1], ip, atol=EPS):
                cur.append(ip)
            out.append(np.vstack(cur).astype(np.float32, copy=False))
            cur = []
        elif (not prev_in) and now_in:
            ip = _intersect_axis(prev, pt, axis, thresh)
            cur = [ip, pt.copy()]
        prev, prev_in = pt, now_in

    if cur:
        out.append(np.vstack(cur).astype(np.float32, copy=False))
    return out


def _clip_polyline_quadrant(
    vertices: np.ndarray,
    *,
    cx: float,
    cy: float,
    sx: int,
    sy: int,
) -> list[np.ndarray]:
    pieces = _clip_polyline_halfspace(vertices, axis=0, thresh=cx, side=sx)
    out: list[np.ndarray] = []
    for p in pieces:
        out.extend(_clip_polyline_halfspace(p, axis=1, thresh=cy, side=sy))
    return out


def _clip_polyline_halfplane(
    vertices: np.ndarray,
    *,
    cx: float,
    cy: float,
    normal: np.ndarray,
) -> list[np.ndarray]:
    cxy = np.array([cx, cy], dtype=np.float32)
    nrm = normal.astype(np.float32, copy=False)
    nl = float(np.linalg.norm(nrm))
    if nl <= 0.0 or not np.isfinite(nl):
        return []
    nrm = nrm / np.float32(nl)

    npts = int(vertices.shape[0])
    if npts == 0:
        return []
    if npts == 1:
        s0 = float(np.dot(vertices[0, :2].astype(np.float32, copy=False) - cxy, nrm))
        ok = s0 >= (-EPS if INCLUDE_BOUNDARY else EPS)
        return [vertices.astype(np.float32, copy=True)] if ok else []

    out_segs: list[np.ndarray] = []
    cur: list[np.ndarray] = []

    a = vertices[0].astype(np.float32, copy=False)
    s_a = float(np.dot(a[:2] - cxy, nrm))
    in_a = s_a >= (-EPS if INCLUDE_BOUNDARY else EPS)
    if in_a:
        cur.append(a.copy())

    for j in range(1, npts):
        b = vertices[j].astype(np.float32, copy=False)
        s_b = float(np.dot(b[:2] - cxy, nrm))
        in_b = s_b >= (-EPS if INCLUDE_BOUNDARY else EPS)

        if in_a and in_b:
            cur.append(b.copy())
        elif in_a and not in_b:
            denom = s_a - s_b
            t = 0.0 if abs(denom) < 1e-20 else s_a / denom
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            p = a + (b - a) * np.float32(t)
            if len(cur) == 0 or not np.allclose(cur[-1], p, atol=EPS):
                cur.append(p.astype(np.float32, copy=False))
            out_segs.append(np.vstack(cur).astype(np.float32, copy=False))
            cur = []
        elif (not in_a) and in_b:
            denom = s_a - s_b
            t = 0.0 if abs(denom) < 1e-20 else s_a / denom
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            p = a + (b - a) * np.float32(t)
            cur = [p.astype(np.float32, copy=False), b.copy()]

        a, s_a, in_a = b, s_b, in_b

    if cur:
        out_segs.append(np.vstack(cur).astype(np.float32, copy=False))
    return out_segs


def _has_wedge_boundary_segment(
    vertices: np.ndarray, *, cx: float, cy: float, n1x: float, n1y: float
) -> bool:
    npts = int(vertices.shape[0])
    if npts <= 0:
        return False

    if npts == 1:
        x0 = float(vertices[0, 0])
        y0 = float(vertices[0, 1])
        if abs(y0 - float(cy)) <= EPS:
            return True
        d0 = (x0 - float(cx)) * float(n1x) + (y0 - float(cy)) * float(n1y)
        return abs(d0) <= EPS

    y = vertices[:, 1].astype(np.float32, copy=False)
    on_y = np.abs(y - np.float32(cy)) <= np.float32(EPS)
    if bool(np.any(on_y[:-1] & on_y[1:])):
        return True

    x = vertices[:, 0].astype(np.float32, copy=False)
    d = (x - np.float32(cx)) * np.float32(n1x) + (y - np.float32(cy)) * np.float32(n1y)
    on_theta = np.abs(d) <= np.float32(EPS)
    return bool(np.any(on_theta[:-1] & on_theta[1:]))


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _clip_polyline_halfplane_nb(
    vertices: np.ndarray,
    cx: float,
    cy: float,
    nx: float,
    ny: float,
) -> tuple[np.ndarray, np.ndarray]:
    npts = int(vertices.shape[0])
    if npts == 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((1,), dtype=np.int32)

    eps = float(EPS)
    thr = -eps if INCLUDE_BOUNDARY else eps

    if npts == 1:
        s0 = (float(vertices[0, 0]) - float(cx)) * float(nx) + (
            float(vertices[0, 1]) - float(cy)
        ) * float(ny)
        if s0 >= thr:
            out1 = np.empty((1, 3), dtype=np.float32)
            out1[0, 0] = float(vertices[0, 0])
            out1[0, 1] = float(vertices[0, 1])
            out1[0, 2] = float(vertices[0, 2])
            offs1 = np.empty((2,), dtype=np.int32)
            offs1[0] = 0
            offs1[1] = 1
            return out1, offs1
        return np.zeros((0, 3), dtype=np.float32), np.zeros((1,), dtype=np.int32)

    # 半平面クリップで点数は高々 2*n 程度になる（交点の挿入分）。
    out_coords = np.empty((2 * npts + 2, 3), dtype=np.float32)
    out_offsets = np.empty((npts + 1,), dtype=np.int32)
    out_offsets[0] = 0

    out_n = 0
    out_lines = 0

    ax = float(vertices[0, 0])
    ay = float(vertices[0, 1])
    az = float(vertices[0, 2])
    s_a = (ax - float(cx)) * float(nx) + (ay - float(cy)) * float(ny)
    in_a = s_a >= thr
    cur_open = in_a
    if in_a:
        out_coords[out_n, 0] = ax
        out_coords[out_n, 1] = ay
        out_coords[out_n, 2] = az
        out_n += 1

    for i in range(1, npts):
        bx = float(vertices[i, 0])
        by = float(vertices[i, 1])
        bz = float(vertices[i, 2])
        s_b = (bx - float(cx)) * float(nx) + (by - float(cy)) * float(ny)
        in_b = s_b >= thr

        if in_a and in_b:
            out_coords[out_n, 0] = bx
            out_coords[out_n, 1] = by
            out_coords[out_n, 2] = bz
            out_n += 1
            cur_open = True
        elif in_a and (not in_b):
            denom = s_a - s_b
            t = 0.0 if abs(denom) < 1e-20 else s_a / denom
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            px = ax + (bx - ax) * t
            py = ay + (by - ay) * t
            pz = az + (bz - az) * t
            if out_n == 0 or (
                abs(float(out_coords[out_n - 1, 0]) - px) > eps
                or abs(float(out_coords[out_n - 1, 1]) - py) > eps
                or abs(float(out_coords[out_n - 1, 2]) - pz) > eps
            ):
                out_coords[out_n, 0] = px
                out_coords[out_n, 1] = py
                out_coords[out_n, 2] = pz
                out_n += 1
            if cur_open:
                out_lines += 1
                out_offsets[out_lines] = out_n
            cur_open = False
        elif (not in_a) and in_b:
            denom = s_a - s_b
            t = 0.0 if abs(denom) < 1e-20 else s_a / denom
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            px = ax + (bx - ax) * t
            py = ay + (by - ay) * t
            pz = az + (bz - az) * t
            out_coords[out_n, 0] = px
            out_coords[out_n, 1] = py
            out_coords[out_n, 2] = pz
            out_n += 1
            out_coords[out_n, 0] = bx
            out_coords[out_n, 1] = by
            out_coords[out_n, 2] = bz
            out_n += 1
            cur_open = True
        else:
            # 外→外
            pass

        ax, ay, az = bx, by, bz
        s_a, in_a = s_b, in_b

    if cur_open:
        out_lines += 1
        out_offsets[out_lines] = out_n

    if out_lines <= 0 or out_n <= 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((1,), dtype=np.int32)

    return out_coords[:out_n], out_offsets[: out_lines + 1]


@njit(cache=True, fastmath=True)  # type: ignore[misc]
def _fill_wedge_mirror_copies_nb(
    piece: np.ndarray,
    cx: np.float32,
    cy: np.float32,
    cos: np.ndarray,
    sin: np.ndarray,
    out: np.ndarray,
) -> None:
    n = int(cos.shape[0])
    ln = int(piece.shape[0])

    for m in range(n):
        c = float(cos[m])
        s = float(sin[m])
        base = m * ln
        for i in range(ln):
            x0 = float(piece[i, 0]) - float(cx)
            y0 = float(piece[i, 1]) - float(cy)
            out[base + i, 0] = x0 * c - y0 * s + float(cx)
            out[base + i, 1] = x0 * s + y0 * c + float(cy)
            out[base + i, 2] = float(piece[i, 2])

    for m in range(n):
        c = float(cos[m])
        s = float(sin[m])
        base = (n + m) * ln
        for i in range(ln):
            x0 = float(piece[i, 0]) - float(cx)
            y0 = float(cy) - float(piece[i, 1])
            out[base + i, 0] = x0 * c - y0 * s + float(cx)
            out[base + i, 1] = x0 * s + y0 * c + float(cy)
            out[base + i, 2] = float(piece[i, 2])


def _append_wedge_planes(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    n: int,
    cx: float,
    cy: float,
) -> tuple[np.ndarray, np.ndarray]:
    if coords.size == 0:
        r = 1.0
    else:
        dx = coords[:, 0].astype(np.float32, copy=False) - np.float32(cx)
        dy = coords[:, 1].astype(np.float32, copy=False) - np.float32(cy)
        r = float(np.sqrt(np.max(dx * dx + dy * dy)))
        if not np.isfinite(r) or r <= 0.0:
            r = 1.0
    r *= 1.05

    delta = float(np.pi / int(n))
    step = float(2.0 * np.pi / int(n))

    n_plane_lines = 2 * int(n)
    plane_coords = np.empty((n_plane_lines * 2, 3), dtype=np.float32)
    plane_offsets = np.empty((n_plane_lines + 1,), dtype=np.int32)
    plane_offsets[0] = 0

    idx = 0
    for k in range(int(n)):
        for ang in (k * step, k * step + delta):
            cth = float(np.cos(ang))
            sth = float(np.sin(ang))
            plane_coords[idx, 0] = np.float32(cx - r * cth)
            plane_coords[idx, 1] = np.float32(cy - r * sth)
            plane_coords[idx, 2] = np.float32(0.0)
            plane_coords[idx + 1, 0] = np.float32(cx + r * cth)
            plane_coords[idx + 1, 1] = np.float32(cy + r * sth)
            plane_coords[idx + 1, 2] = np.float32(0.0)
            idx += 2
            plane_offsets[idx // 2] = idx

    base = int(offsets[-1]) if offsets.size > 0 else 0
    new_coords = np.concatenate([coords, plane_coords], axis=0).astype(
        np.float32, copy=False
    )
    new_offsets = np.concatenate([offsets, base + plane_offsets[1:]], axis=0).astype(
        np.int32, copy=False
    )
    return new_coords, new_offsets


def _reflect_x(vertices: np.ndarray, cx: float) -> np.ndarray:
    r = vertices.astype(np.float32, copy=True)
    r[:, 0] = np.float32(2.0 * float(cx)) - r[:, 0]
    return r


def _reflect_y(vertices: np.ndarray, cy: float) -> np.ndarray:
    r = vertices.astype(np.float32, copy=True)
    r[:, 1] = np.float32(2.0 * float(cy)) - r[:, 1]
    return r


def _rotate_xy(vertices: np.ndarray, ang: float, cx: float, cy: float) -> np.ndarray:
    if vertices.shape[0] == 0:
        return vertices.astype(np.float32, copy=True)
    c = float(np.cos(ang))
    s = float(np.sin(ang))
    out = vertices.astype(np.float32, copy=True)
    out[:, 0] -= np.float32(cx)
    out[:, 1] -= np.float32(cy)
    x = out[:, 0].copy()
    y = out[:, 1].copy()
    out[:, 0] = x * np.float32(c) - y * np.float32(s)
    out[:, 1] = x * np.float32(s) + y * np.float32(c)
    out[:, 0] += np.float32(cx)
    out[:, 1] += np.float32(cy)
    return out


def _dedup_lines(lines: Iterable[np.ndarray]) -> list[np.ndarray]:
    seen: set[tuple[int, bytes]] = set()
    out: list[np.ndarray] = []
    inv = 1.0 / EPS if EPS > 0 else 1e6
    for ln in lines:
        if ln.shape[0] == 0:
            continue
        q = np.rint(ln.astype(np.float64, copy=False) * inv).astype(
            np.int64, copy=False
        )
        key = (int(q.shape[0]), q.tobytes())
        if key in seen:
            continue
        seen.add(key)
        out.append(ln.astype(np.float32, copy=False))
    return out
