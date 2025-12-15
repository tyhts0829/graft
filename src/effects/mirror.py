"""
どこで: `src/effects/mirror.py`。ミラー（mirror）effect の実体変換。
何を: 入力ポリライン列を指定領域へクリップし、対称変換で複製する。
なぜ: 左右/象限/放射状の対称パターンを effect チェーンとして扱えるようにするため。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np

from src.core.effect_registry import effect
from src.core.realized_geometry import RealizedGeometry
from src.parameters.meta import ParamMeta

EPS = 1e-6
INCLUDE_BOUNDARY = True

mirror_meta = {
    "n_mirror": ParamMeta(kind="int", ui_min=1, ui_max=12),
    "cx": ParamMeta(kind="float", ui_min=-500.0, ui_max=500.0),
    "cy": ParamMeta(kind="float", ui_min=-500.0, ui_max=500.0),
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

    base = inputs[0]
    coords = base.coords
    offsets = base.offsets
    if coords.shape[0] == 0:
        return base

    n = int(n_mirror)
    if n < 1:
        return base

    cx_f = float(cx)
    cy_f = float(cy)
    if not (np.isfinite(cx_f) and np.isfinite(cy_f)):
        return base

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
        n0 = np.array([0.0, 1.0], dtype=np.float32)  # y>=0
        n1 = np.array([-np.sin(delta), np.cos(delta)], dtype=np.float32)  # θ=delta の法線

        for li in range(int(offsets.size) - 1):
            v = coords[int(offsets[li]) : int(offsets[li + 1])]
            if v.shape[0] == 0:
                continue
            pieces = _clip_polyline_halfplane(v, cx=cx_f, cy=cy_f, normal=n0)
            tmp: list[np.ndarray] = []
            for p in pieces:
                tmp.extend(_clip_polyline_halfplane(p, cx=cx_f, cy=cy_f, normal=-n1))
            for p in tmp:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32, copy=False))

        for p in src_lines:
            for m in range(n):
                out_lines.append(_rotate_xy(p, m * step, cx_f, cy_f))
            pref = _reflect_y(p, cy_f)
            for m in range(n):
                out_lines.append(_rotate_xy(pref, m * step, cx_f, cy_f))

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
                    np.array([[cx_f, y_min, 0.0], [cx_f, y_max, 0.0]], dtype=np.float32),
                    np.array([[x_min, cy_f, 0.0], [x_max, cy_f, 0.0]], dtype=np.float32),
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
                    p0 = np.array([cx_f - r * cth, cy_f - r * sth, 0.0], dtype=np.float32)
                    p1 = np.array([cx_f + r * cth, cy_f + r * sth, 0.0], dtype=np.float32)
                    plane_lines.append(np.vstack([p0, p1]).astype(np.float32, copy=False))

        if plane_lines:
            uniq.extend(plane_lines)

    if not uniq:
        return _empty_geometry()

    all_coords = np.vstack(uniq).astype(np.float32, copy=False)
    new_offsets = np.zeros((len(uniq) + 1,), dtype=np.int32)
    acc = 0
    for i, ln in enumerate(uniq, start=1):
        acc += int(ln.shape[0])
        new_offsets[i] = acc
    return RealizedGeometry(coords=all_coords, offsets=new_offsets)


def _is_inside(val: float, thresh: float, side: int) -> bool:
    d = float(side) * (float(val) - float(thresh))
    return d >= (-EPS if INCLUDE_BOUNDARY else EPS)


def _intersect_axis(a: np.ndarray, b: np.ndarray, axis: int, thresh: float) -> np.ndarray:
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
    p = a.astype(np.float32) + (b.astype(np.float32) - a.astype(np.float32)) * np.float32(t)
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
        return [vertices.astype(np.float32, copy=True)] if _is_inside(float(v0[axis]), thresh, side) else []

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
        q = np.rint(ln.astype(np.float64, copy=False) * inv).astype(np.int64, copy=False)
        key = (int(q.shape[0]), q.tobytes())
        if key in seen:
            continue
        seen.add(key)
        out.append(ln.astype(np.float32, copy=False))
    return out

