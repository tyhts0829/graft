"""
Purpose:
    3D polylineを基本領域から軸周りまたは多面体群の対称配置へ複製する。
Use when:
    planar mirrorではなく、azimuth wedge・equator反射・T/O/I対称群を扱う場合。
Constraints:
    - azimuth modeは指定軸を含むwedgeをsourceとし、任意の非ゼロaxis周りで複製する。
    - polyhedral modeはcenter相対のT/O/I回転群を使い、reflection追加と回転群を区別する。
    - 対称面上で重なる同一lineを重複出力せず、最初に生成したlineの向きと順を保つ。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import lru_cache

import numpy as np

from grafix.core.operation_authoring import effect
from grafix.core.operation_schema import UiVisiblePred
from grafix.core.parameters.meta import ParamMeta
from grafix.core.realized_geometry import GeomTuple

from grafix.core.geometry_kernels.packed import empty_packed_geometry

EPS = 1e-6
INCLUDE_BOUNDARY = True
_PACKED_POLYHEDRAL_MIN_SOURCE_LINES = 32
_PACKED_DEDUP_MIN_LINES = 64
_MODE_CHOICES = ("azimuth", "polyhedral")
_GROUP_CHOICES = ("T", "O", "I")

mirror3d_meta = {
    "mode": ParamMeta(
        kind="choice",
        choices=_MODE_CHOICES,
        description="任意軸まわりの放射対称と正多面体の回転対称から複製方式を選ぶ。",
    ),
    "n_azimuth": ParamMeta(
        kind="int",
        ui_min=1,
        ui_max=64,
        description=(
            "回転軸まわりの等分数。180 / n_azimuth 度のくさびを回転・反射し、"
            "通常は最大 2 × n_azimuth 個、赤道面反射時は最大 4 × n_azimuth 個へ複製する。"
        ),
    ),
    "center": ParamMeta(
        kind="vec3",
        ui_min=-300.0,
        ui_max=300.0,
        description="三次元の回転および反射に使用する中心点。",
    ),
    "axis": ParamMeta(
        kind="vec3",
        ui_min=-1.0,
        ui_max=1.0,
        description="放射対称で使用する回転軸の方向ベクトル。",
    ),
    "phi0": ParamMeta(
        kind="float",
        ui_min=-180.0,
        ui_max=180.0,
        description="放射対称の複製元となるくさびの開始角を度単位で指定する。",
    ),
    "mirror_equator": ParamMeta(
        kind="bool",
        description="放射対称の結果を回転軸に直交する赤道面でも反射する。",
    ),
    "source_side": ParamMeta(
        kind="bool",
        description="赤道面反射で回転軸方向の正側を複製元にする。",
    ),
    "group": ParamMeta(
        kind="choice",
        choices=_GROUP_CHOICES,
        description="正多面体対称に使う四面体、八面体、二十面体の回転群を選ぶ。",
    ),
    "use_reflection": ParamMeta(
        kind="bool",
        description="正多面体の回転対称へ代表反射を追加して複製数を倍にする。",
    ),
    "show_planes": ParamMeta(
        kind="bool",
        description="対称面を確認用の十字ラインとして出力する。",
    ),
}

def _mode_is(name: str) -> UiVisiblePred:
    def _pred(v: Mapping[str, object]) -> bool:
        return v.get("mode", "azimuth") == name

    return _pred


mirror3d_ui_visible = {
    "n_azimuth": _mode_is("azimuth"),
    "axis": _mode_is("azimuth"),
    "phi0": _mode_is("azimuth"),
    "mirror_equator": _mode_is("azimuth"),
    "source_side": lambda v: (
        v.get("mode", "azimuth") == "azimuth"
        and v.get("mirror_equator", False) is True
    ),
    "group": _mode_is("polyhedral"),
    "use_reflection": _mode_is("polyhedral"),
}


@effect(meta=mirror3d_meta, ui_visible=mirror3d_ui_visible)
def mirror3d(
    g: GeomTuple,
    *,
    mode: str = "azimuth",
    n_azimuth: int = 1,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0),
    phi0: float = 0.0,
    mirror_equator: bool = False,
    source_side: bool = True,
    group: str = "T",
    use_reflection: bool = False,
    show_planes: bool = False,
) -> GeomTuple:
    """3D 放射状ミラー（azimuth / polyhedral）。

    Parameters
    ----------
    g : tuple[np.ndarray, np.ndarray]
        入力実体ジオメトリ（coords, offsets）。
    mode : {"azimuth","polyhedral"}, default "azimuth"
        "azimuth" は回転軸を含む 2 平面でくさびを作り、回転と反射で複製する。
        "polyhedral" は多面体対称（T/O/I）の回転群で複製する。
    n_azimuth : int, default 1
        "azimuth" の等分数。くさび角は Δφ=π/n_azimuth、出力は最大 2*n_azimuth（+赤道なら倍）。
    center : tuple[float, float, float], default (0,0,0)
        回転/反射の中心。
    axis : tuple[float, float, float], default (0,0,1)
        "azimuth" の非ゼロ回転軸。内部で単位化する。
    phi0 : float, default 0.0
        くさびの開始角 [deg]（"azimuth" のみ）。
    mirror_equator : bool, default False
        赤道面（axis ⟂）でさらにミラーする（"azimuth" のみ）。
    source_side : bool, default True
        mirror_equator=True のときのソース側。True なら (axis·(p-center) >= 0) 側を採用する。
    group : {"T","O","I"}, default "T"
        "polyhedral" の回転群（T=12, O=24, I=60）。
    use_reflection : bool, default False
        "polyhedral" で代表反射（y=0）を追加して倍化する。
    show_planes : bool, default False
        対称面を可視化用の十字線として出力に追加する。

    Raises
    ------
    ValueError
        `mode="azimuth"` で `n_azimuth` が 1 未満、または `axis` がゼロの場合。
    """
    ax: np.ndarray | None = None
    if mode == "azimuth":
        if n_azimuth < 1:
            raise ValueError("mirror3d の n_azimuth は 1 以上である必要がある")
        ax_raw = np.asarray(axis, dtype=np.float64)
        axis_norm = float(np.linalg.norm(ax_raw))
        if axis_norm == 0.0:
            raise ValueError("mirror3d の axis は非ゼロベクトルである必要がある")
        ax = (ax_raw / axis_norm).astype(np.float32)

    coords, offsets = g
    if coords.shape[0] == 0:
        return coords, offsets

    c = np.asarray(center, dtype=np.float32)

    if mode == "azimuth":
        assert ax is not None
        out_lines = _mirror3d_azimuth(
            coords,
            offsets,
            n_azimuth=n_azimuth,
            center=c,
            axis=ax,
            phi0_deg=phi0,
            mirror_equator=mirror_equator,
            source_side=source_side,
        )
        if show_planes:
            out_lines.extend(
                _show_planes_azimuth(
                    out_lines=out_lines,
                    coords=coords,
                    center=c,
                    axis=ax,
                    n_azimuth=n_azimuth,
                    phi0=phi0,
                    mirror_equator=mirror_equator,
                )
            )
    else:  # mode == "polyhedral"
        out_lines = _mirror3d_polyhedral(
            coords,
            offsets,
            center=c,
            group=group,
            use_reflection=use_reflection,
        )
        if show_planes:
            out_lines.extend(
                _show_planes_polyhedral(out_lines=out_lines, coords=coords, center=c)
            )
    uniq = _dedup_lines(out_lines)
    if not uniq:
        return empty_packed_geometry()

    all_coords = np.vstack(uniq).astype(np.float32, copy=False)
    new_offsets = np.zeros((len(uniq) + 1,), dtype=np.int32)
    acc = 0
    for i, ln in enumerate(uniq, start=1):
        acc += int(ln.shape[0])
        new_offsets[i] = acc
    return all_coords, new_offsets


def _mirror3d_azimuth(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    n_azimuth: int,
    center: np.ndarray,
    axis: np.ndarray,
    phi0_deg: float,
    mirror_equator: bool,
    source_side: bool,
) -> list[np.ndarray]:
    phi0_rad = float(np.deg2rad(float(phi0_deg)))
    n0, n1 = _compute_azimuth_plane_normals(
        n_azimuth=n_azimuth, axis=axis, phi0=phi0_rad
    )

    src_lines: list[np.ndarray] = []
    for li in range(int(offsets.size) - 1):
        v = coords[int(offsets[li]) : int(offsets[li + 1])]
        if v.shape[0] == 0:
            continue
        pieces = _clip_polyline_halfspace_3d(v, normal=n0, center=center)
        tmp: list[np.ndarray] = []
        for p in pieces:
            tmp.extend(_clip_polyline_halfspace_3d(p, normal=-n1, center=center))
        for p in tmp:
            if p.shape[0] >= 1:
                src_lines.append(p.astype(np.float32, copy=False))

    step = float(2.0 * np.pi / float(n_azimuth))
    out_lines: list[np.ndarray] = []
    for p in src_lines:
        for m in range(n_azimuth):
            out_lines.append(_rotate_around_axis(p, axis, m * step, center))
        pref = _reflect_across_plane(p, n0, center)
        for m in range(n_azimuth):
            out_lines.append(_rotate_around_axis(pref, axis, m * step, center))

    if not mirror_equator:
        return out_lines

    eq_n = axis
    src_n = eq_n if source_side else -eq_n

    clipped: list[np.ndarray] = []
    for ln in out_lines:
        clipped.extend(_clip_polyline_halfspace_3d(ln, normal=src_n, center=center))

    extra: list[np.ndarray] = []
    for ln in clipped:
        extra.append(_reflect_across_plane(ln, eq_n, center))
    return clipped + extra


def _mirror3d_polyhedral(
    coords: np.ndarray,
    offsets: np.ndarray,
    *,
    center: np.ndarray,
    group: str,
    use_reflection: bool,
) -> list[np.ndarray]:
    mats = _polyhedral_rotation_mats(group)
    if not mats:
        return []

    # ソース抽出（簡易: 正の八分体 x>=cx, y>=cy, z>=cz）
    normals = (
        np.array([1.0, 0.0, 0.0], dtype=np.float32),
        np.array([0.0, 1.0, 0.0], dtype=np.float32),
        np.array([0.0, 0.0, 1.0], dtype=np.float32),
    )
    src_lines: list[np.ndarray] = []
    for li in range(int(offsets.size) - 1):
        v = coords[int(offsets[li]) : int(offsets[li + 1])]
        if v.shape[0] == 0:
            continue
        pieces = _clip_polyhedron_octant(v, normals=normals, center=center)
        for p in pieces:
            if p.shape[0] >= 1:
                src_lines.append(p.astype(np.float32, copy=False))

    out_lines = _packed_polyhedral_transforms(
        src_lines,
        mats=mats,
        center=center,
    )
    if out_lines is None:
        out_lines = []
        for p in src_lines:
            p_local = p - center
            for M in mats:
                out_lines.append(
                    (p_local @ M.T + center).astype(np.float32, copy=False)
                )

    if use_reflection and out_lines:
        # 代表反射: y=cy（ローカルでは y=0）
        Ry = _reflect_matrix(np.array([0.0, 1.0, 0.0], dtype=np.float32))
        extra: list[np.ndarray] = []
        for ln in out_lines:
            ln_local = ln - center
            extra.append((ln_local @ Ry.T + center).astype(np.float32, copy=False))
        out_lines.extend(extra)

    return out_lines


def _packed_polyhedral_transforms(
    src_lines: list[np.ndarray],
    *,
    mats: tuple[np.ndarray, ...],
    center: np.ndarray,
) -> list[np.ndarray] | None:
    """uniform な多数 line を source-major 順のまま一括変換する。"""
    if len(src_lines) < _PACKED_POLYHEDRAL_MIN_SOURCE_LINES or not mats:
        return None

    shape = src_lines[0].shape
    if (
        len(shape) != 2
        or shape[0] == 0
        or shape[1] != 3
        or any(line.shape != shape for line in src_lines)
    ):
        return None

    stacked_sources = np.stack(src_lines, axis=0)
    stacked_mats_t = np.stack([matrix.T for matrix in mats], axis=0)
    transformed = (
        np.matmul(
            (stacked_sources - center)[:, None, :, :],
            stacked_mats_t[None, :, :, :],
        )
        + center
    )
    packed = transformed.reshape(
        len(src_lines) * len(mats),
        shape[0],
        shape[1],
    )
    return [packed[index] for index in range(packed.shape[0])]


@lru_cache(maxsize=3)
def _polyhedral_rotation_mats(group: str) -> tuple[np.ndarray, ...]:
    def rotM(axis3: np.ndarray, ang: float) -> np.ndarray:
        a = _unit(axis3)
        c = float(np.cos(ang))
        s = float(np.sin(ang))
        K = np.array(
            [[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]],
            dtype=np.float32,
        )
        I3 = np.eye(3, dtype=np.float32)
        return (I3 + s * K + (1.0 - c) * (K @ K)).astype(np.float32, copy=False)

    mats: list[np.ndarray] = [np.eye(3, dtype=np.float32)]

    if group == "T":
        vaxes = [
            np.array([1.0, 1.0, 1.0], dtype=np.float32),
            np.array([-1.0, -1.0, 1.0], dtype=np.float32),
            np.array([-1.0, 1.0, -1.0], dtype=np.float32),
            np.array([1.0, -1.0, -1.0], dtype=np.float32),
        ]
        for vax in vaxes:
            mats.append(rotM(vax, 2.0 * np.pi / 3.0))
            mats.append(rotM(vax, -2.0 * np.pi / 3.0))
        caxes = [
            np.array([1.0, 0.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
            np.array([0.0, 0.0, 1.0], dtype=np.float32),
        ]
        for cax in caxes:
            mats.append(rotM(cax, np.pi))

    elif group == "O":
        for cax in (
            np.array([1.0, 0.0, 0.0], dtype=np.float32),
            np.array([0.0, 1.0, 0.0], dtype=np.float32),
            np.array([0.0, 0.0, 1.0], dtype=np.float32),
        ):
            mats.append(rotM(cax, np.pi / 2))
            mats.append(rotM(cax, -np.pi / 2))
            mats.append(rotM(cax, np.pi))

        vaxes = [
            np.array([1.0, 1.0, 1.0], dtype=np.float32),
            np.array([-1.0, -1.0, 1.0], dtype=np.float32),
            np.array([-1.0, 1.0, -1.0], dtype=np.float32),
            np.array([1.0, -1.0, -1.0], dtype=np.float32),
        ]
        for vax in vaxes:
            mats.append(rotM(vax, 2.0 * np.pi / 3.0))
            mats.append(rotM(vax, -2.0 * np.pi / 3.0))

        eaxes = [
            np.array([1.0, 1.0, 0.0], dtype=np.float32),
            np.array([1.0, -1.0, 0.0], dtype=np.float32),
            np.array([1.0, 0.0, 1.0], dtype=np.float32),
            np.array([1.0, 0.0, -1.0], dtype=np.float32),
            np.array([0.0, 1.0, 1.0], dtype=np.float32),
            np.array([0.0, 1.0, -1.0], dtype=np.float32),
        ]
        for eax in eaxes:
            mats.append(rotM(eax, np.pi))

    else:  # "I"
        phi = (1.0 + np.sqrt(5.0)) / 2.0
        verts: list[np.ndarray] = []
        for s1 in (-1.0, 1.0):
            for s2 in (-1.0, 1.0):
                verts.append(np.array([0.0, s1, s2 * phi], dtype=np.float32))
                verts.append(np.array([s1, s2 * phi, 0.0], dtype=np.float32))
                verts.append(np.array([s2 * phi, 0.0, s1], dtype=np.float32))

        axes5 = [_unit(v) for v in verts]

        axes3: list[np.ndarray] = []
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    axes3.append(_unit(np.array([sx, sy, sz], dtype=np.float32)))
        invphi = float(1.0 / phi)
        for s1 in (-1.0, 1.0):
            for s2 in (-1.0, 1.0):
                axes3.append(
                    _unit(np.array([0.0, s1 * invphi, s2 * phi], dtype=np.float32))
                )
                axes3.append(
                    _unit(np.array([s1 * invphi, s2 * phi, 0.0], dtype=np.float32))
                )
                axes3.append(
                    _unit(np.array([s2 * phi, 0.0, s1 * invphi], dtype=np.float32))
                )

        V = np.stack(verts, axis=0)
        dists = np.linalg.norm(V[None, :, :] - V[:, None, :], axis=2)
        idx = np.where(dists > 1e-6)
        pairs = list(zip(idx[0].tolist(), idx[1].tolist()))
        pairs = [(i, j) for (i, j) in pairs if i < j]
        min_d = min(dists[i, j] for (i, j) in pairs)
        tol = float(min_d) * 1.01
        edges = [(i, j) for (i, j) in pairs if float(dists[i, j]) <= tol]
        axes2 = [_unit((V[i] + V[j]) * 0.5) for (i, j) in edges]

        for a in axes5:
            for k in (1, 2, 3, 4):
                mats.append(rotM(a, 2.0 * np.pi * k / 5.0))
        for a in axes3:
            for k in (1, 2):
                mats.append(rotM(a, 2.0 * np.pi * k / 3.0))
        for a in axes2:
            mats.append(rotM(a, np.pi))

    uniq: dict[tuple[int, ...], np.ndarray] = {}
    inv = 1.0 / EPS if EPS > 0 else 1e6
    for M in mats:
        key = tuple(np.rint(M.flatten() * inv).astype(np.int64).tolist())
        uniq[key] = M.astype(np.float32, copy=False)
    result = tuple(uniq.values())
    for M in result:
        M.setflags(write=False)
    return result


def _show_planes_azimuth(
    *,
    out_lines: list[np.ndarray],
    coords: np.ndarray,
    center: np.ndarray,
    axis: np.ndarray,
    n_azimuth: int,
    phi0: float,
    mirror_equator: bool,
) -> list[np.ndarray]:
    if out_lines:
        all_pts = np.vstack(out_lines).astype(np.float32, copy=False)
    else:
        all_pts = coords.astype(np.float32, copy=False)
    r = _fit_radius(all_pts=all_pts, center=center)

    phi0_rad = float(np.deg2rad(float(phi0)))
    n0, n1 = _compute_azimuth_plane_normals(
        n_azimuth=n_azimuth,
        axis=axis,
        phi0=phi0_rad,
    )
    planes = [n0, n1]
    if mirror_equator:
        planes.append(axis)
    if r <= 0.0:
        r = 1.0
    r *= 1.05

    plane_lines: list[np.ndarray] = []
    for normal in planes:
        plane_lines.extend(_plane_cross_segments(center=center, normal=normal, r=r))
    return plane_lines


def _show_planes_polyhedral(
    *,
    out_lines: list[np.ndarray],
    coords: np.ndarray,
    center: np.ndarray,
) -> list[np.ndarray]:
    if out_lines:
        all_pts = np.vstack(out_lines).astype(np.float32, copy=False)
    else:
        all_pts = coords.astype(np.float32, copy=False)
    r = _fit_radius(all_pts=all_pts, center=center)
    if r <= 0.0:
        r = 1.0
    r *= 1.05
    plane_lines: list[np.ndarray] = []
    for normal in (
        np.array([1.0, 0.0, 0.0], dtype=np.float32),
        np.array([0.0, 1.0, 0.0], dtype=np.float32),
        np.array([0.0, 0.0, 1.0], dtype=np.float32),
    ):
        plane_lines.extend(_plane_cross_segments(center=center, normal=normal, r=r))
    return plane_lines


def _fit_radius(*, all_pts: np.ndarray, center: np.ndarray) -> float:
    if all_pts.size == 0:
        return 1.0
    d = all_pts - center
    r = float(np.sqrt(np.max(np.sum(d * d, axis=1))))
    if not np.isfinite(r) or r <= 0.0:
        return 1.0
    return r


def _plane_cross_segments(
    *, center: np.ndarray, normal: np.ndarray, r: float
) -> list[np.ndarray]:
    n = _unit(normal)
    ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    if abs(float(np.dot(n, ref))) > 0.95:
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    u = _unit(np.cross(n, ref))
    v = _unit(np.cross(n, u))
    p0 = center - r * u
    p1 = center + r * u
    q0 = center - r * v
    q1 = center + r * v
    return [
        np.vstack([p0, p1]).astype(np.float32, copy=False),
        np.vstack([q0, q1]).astype(np.float32, copy=False),
    ]


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    if n == 0.0:
        return v.astype(np.float32, copy=False)
    return (v / n).astype(np.float32, copy=False)


def _rotate_around_axis(
    points: np.ndarray, axis: np.ndarray, angle: float, center: np.ndarray
) -> np.ndarray:
    if points.shape[0] == 0:
        return points
    k = _unit(axis)
    c = float(np.cos(angle))
    s = float(np.sin(angle))

    p = points.astype(np.float32, copy=True)
    p[:, 0] -= center[0]
    p[:, 1] -= center[1]
    p[:, 2] -= center[2]
    kv = np.cross(k, p)
    kdotv = np.dot(p, k)
    v_rot = p * c + kv * s + np.outer(kdotv, k) * (1.0 - c)
    v_rot[:, 0] += center[0]
    v_rot[:, 1] += center[1]
    v_rot[:, 2] += center[2]
    return v_rot.astype(np.float32, copy=False)


def _reflect_across_plane(
    points: np.ndarray, normal: np.ndarray, center: np.ndarray
) -> np.ndarray:
    n = _unit(normal)
    p = points.astype(np.float32, copy=True)
    p[:, 0] -= center[0]
    p[:, 1] -= center[1]
    p[:, 2] -= center[2]
    proj = np.dot(p, n).reshape(-1, 1)
    p_ref = p - 2.0 * proj * n
    p_ref[:, 0] += center[0]
    p_ref[:, 1] += center[1]
    p_ref[:, 2] += center[2]
    return p_ref.astype(np.float32, copy=False)


def _reflect_matrix(normal: np.ndarray) -> np.ndarray:
    n = _unit(normal).reshape(3, 1).astype(np.float32, copy=False)
    I3 = np.eye(3, dtype=np.float32)
    return (I3 - 2.0 * (n @ n.T)).astype(np.float32, copy=False)


def _compute_azimuth_plane_normals(
    *, n_azimuth: int, axis: np.ndarray, phi0: float
) -> tuple[np.ndarray, np.ndarray]:
    delta = float(np.pi / float(n_azimuth))
    b0, b1 = _basis_perp_axis(axis)
    u0 = np.cos(phi0) * b0 + np.sin(phi0) * b1
    u1 = np.cos(phi0 + delta) * b0 + np.sin(phi0 + delta) * b1
    n0 = _unit(np.cross(axis, u0))
    n1 = _unit(np.cross(axis, u1))
    return n0, n1


def _basis_perp_axis(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = _unit(axis)
    ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    if abs(float(np.dot(a, ref))) > 0.95:
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    b0 = _unit(np.cross(a, ref))
    b1 = _unit(np.cross(a, b0))
    return b0, b1


def _clip_polyline_halfspace_3d(
    vertices: np.ndarray, *, normal: np.ndarray, center: np.ndarray
) -> list[np.ndarray]:
    """3D 半空間でポリラインをクリップする（内側: n·(p-center) >= -EPS）。"""
    nrm = _unit(normal)
    n = int(vertices.shape[0])
    if n <= 0:
        return []
    if n == 1:
        s = float(np.dot(vertices[0] - center, nrm))
        ok = s >= (-EPS if INCLUDE_BOUNDARY else EPS)
        return [vertices.astype(np.float32, copy=True)] if ok else []

    out: list[np.ndarray] = []
    cur: list[np.ndarray] = []

    a = vertices[0].astype(np.float32, copy=False)
    sA = float(np.dot(a - center, nrm))
    inA = sA >= (-EPS if INCLUDE_BOUNDARY else EPS)
    if inA:
        cur.append(a)

    for i in range(1, n):
        b = vertices[i].astype(np.float32, copy=False)
        sB = float(np.dot(b - center, nrm))
        inB = sB >= (-EPS if INCLUDE_BOUNDARY else EPS)

        if inA and inB:
            cur.append(b)
        elif inA and (not inB):
            denom = sA - sB
            t = 0.0 if abs(denom) < 1e-20 else sA / denom
            t = float(min(max(t, 0.0), 1.0))
            p = a + (b - a) * np.float32(t)
            if len(cur) == 0 or not np.allclose(cur[-1], p, atol=EPS):
                cur.append(p)
            out.append(np.vstack(cur).astype(np.float32, copy=False))
            cur = []
        elif (not inA) and inB:
            denom = sA - sB
            t = 0.0 if abs(denom) < 1e-20 else sA / denom
            t = float(min(max(t, 0.0), 1.0))
            p = a + (b - a) * np.float32(t)
            cur = [p, b]

        a, sA, inA = b, sB, inB

    if cur:
        out.append(np.vstack(cur).astype(np.float32, copy=False))
    return out


def _clip_polyhedron_octant(
    vertices: np.ndarray,
    *,
    normals: tuple[np.ndarray, np.ndarray, np.ndarray],
    center: np.ndarray,
) -> list[np.ndarray]:
    n1, n2, n3 = normals
    pieces = _clip_polyline_halfspace_3d(vertices, normal=n1, center=center)
    tmp: list[np.ndarray] = []
    for p in pieces:
        tmp.extend(_clip_polyline_halfspace_3d(p, normal=n2, center=center))
    out: list[np.ndarray] = []
    for p in tmp:
        out.extend(_clip_polyline_halfspace_3d(p, normal=n3, center=center))
    return out


def _dedup_lines(lines: Iterable[np.ndarray]) -> list[np.ndarray]:
    if isinstance(lines, list):
        packed = _dedup_uniform_lines(lines)
        if packed is not None:
            return packed

    seen: set[tuple[int, bytes]] = set()
    out: list[np.ndarray] = []
    inv = 1.0 / EPS if EPS > 0 else 1e6
    for ln in lines:
        if ln.shape[0] == 0:
            continue
        q = np.rint(ln.astype(np.float32, copy=False) * inv).astype(np.int64)
        key = (int(q.shape[0]), q.tobytes())
        if key in seen:
            continue
        seen.add(key)
        out.append(ln.astype(np.float32, copy=False))
    return out


def _dedup_uniform_lines(lines: list[np.ndarray]) -> list[np.ndarray] | None:
    """uniform な多数 line を一括量子化し、元順の first line を残す。"""
    if len(lines) < _PACKED_DEDUP_MIN_LINES:
        return None

    shape = lines[0].shape
    if (
        len(shape) != 2
        or shape[0] == 0
        or shape[1] != 3
        or any(line.shape != shape for line in lines)
    ):
        return None

    stacked = np.stack(lines, axis=0)
    inv = 1.0 / EPS if EPS > 0 else 1e6
    quantized = np.rint(stacked * inv).astype(np.int64)
    seen: set[tuple[int, bytes]] = set()
    out: list[np.ndarray] = []
    for index, line in enumerate(lines):
        key = (int(shape[0]), quantized[index].tobytes())
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


__all__ = ["mirror3d"]
