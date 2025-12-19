"""線分を細分化し、局所的なランダム変位で「崩し」を作る effect。"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from numba import njit  # type: ignore[import-untyped]

from grafix.core.effect_registry import effect
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.parameters.meta import ParamMeta

EPS = 1e-12

collapse_meta = {
    "intensity": ParamMeta(kind="float", ui_min=0.0, ui_max=10.0),
    "subdivisions": ParamMeta(kind="int", ui_min=0, ui_max=10),
}


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


@effect(meta=collapse_meta)
def collapse(
    inputs: Sequence[RealizedGeometry],
    *,
    intensity: float = 5.0,
    subdivisions: int = 6,
) -> RealizedGeometry:
    """線分を細分化してノイズで崩す（非接続）。

    Parameters
    ----------
    inputs : Sequence[RealizedGeometry]
        入力の実体ジオメトリ列。通常は 1 要素。
    intensity : float, default 5.0
        変位量（長さ単位は座標系に従う）。0.0 で no-op。
    subdivisions : int, default 6
        細分回数。0 以下で no-op。

    Returns
    -------
    RealizedGeometry
        変形後の実体ジオメトリ。

    Notes
    -----
    出力は「各サブセグメントが 2 点からなる独立ポリライン（非接続）」。
    """
    if not inputs:
        return _empty_geometry()

    base = inputs[0]
    if base.coords.shape[0] == 0:
        return base

    intensity = float(intensity)
    divisions = int(subdivisions)
    if intensity == 0.0 or divisions <= 0:
        return base

    new_coords, new_offsets = _collapse_numba(base.coords, base.offsets, intensity, divisions)
    return RealizedGeometry(coords=new_coords, offsets=new_offsets)


def _collapse_numpy_v2(
    coords: np.ndarray,
    offsets: np.ndarray,
    intensity: float,
    divisions: int,
) -> tuple[np.ndarray, np.ndarray]:
    """collapse を分布互換のまま効率化（2 パス + 前方確保）。"""
    if coords.shape[0] == 0 or intensity == 0.0 or divisions <= 0:
        return coords.copy(), offsets.copy()

    rng = np.random.default_rng(0)
    n_lines = len(offsets) - 1

    total_lines = 0
    total_vertices = 0
    for li in range(n_lines):
        v = coords[offsets[li] : offsets[li + 1]]
        n = v.shape[0]
        if n < 2:
            total_lines += 1
            total_vertices += n
            continue
        seg = v[1:] - v[:-1]
        L = np.sqrt(np.sum(seg.astype(np.float64) ** 2, axis=1))
        nz = L > EPS
        total_lines += int(np.count_nonzero(nz)) * divisions + int(np.count_nonzero(~nz))
        total_vertices += (
            int(np.count_nonzero(nz)) * (2 * divisions) + int(np.count_nonzero(~nz)) * 2
        )

    if total_lines == 0:
        return coords.copy(), offsets.copy()

    out_coords = np.empty((total_vertices, 3), dtype=np.float32)
    out_offsets = np.empty((total_lines + 1,), dtype=np.int32)
    out_offsets[0] = 0
    vc = 0
    oc = 1

    t = np.linspace(0.0, 1.0, divisions + 1, dtype=np.float64)
    t0 = t[:-1]
    t1 = t[1:]

    for li in range(n_lines):
        v = coords[offsets[li] : offsets[li + 1]].astype(np.float64, copy=False)
        n = v.shape[0]
        if n < 2:
            if n > 0:
                out_coords[vc : vc + n] = v.astype(np.float32, copy=False)
                vc += n
            out_offsets[oc] = vc
            oc += 1
            continue

        for j in range(n - 1):
            a = v[j]
            b = v[j + 1]
            d = b - a
            L = float(np.sqrt(np.dot(d, d)))
            if not np.isfinite(L) or L <= EPS:
                out_coords[vc] = a.astype(np.float32)
                vc += 1
                out_coords[vc] = b.astype(np.float32)
                vc += 1
                out_offsets[oc] = vc
                oc += 1
                continue

            n_main = d / L
            ref = np.array([0.0, 0.0, 1.0], dtype=np.float64)
            if abs(n_main[2]) >= 0.9:
                ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
            u = np.cross(n_main, ref)
            ul = float(np.sqrt(np.dot(u, u)))
            if ul <= EPS:
                u = np.array([1.0, 0.0, 0.0], dtype=np.float64)
                ul = 1.0
            u /= ul
            v_basis = np.cross(n_main, u)

            starts = a * (1.0 - t0[:, None]) + b * t0[:, None]
            ends = a * (1.0 - t1[:, None]) + b * t1[:, None]

            theta = rng.random(divisions) * (2.0 * math.pi)
            c = np.cos(theta)
            s = np.sin(theta)
            noise = (c[:, None] * u[None, :] + s[:, None] * v_basis[None, :]) * float(intensity)

            out_coords[vc : vc + 2 * divisions : 2] = (starts + noise).astype(
                np.float32, copy=False
            )
            out_coords[vc + 1 : vc + 2 * divisions : 2] = (ends + noise).astype(
                np.float32, copy=False
            )
            out_offsets[oc : oc + divisions] = vc + 2 * (np.arange(divisions, dtype=np.int32) + 1)
            vc += 2 * divisions
            oc += divisions

    if oc < out_offsets.shape[0]:
        out_offsets[oc:] = vc
    return out_coords, out_offsets


def _collapse_count(
    coords: np.ndarray,
    offsets: np.ndarray,
    divisions: int,
) -> tuple[int, int, int]:
    """出力配列サイズと有効セグメント数を事前に数える（NumPy）。"""
    n_lines = len(offsets) - 1
    total_lines = 0
    total_vertices = 0
    valid_seg_count = 0

    for li in range(n_lines):
        v = coords[offsets[li] : offsets[li + 1]]
        n = v.shape[0]
        if n < 2:
            total_lines += 1
            total_vertices += n
            continue
        seg = v[1:] - v[:-1]
        L2 = np.sum(seg.astype(np.float64) ** 2, axis=1)
        L = np.sqrt(L2)
        finite = np.isfinite(L)
        mask = finite & (L > EPS)
        n_valid = int(np.count_nonzero(mask))
        n_invalid = int(np.count_nonzero(~mask))

        valid_seg_count += n_valid
        total_lines += n_valid * divisions + n_invalid
        total_vertices += n_valid * (2 * divisions) + n_invalid * 2

    return total_lines, total_vertices, valid_seg_count


@njit(cache=True, fastmath=False)
def _collapse_njit_fill(
    coords64: np.ndarray,
    offsets32: np.ndarray,
    intensity64: float,
    divisions: int,
    t0: np.ndarray,
    t1: np.ndarray,
    cos_list: np.ndarray,
    sin_list: np.ndarray,
    out_coords32: np.ndarray,
    out_offsets32: np.ndarray,
) -> None:
    vc = 0
    oc = 1
    out_offsets32[0] = 0
    idx = 0

    n_lines = offsets32.shape[0] - 1
    for li in range(n_lines):
        start = int(offsets32[li])
        end = int(offsets32[li + 1])
        n = end - start
        if n < 2:
            if n > 0:
                for m in range(n):
                    p = coords64[start + m]
                    out_coords32[vc, 0] = float(p[0])
                    out_coords32[vc, 1] = float(p[1])
                    out_coords32[vc, 2] = float(p[2])
                    vc += 1
            out_offsets32[oc] = vc
            oc += 1
            continue

        for j in range(n - 1):
            a = coords64[start + j]
            b = coords64[start + j + 1]
            d0 = b[0] - a[0]
            d1 = b[1] - a[1]
            d2 = b[2] - a[2]
            L = math.sqrt(d0 * d0 + d1 * d1 + d2 * d2)
            if (not math.isfinite(L)) or (L <= EPS):
                out_coords32[vc, 0] = float(a[0])
                out_coords32[vc, 1] = float(a[1])
                out_coords32[vc, 2] = float(a[2])
                vc += 1
                out_coords32[vc, 0] = float(b[0])
                out_coords32[vc, 1] = float(b[1])
                out_coords32[vc, 2] = float(b[2])
                vc += 1
                out_offsets32[oc] = vc
                oc += 1
                continue

            invL = 1.0 / L
            nmx = d0 * invL
            nmy = d1 * invL
            nmz = d2 * invL

            refx = 0.0
            refy = 0.0
            refz = 1.0
            if abs(nmz) >= 0.9:
                refx = 1.0
                refy = 0.0
                refz = 0.0

            ux = nmy * refz - nmz * refy
            uy = nmz * refx - nmx * refz
            uz = nmx * refy - nmy * refx
            ul = math.sqrt(ux * ux + uy * uy + uz * uz)
            if ul <= EPS:
                ux, uy, uz = 1.0, 0.0, 0.0
                ul = 1.0
            inv_ul = 1.0 / ul
            ux *= inv_ul
            uy *= inv_ul
            uz *= inv_ul

            vx = nmy * uz - nmz * uy
            vy = nmz * ux - nmx * uz
            vz = nmx * uy - nmy * ux

            for k in range(divisions):
                t0k = t0[k]
                t1k = t1[k]

                p0x = a[0] * (1.0 - t0k) + b[0] * t0k
                p0y = a[1] * (1.0 - t0k) + b[1] * t0k
                p0z = a[2] * (1.0 - t0k) + b[2] * t0k

                p1x = a[0] * (1.0 - t1k) + b[0] * t1k
                p1y = a[1] * (1.0 - t1k) + b[1] * t1k
                p1z = a[2] * (1.0 - t1k) + b[2] * t1k

                c = cos_list[idx]
                s = sin_list[idx]
                idx += 1

                nx = (c * ux + s * vx) * intensity64
                ny = (c * uy + s * vy) * intensity64
                nz = (c * uz + s * vz) * intensity64

                out_coords32[vc, 0] = float(p0x + nx)
                out_coords32[vc, 1] = float(p0y + ny)
                out_coords32[vc, 2] = float(p0z + nz)
                vc += 1
                out_coords32[vc, 0] = float(p1x + nx)
                out_coords32[vc, 1] = float(p1y + ny)
                out_coords32[vc, 2] = float(p1z + nz)
                vc += 1

                out_offsets32[oc] = vc
                oc += 1


def _collapse_numba(
    coords: np.ndarray,
    offsets: np.ndarray,
    intensity: float,
    divisions: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Numba 経路で collapse を実行する。"""
    if coords.shape[0] == 0 or intensity == 0.0 or divisions <= 0:
        return coords.copy(), offsets.copy()

    total_lines, total_vertices, valid_seg_count = _collapse_count(coords, offsets, divisions)
    if total_lines == 0:
        return coords.copy(), offsets.copy()

    out_coords = np.empty((total_vertices, 3), dtype=np.float32)
    out_offsets = np.empty((total_lines + 1,), dtype=np.int32)

    t = np.linspace(0.0, 1.0, divisions + 1, dtype=np.float64)
    t0 = t[:-1]
    t1 = t[1:]

    rng = np.random.default_rng(0)
    theta = rng.random(valid_seg_count * divisions) * (2.0 * math.pi)
    cos_list = np.cos(theta)
    sin_list = np.sin(theta)

    coords64 = coords.astype(np.float64, copy=False)
    offsets32 = offsets.astype(np.int32, copy=False)

    _collapse_njit_fill(
        coords64,
        offsets32,
        float(intensity),
        int(divisions),
        t0,
        t1,
        cos_list,
        sin_list,
        out_coords,
        out_offsets,
    )
    return out_coords, out_offsets
