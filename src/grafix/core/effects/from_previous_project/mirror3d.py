"""
どこで: `effects`
何を: 真の3D放射状ミラー（球面くさび / 多面体対称）
なぜ: 3D 空間における等角度/群対称の鏡映複製を提供するため。

概要:
- mode='azimuth': 回転軸 `axis` を含む 2 枚の境界平面でくさびを形成（Δφ=π/n_azimuth）。
  - ソースはくさび内（2 半空間 AND）にクリップ。非反転/境界1反転を n 回回して 2n 複製。
  - `mirror_equator=True` で赤道反転を追加（最大 4n）。Z は反転される。
- mode='polyhedral': 多面体対称（T/O/I）に基づく回転群/反射で複製。
  - v1 は回転群（T=12, O=24, I=60）＋代表反射で倍化（24/48/120）。
  - ソースは三半空間 AND（正の八分体）にクリップ（将来、球面三角形へ切替予定）。

数値安定:
- EPS=1e-6, INCLUDE_BOUNDARY=True を固定し、クリップ/交点/重複除去に用いる。
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

from engine.core.geometry import Geometry

from .registry import effect

# 固定許容誤差と境界ポリシー
EPS: float = 1e-6
INCLUDE_BOUNDARY: bool = True
PARAM_META = {
    "n_azimuth": {"min": 1, "max": 64, "step": 1},
    "cx": {"min": 0.0, "max": 1000.0},
    "cy": {"min": 0.0, "max": 1000.0},
    "cz": {"min": 0.0, "max": 1000.0},
    "phi0": {"min": -180.0, "max": 180.0, "step": 1.0},
    # 追加（polyhedral モード向け）
    "mode": {"choices": ["azimuth", "polyhedral"]},
    "group": {"choices": ["T", "O", "I"]},
    "use_reflection": {"type": "bool"},
    "axis": {"type": "vec3", "min": (-1.0, -1.0, -1.0), "max": (1.0, 1.0, 1.0)},
    "show_planes": {"type": "bool"},
}


@effect(name="mirror3d")
def mirror3d(
    g: Geometry,
    *,
    n_azimuth: int = 1,
    cx: float = 0.0,
    cy: float = 0.0,
    cz: float = 0.0,
    axis: Sequence[float] = (0.0, 0.0, 1.0),
    phi0: float = 0.0,
    mirror_equator: bool = False,
    source_side: bool | Sequence[bool] = True,
    mode: str = "azimuth",
    group: str | None = "T",
    use_reflection: bool = False,
    show_planes: bool = False,
) -> Geometry:
    """3D 放射状ミラー（azimuth/ polyhedral）。

    Parameters
    ----------
    mode : {'azimuth', 'polyhedral'}, default 'azimuth'
        ミラー方式の選択。'azimuth' は等角くさび、'polyhedral' は多面体対称（T/O/I）。
    n_azimuth : int, default 1
        方位の等分数（Δφ=π/n_azimuth）。mode='azimuth' のみ使用。
    group : {'T','O','I'} | None, default None
        多面体対称の群。mode='polyhedral' で必須（T=12, O=24, I=60 の回転群）。
    use_reflection : bool, default False
        反射込みで倍化（T:24, O:48, I:120）。
    cx, cy, cz : float, default 0.0
        中心座標（回転/反射の pivot）。
    axis : Sequence[float], default (0,0,1)
        回転軸（内部で単位化）。'azimuth' のくさび軸、'polyhedral' の代表反射にも利用。
    phi0 : float, default 0.0
        くさびの開始角 [deg]（mode='azimuth' のみ使用）。
    mirror_equator : bool, default False
        赤道面（axis ⟂）で反転を追加（mode='azimuth'）。
    source_side : bool | Sequence[bool], default True
        赤道反転のソース側（mode='azimuth' で有効）。True=正側。

    Notes
    -----
    - クリップ/交点/重複除去は EPS=1e-6, INCLUDE_BOUNDARY=True を用いて安定化している。
    - mode='polyhedral' のソース領域は v1 では三半空間 AND（正の八分体）。将来、球面三角形へ移行予定。
    """
    center = np.array([cx, cy, cz], dtype=np.float32)
    ax = _unit(np.asarray(axis, dtype=np.float32))

    if mode not in ("azimuth", "polyhedral"):
        raise ValueError(f"未知の mode: {mode}")

    coords, offsets = g.as_arrays(copy=False)
    out_lines: list[np.ndarray] = []

    if mode == "azimuth":
        if n_azimuth < 1:
            raise ValueError("n_azimuth は 1 以上の整数である必要があります。")
        phi0_rad = float(np.deg2rad(phi0))
        n0, n1 = _compute_azimuth_plane_normals(int(n_azimuth), ax, phi0_rad)

        src_lines: list[np.ndarray] = []
        # くさび内にクリップ（H0: n0·(p-c)>=0, H1: (-n1)·(p-c)>=0）
        for i in range(len(offsets) - 1):
            v = coords[offsets[i] : offsets[i + 1]]
            if v.shape[0] == 0:
                continue
            pieces = _clip_polyline_halfspace_3d(v, normal=n0, center=center)
            tmp: list[np.ndarray] = []
            for p in pieces:
                tmp.extend(_clip_polyline_halfspace_3d(p, normal=-n1, center=center))
            # equator ミラーは後段の反映で行う（ソースの半空間クリップは行わない）
            for p in tmp:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32))

        step = 2.0 * np.pi / float(n_azimuth)
        for p in src_lines:
            for m in range(int(n_azimuth)):
                out_lines.append(_rotate_around_axis(p, ax, m * step, center))
            pref = _reflect_across_plane(p, n0, center)
            for m in range(int(n_azimuth)):
                out_lines.append(_rotate_around_axis(pref, ax, m * step, center))

        if mirror_equator:
            eq_n = ax
            extra: list[np.ndarray] = []
            for ln in out_lines:
                extra.append(_reflect_across_plane(ln, eq_n, center))
            out_lines.extend(extra)

    else:  # mode == 'polyhedral'
        if group is None:
            raise ValueError("polyhedral モードでは group を指定してください（'T'|'O'|'I'）。")
        gname = str(group).upper()

        # 回転群の列挙（安定な有限集合を生成）
        def _rotM(axis3: np.ndarray, ang: float) -> np.ndarray:
            a = _unit(axis3)
            c, s = float(np.cos(ang)), float(np.sin(ang))
            K = np.array(
                [[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]],
                dtype=np.float32,
            )
            I = np.eye(3, dtype=np.float32)
            return (I + s * K + (1.0 - c) * (K @ K)).astype(np.float32)

        mats: list[np.ndarray] = []
        I3 = np.eye(3, dtype=np.float32)
        mats.append(I3)
        if gname == "T":
            vaxes = [
                np.array([1.0, 1.0, 1.0], dtype=np.float32),
                np.array([-1.0, -1.0, 1.0], dtype=np.float32),
                np.array([-1.0, 1.0, -1.0], dtype=np.float32),
                np.array([1.0, -1.0, -1.0], dtype=np.float32),
            ]
            for vax in vaxes:
                mats.append(_rotM(vax, 2.0 * np.pi / 3.0))
                mats.append(_rotM(vax, -2.0 * np.pi / 3.0))
            caxes = [
                np.array([1.0, 0.0, 0.0], dtype=np.float32),
                np.array([0.0, 1.0, 0.0], dtype=np.float32),
                np.array([0.0, 0.0, 1.0], dtype=np.float32),
            ]
            for cax in caxes:
                mats.append(_rotM(cax, np.pi))
        elif gname == "O":
            # 90°/270°（座標軸 3 本）
            for cax in (
                np.array([1.0, 0.0, 0.0], dtype=np.float32),
                np.array([0.0, 1.0, 0.0], dtype=np.float32),
                np.array([0.0, 0.0, 1.0], dtype=np.float32),
            ):
                mats.append(_rotM(cax, np.pi / 2))
                mats.append(_rotM(cax, -np.pi / 2))
                mats.append(_rotM(cax, np.pi))
            # 120°/240°（体対角 4 本）
            vaxes = [
                np.array([1.0, 1.0, 1.0], dtype=np.float32),
                np.array([-1.0, -1.0, 1.0], dtype=np.float32),
                np.array([-1.0, 1.0, -1.0], dtype=np.float32),
                np.array([1.0, -1.0, -1.0], dtype=np.float32),
            ]
            for vax in vaxes:
                mats.append(_rotM(vax, 2.0 * np.pi / 3.0))
                mats.append(_rotM(vax, -2.0 * np.pi / 3.0))
            # 180°（辺の中心軸 6 本）
            eaxes = [
                np.array([1.0, 1.0, 0.0], dtype=np.float32),
                np.array([1.0, -1.0, 0.0], dtype=np.float32),
                np.array([1.0, 0.0, 1.0], dtype=np.float32),
                np.array([1.0, 0.0, -1.0], dtype=np.float32),
                np.array([0.0, 1.0, 1.0], dtype=np.float32),
                np.array([0.0, 1.0, -1.0], dtype=np.float32),
            ]
            for eax in eaxes:
                mats.append(_rotM(eax, np.pi))
        elif gname == "I":
            # 5回軸（12 頂点方向）
            phi = (1.0 + np.sqrt(5.0)) / 2.0
            verts = []
            for s1 in (-1.0, 1.0):
                for s2 in (-1.0, 1.0):
                    verts.append(np.array([0.0, s1, s2 * phi], dtype=np.float32))
                    verts.append(np.array([s1, s2 * phi, 0.0], dtype=np.float32))
                    verts.append(np.array([s2 * phi, 0.0, s1], dtype=np.float32))
            axes5 = [_unit(v) for v in verts]
            # 3回軸（20 面中心 = 正十二面体の頂点）
            axes3 = []
            # (±1, ±1, ±1)
            for sx in (-1.0, 1.0):
                for sy in (-1.0, 1.0):
                    for sz in (-1.0, 1.0):
                        axes3.append(_unit(np.array([sx, sy, sz], dtype=np.float32)))
            invphi = 1.0 / phi
            # 3*4=12 個: (0, ±1/φ, ±φ) と循環置換
            for s1 in (-1.0, 1.0):
                for s2 in (-1.0, 1.0):
                    axes3.append(_unit(np.array([0.0, s1 * invphi, s2 * phi], dtype=np.float32)))
                    axes3.append(_unit(np.array([s1 * invphi, s2 * phi, 0.0], dtype=np.float32)))
                    axes3.append(_unit(np.array([s2 * phi, 0.0, s1 * invphi], dtype=np.float32)))
            # 2回軸（30 辺の中点方向→15 軸）: 頂点隣接判定で辺集合を生成し中点を軸に
            V = np.stack(verts, axis=0)
            # 距離で最短のものを辺と見なす
            dists = np.linalg.norm(V[None, :, :] - V[:, None, :], axis=2)
            idx = np.where(dists > 1e-6)
            pairs = list(zip(idx[0].tolist(), idx[1].tolist()))
            # 片側のみ（i<j）に制限
            pairs = [(i, j) for (i, j) in pairs if i < j]
            # 最小距離を閾値に
            min_d = min(dists[i, j] for (i, j) in pairs)
            tol = min_d * 1.01
            edges = [(i, j) for (i, j) in pairs if dists[i, j] <= tol]
            mids = [_unit((V[i] + V[j]) * 0.5) for (i, j) in edges]
            axes2 = mids

            # 行列を追加
            for a in axes5:
                for k in (1, 2, 3, 4):
                    mats.append(_rotM(a, 2.0 * np.pi * k / 5.0))
            for a in axes3:
                for k in (1, 2):
                    mats.append(_rotM(a, 2.0 * np.pi * k / 3.0))
            for a in axes2:
                mats.append(_rotM(a, np.pi))
        else:
            raise NotImplementedError(f"未知の polyhedral group: {gname}")

        # 一意化
        uniq: dict[tuple, np.ndarray] = {}
        for M in mats:
            key = tuple(np.rint(M.flatten() * (1.0 / EPS)).astype(np.int64).tolist())
            uniq[key] = M
        mats_u = list(uniq.values())
        # ソース抽出（簡易: 正の八分体 x>=cx, y>=cy, z>=cz を基本領域とする）
        normals_tri = (
            np.array([1.0, 0.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
            np.array([0.0, 0.0, 1.0], dtype=np.float32),
        )
        src_lines: list[np.ndarray] = []
        for i in range(len(offsets) - 1):
            v = coords[offsets[i] : offsets[i + 1]]
            if v.shape[0] == 0:
                continue
            pieces = _clip_polyhedron_triangle(v, normals_tri, center)
            for p in pieces:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32))

        # 入力（ソース）から複製（回転群）
        for p in src_lines:
            p_local = p - center
            for M in mats_u:
                q = (p_local @ M.T) + center
                out_lines.append(q.astype(np.float32))
        if use_reflection:
            # 代表反射（y=0）を追加して倍化（不正規な回転を含む）
            Ry = _reflect_matrix(np.array([0.0, 1.0, 0.0], dtype=np.float32))
            extra: list[np.ndarray] = []
            for ln in out_lines:
                ln_local = ln - center
                extra.append((ln_local @ Ry.T + center).astype(np.float32))
            out_lines.extend(extra)

    uniq = _dedup_lines(out_lines)

    # 可視化: 対象面のクロス線を追加
    if show_planes:
        if uniq:
            all_pts = np.vstack(uniq).astype(np.float32)
        else:
            all_pts = coords.astype(np.float32, copy=True)
        if all_pts.size == 0:
            r = 1.0
        else:
            d = all_pts - center
            r = float(np.sqrt(np.max(np.sum(d * d, axis=1))))
            if not np.isfinite(r) or r <= 0.0:
                r = 1.0
        r *= 1.05

        def _plane_cross_segments(n: np.ndarray) -> list[np.ndarray]:
            n = _unit(n)
            ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
            if abs(float(np.dot(n, ref))) > 0.95:
                ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
            u = _unit(np.cross(n, ref))
            v = _unit(np.cross(n, u))
            p0 = center - r * u
            p1 = center + r * u
            q0 = center - r * v
            q1 = center + r * v
            return [np.vstack([p0, p1]).astype(np.float32), np.vstack([q0, q1]).astype(np.float32)]

        plane_lines: list[np.ndarray] = []
        if mode == "azimuth":
            phi0_rad = float(np.deg2rad(phi0))
            n0, n1 = _compute_azimuth_plane_normals(int(max(1, n_azimuth)), ax, phi0_rad)
            plane_lines.extend(_plane_cross_segments(n0))
            plane_lines.extend(_plane_cross_segments(n1))
            if mirror_equator:
                plane_lines.extend(_plane_cross_segments(ax))
        else:
            for n in (
                np.array([1.0, 0.0, 0.0], dtype=np.float32),
                np.array([0.0, 1.0, 0.0], dtype=np.float32),
                np.array([0.0, 0.0, 1.0], dtype=np.float32),
            ):
                plane_lines.extend(_plane_cross_segments(n))
        if plane_lines:
            uniq.extend(plane_lines)
    if not uniq:
        return Geometry(coords.copy(), offsets.copy())
    all_coords = np.vstack(uniq).astype(np.float32)
    new_offsets = np.zeros(len(uniq) + 1, dtype=np.int32)
    acc = 0
    for i, ln in enumerate(uniq, start=1):
        acc += ln.shape[0]
        new_offsets[i] = acc
    return Geometry(all_coords, new_offsets)


__all__ = ["mirror3d"]


mirror3d.__param_meta__ = PARAM_META


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return v.astype(np.float32)
    return (v / n).astype(np.float32)


def _rotate_around_axis(
    points: np.ndarray, axis: np.ndarray, angle: float, center: np.ndarray
) -> np.ndarray:
    if points.shape[0] == 0:
        return points
    k = _unit(axis)
    c = float(np.cos(angle))
    s = float(np.sin(angle))
    # ロドリゲスの回転公式: v' = v c + (k×v) s + k (k·v)(1-c)
    p = points.copy()
    p[:, 0] -= center[0]
    p[:, 1] -= center[1]
    p[:, 2] -= center[2]
    v = p
    kv = np.cross(k, v)
    kdotv = np.dot(v, k)
    v_rot = v * c + kv * s + np.outer(kdotv, k) * (1.0 - c)
    v_rot[:, 0] += center[0]
    v_rot[:, 1] += center[1]
    v_rot[:, 2] += center[2]
    return v_rot.astype(np.float32)


def _reflect_across_plane(points: np.ndarray, normal: np.ndarray, center: np.ndarray) -> np.ndarray:
    n = _unit(normal)
    p = points.copy()
    p[:, 0] -= center[0]
    p[:, 1] -= center[1]
    p[:, 2] -= center[2]
    proj = np.dot(p, n).reshape(-1, 1)  # (N,1)
    p_ref = p - 2.0 * proj * n
    p_ref[:, 0] += center[0]
    p_ref[:, 1] += center[1]
    p_ref[:, 2] += center[2]
    return p_ref.astype(np.float32)


def _reflect_matrix(normal: np.ndarray) -> np.ndarray:
    """平面法線 `n` による 3×3 の反射行列（原点基準）。"""
    n = _unit(normal).reshape(3, 1).astype(np.float32)
    I = np.eye(3, dtype=np.float32)
    return (I - 2.0 * (n @ n.T)).astype(np.float32)


def _clip_polyline_halfspace_3d(
    vertices: np.ndarray, *, normal: np.ndarray, center: np.ndarray
) -> list[np.ndarray]:
    """3D 半空間でクリップ（内側: n·(p-c) >= -EPS）。"""
    nrm = _unit(normal)
    n = vertices.shape[0]
    if n == 0:
        return []
    if n == 1:
        s = float(np.dot(vertices[0] - center, nrm))
        ok = s >= (-EPS if INCLUDE_BOUNDARY else EPS)
        return [vertices.copy()] if ok else []

    out: list[np.ndarray] = []
    cur: list[np.ndarray] = []
    a = vertices[0].astype(np.float32)
    sA = float(np.dot(a - center, nrm))
    inA = sA >= (-EPS if INCLUDE_BOUNDARY else EPS)
    if inA:
        cur.append(a)
    for i in range(1, n):
        b = vertices[i].astype(np.float32)
        sB = float(np.dot(b - center, nrm))
        inB = sB >= (-EPS if INCLUDE_BOUNDARY else EPS)
        if inA and inB:
            cur.append(b)
        elif inA and not inB:
            denom = sA - sB
            t = 0.0 if abs(denom) < 1e-20 else sA / (sA - sB)
            t = min(max(t, 0.0), 1.0)
            p = a + (b - a) * np.float32(t)
            if len(cur) == 0 or not np.allclose(cur[-1], p, atol=EPS):
                cur.append(p)
            if len(cur) >= 1:
                out.append(np.vstack(cur).astype(np.float32))
            cur = []
        elif (not inA) and inB:
            denom = sA - sB
            t = 0.0 if abs(denom) < 1e-20 else sA / (sA - sB)
            t = min(max(t, 0.0), 1.0)
            p = a + (b - a) * np.float32(t)
            cur = [p, b]
        else:
            pass
        a, sA, inA = b, sB, inB
    if cur:
        out.append(np.vstack(cur).astype(np.float32))
    return out


def _basis_perp_axis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = _unit(axis)
    # 任意の基準ベクトル（軸とほぼ平行を避ける）
    ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    if abs(float(np.dot(a, ref))) > 0.95:
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    b0 = _unit(np.cross(a, ref))
    b1 = _unit(np.cross(a, b0))
    return b0, b1


def _compute_azimuth_plane_normals(
    n_azimuth: int, axis: np.ndarray, phi0: float
) -> tuple[np.ndarray, np.ndarray]:
    delta = np.pi / float(n_azimuth)
    b0, b1 = _basis_perp_axis(axis)
    u0 = np.cos(phi0) * b0 + np.sin(phi0) * b1
    u1 = np.cos(phi0 + delta) * b0 + np.sin(phi0 + delta) * b1
    n0 = _unit(np.cross(axis, u0))
    n1 = _unit(np.cross(axis, u1))
    return n0, n1


def _equator_normal(axis: np.ndarray) -> np.ndarray:
    """赤道面（軸に垂直な平面）の法線。軸そのものを単位化して返す。"""
    return _unit(axis)


def _clip_polyline_wedge(
    vertices: np.ndarray,
    *,
    n0: np.ndarray,
    n1: np.ndarray,
    center: np.ndarray,
) -> list[np.ndarray]:
    """2 枚の境界面（n0 と n1 の大円）で挟まれたくさび領域でクリップ。"""
    pieces = _clip_polyline_halfspace_3d(vertices, normal=n0, center=center)
    res: list[np.ndarray] = []
    for p in pieces:
        res.extend(_clip_polyline_halfspace_3d(p, normal=-n1, center=center))
    return res


def _clip_polyhedron_triangle(
    vertices: np.ndarray, normals: tuple[np.ndarray, np.ndarray, np.ndarray], center: np.ndarray
) -> list[np.ndarray]:
    """3 半空間（n1,n2,n3 の AND）でクリップして三角領域内の断片を返す。"""
    n1, n2, n3 = normals
    pieces = _clip_polyline_halfspace_3d(vertices, normal=n1, center=center)
    tmp: list[np.ndarray] = []
    for p in pieces:
        tmp.extend(_clip_polyline_halfspace_3d(p, normal=n2, center=center))
    res: list[np.ndarray] = []
    for p in tmp:
        res.extend(_clip_polyline_halfspace_3d(p, normal=n3, center=center))
    return res


def _coxeter_m_ij(group: str) -> tuple[int, int, int]:
    g = group.upper()
    if g == "T":  # A3
        return 3, 3, 2
    if g == "O":  # B3
        return 3, 4, 2
    if g == "I":  # H3
        return 3, 5, 2
    raise ValueError(f"未知の group: {group}")


def _coxeter_normals(group: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """コクセター角から 3 平面の法線（単位）を構成（連鎖 1-2-3 を仮定）。"""
    m12, m23, m13 = _coxeter_m_ij(group)
    th12 = np.pi / float(m12)
    th23 = np.pi / float(m23)
    th13 = np.pi / float(m13)
    c12, s12 = float(np.cos(th12)), float(np.sin(th12))
    c23 = float(np.cos(th23))
    c13 = float(np.cos(th13))
    # n1=[1,0,0], n2=[c12,s12,0], n3=[a,b,c] を解く
    a = c13
    if abs(s12) < 1e-12:
        raise ValueError("無効なコクセター角（s12=0）")
    b = (c23 - a * c12) / s12
    c_sq = 1.0 - a * a - b * b
    if c_sq < -1e-6:
        raise ValueError("法線構成に失敗（c^2<0）")
    c = float(np.sqrt(max(0.0, c_sq)))
    n1 = _unit(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    n2 = _unit(np.array([c12, s12, 0.0], dtype=np.float32))
    n3 = _unit(np.array([a, b, c], dtype=np.float32))
    return n1, n2, n3


def _generate_reflection_group(
    normals: tuple[np.ndarray, np.ndarray, np.ndarray], *, use_reflection: bool
) -> list[np.ndarray]:
    """反射生成元から有限群（A3/B3/H3）を BFS で構成し行列集合を返す。"""
    n1, n2, n3 = normals
    gens = [_reflect_matrix(n1), _reflect_matrix(n2), _reflect_matrix(n3)]
    I = np.eye(3, dtype=np.float32)
    seen: dict[tuple, np.ndarray] = {}
    frontier: list[np.ndarray] = [I]
    keyI = tuple(np.rint(I.flatten() * (1.0 / EPS)).astype(np.int64).tolist())
    seen[keyI] = I
    # BFS で右から生成子を掛けて閉包
    while frontier:
        cur = frontier.pop()
        for G in gens:
            M = (cur @ G).astype(np.float32)
            key = tuple(np.rint(M.flatten() * (1.0 / EPS)).astype(np.int64).tolist())
            if key in seen:
                continue
            seen[key] = M
            frontier.append(M)
            # 安全弁（過剰ループ防止）
            if len(seen) > 128:  # H3 の 120 + α 程度
                break
        if len(seen) > 128:
            break
    mats = list(seen.values())
    if not use_reflection:
        mats = [M for M in mats if float(np.linalg.det(M)) > 0.0]
    return mats


def _dedup_lines(lines: Iterable[np.ndarray]) -> list[np.ndarray]:
    seen: set[tuple] = set()
    out: list[np.ndarray] = []
    inv = 1.0 / EPS if EPS > 0 else 1e6
    for ln in lines:
        if ln.shape[0] == 0:
            continue
        q = np.rint(ln * inv).astype(np.int64)
        key = (q.shape[0],) + tuple(q.flatten().tolist())
        if key in seen:
            continue
        seen.add(key)
        out.append(ln.astype(np.float32))
    return out
