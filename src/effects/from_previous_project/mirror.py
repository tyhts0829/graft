"""
どこで: `effects`
何を: XY 直交平面に対する対象面ミラーリング（n=1/2）。
なぜ: 片側/象限をソースとして他側へ鏡映し、反対側に元からある線を削除するため。

仕様（v0）:
- n_mirror=1: 平面 x=cx。`source_side` に応じて x>=cx または x<=cx をソースとし、
  クリップした線を反対側へ鏡映。ソース外の元線は削除。z は不変。
- n_mirror=2: 平面 x=cx, y=cy。`source_side=(sx, sy)` で象限を指定し、ソース領域に
  クリップした線から残り 3 象限へ鏡映。ソース外の元線は削除。z は不変。

注意:
- クリップは半空間/象限に対し線分を分割して行う（重心判定ではない）。
- 境界は include_boundary=True で内側扱い。eps=1e-6 を比較・交点計算に使用。
- 境界上の線の鏡映による重複は頂点の量子化ハッシュで除去する。
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
    "n_mirror": {"min": 1, "max": 8, "step": 1},
    "cx": {"min": 0, "max": 1000.0, "step": 0.1},
    "cy": {"min": 0, "max": 1000.0, "step": 0.1},
    "show_planes": {"type": "bool"},
}


@effect(name="mirror")
def mirror(
    g: Geometry,
    *,
    n_mirror: int = 1,
    cx: float = 0.0,
    cy: float = 0.0,
    source_side: bool | Sequence[bool] = True,
    show_planes: bool = False,
) -> Geometry:
    """対象面ミラーリング（n=1/2）。

    Parameters
    ----------
    g : Geometry
        入力ジオメトリ（3D）。
    n_mirror : int, default 1
        1: x=cx による半空間ミラー。2: x=cx, y=cy による象限ミラー。
    cx, cy : float, default 0.0
        各対称平面の中心座標。
    source_side : bool | Sequence[bool], default True
        半空間/象限のソース側を指定。True=正側（x>=cx / y>=cy）、False=負側。
        長さ不足は循環、bool 単体は全平面に適用。
        n=1: [sx]、n=2: [sx, sy] の順で解釈。
    show_planes : bool, default False
        可視化用に対象面（x=cx, y=cy など）を線として出力へ含める。
        n=1 では x=cx の鉛直線、n=2 では x=cx と y=cy の直交線を追加する。
        n>=3 では中心 (cx,cy) を通る 2n 本の放射状の線（セクタ境界）を追加する。

    Notes
    -----
    許容誤差は EPS=1e-6、境界は INCLUDE_BOUNDARY=True 固定で運用する。
    """
    if n_mirror < 1:
        raise ValueError("n_mirror は 1 以上の整数である必要があります。")

    def _rotate(points: np.ndarray, ang: float, cx: float, cy: float) -> np.ndarray:
        if points.shape[0] == 0:
            return points
        c, s = float(np.cos(ang)), float(np.sin(ang))
        out = points.copy()
        out[:, 0] -= cx
        out[:, 1] -= cy
        x = out[:, 0].copy()
        y = out[:, 1].copy()
        new_x = x * c - y * s
        new_y = x * s + y * c
        out[:, 0] = new_x
        out[:, 1] = new_y
        out[:, 0] += cx
        out[:, 1] += cy
        return out

    sx_sy = _normalize_source_side(n_mirror, source_side)
    coords, offsets = g.as_arrays(copy=False)
    src_lines: list[np.ndarray] = []

    if n_mirror == 1:
        sx = sx_sy[0]
        for i in range(len(offsets) - 1):
            v = coords[offsets[i] : offsets[i + 1]]
            if v.shape[0] == 0:
                continue
            pieces = _clip_polyline_halfspace(v, axis=0, thresh=float(cx), side=sx)
            for p in pieces:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32))

        # 鏡映生成（x）
        out_lines: list[np.ndarray] = []
        for p in src_lines:
            out_lines.append(p)
            out_lines.append(_reflect_x(p, float(cx)))

    elif n_mirror == 2:
        sx = sx_sy[0]
        sy = sx_sy[1] if len(sx_sy) > 1 else 1
        for i in range(len(offsets) - 1):
            v = coords[offsets[i] : offsets[i + 1]]
            if v.shape[0] == 0:
                continue
            pieces = _clip_polyline_quadrant(v, cx=float(cx), cy=float(cy), sx=int(sx), sy=int(sy))
            for p in pieces:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32))

        # 鏡映生成（x / y / x+y）
        out_lines = []
        for p in src_lines:
            out_lines.append(p)
            px = _reflect_x(p, float(cx))
            py = _reflect_y(p, float(cy))
            pxy = _reflect_y(px, float(cy))
            out_lines.extend([px, py, pxy])

    else:  # n_mirror >= 3
        n = int(n_mirror)
        delta = np.pi / n
        # 楔 [0, delta) をソース領域とする（中心 (cx,cy) 原点基準）
        n0 = np.array([0.0, 1.0], dtype=np.float32)  # θ=0 の法線（上側が +）
        n1 = np.array([-np.sin(delta), np.cos(delta)], dtype=np.float32)  # θ=delta の法線

        # half-plane: n•(p-c) >= 0 を満たす側。2 枚目は反対側（<=0）なので -n1 を用いる。
        def _clip_halfplane_general(vertices: np.ndarray, normal: np.ndarray) -> list[np.ndarray]:
            cxy = np.array([cx, cy], dtype=np.float32)
            nrm = normal.astype(np.float32)
            nrm /= np.linalg.norm(nrm) if np.linalg.norm(nrm) > 0 else 1.0
            npts = vertices.shape[0]
            if npts == 0:
                return []
            if npts == 1:
                s = float(np.dot(vertices[0, :2] - cxy, nrm))
                ok = s >= (-EPS if INCLUDE_BOUNDARY else EPS)
                return [vertices.copy()] if ok else []
            out_segs: list[np.ndarray] = []
            cur: list[np.ndarray] = []
            a = vertices[0].astype(np.float32)
            sA = float(np.dot(a[:2] - cxy, nrm))
            inA = sA >= (-EPS if INCLUDE_BOUNDARY else EPS)
            if inA:
                cur.append(a)
            for j in range(1, npts):
                b = vertices[j].astype(np.float32)
                sB = float(np.dot(b[:2] - cxy, nrm))
                inB = sB >= (-EPS if INCLUDE_BOUNDARY else EPS)
                if inA and inB:
                    cur.append(b)
                elif inA and not inB:
                    # 出るとき: 線分交点
                    denom = sA - sB
                    t = 0.0 if abs(denom) < 1e-20 else sA / (sA - sB)
                    t = min(max(t, 0.0), 1.0)
                    p = a + (b - a) * np.float32(t)
                    if len(cur) == 0 or not np.allclose(cur[-1], p, atol=EPS):
                        cur.append(p)
                    if len(cur) >= 1:
                        out_segs.append(np.vstack(cur).astype(np.float32))
                    cur = []
                elif (not inA) and inB:
                    denom = sA - sB
                    t = 0.0 if abs(denom) < 1e-20 else sA / (sA - sB)
                    t = min(max(t, 0.0), 1.0)
                    p = a + (b - a) * np.float32(t)
                    cur = [p, b]
                else:
                    # 外→外
                    pass
                a, sA, inA = b, sB, inB
            if cur:
                out_segs.append(np.vstack(cur).astype(np.float32))
            return out_segs

        for i in range(len(offsets) - 1):
            v = coords[offsets[i] : offsets[i + 1]]
            if v.shape[0] == 0:
                continue
            pieces = _clip_halfplane_general(v, n0)
            tmp: list[np.ndarray] = []
            for p in pieces:
                tmp.extend(_clip_halfplane_general(p, -n1))
            for p in tmp:
                if p.shape[0] >= 1:
                    src_lines.append(p.astype(np.float32))

        # 2n 個の複製: 回転 n 個 + 反転後回転 n 個
        out_lines = []
        step = 2.0 * np.pi / n
        for p in src_lines:
            # 回転（非反転）
            for m in range(n):
                out_lines.append(_rotate(p, m * step, float(cx), float(cy)))
            # 反転（θ=0 で反転 = y 軸方向に関して反転）→ 回転
            pref = _reflect_y(p, float(cy))
            for m in range(n):
                out_lines.append(_rotate(pref, m * step, float(cx), float(cy)))

    # 重複除去
    uniq = _dedup_lines(out_lines)

    # 対象面の可視化（必要に応じて末尾に追加）
    if show_planes:
        # 既存ラインのバウンディング（出力想定範囲）。出力が空の場合は入力から推定。
        if uniq:
            all_pts = np.vstack(uniq).astype(np.float32)
        else:
            all_pts = coords.astype(np.float32, copy=True)

        if all_pts.size == 0:
            # 最小スパン（見やすさ確保）。
            x_min, x_max = float(cx - 1.0), float(cx + 1.0)
            y_min, y_max = float(cy - 1.0), float(cy + 1.0)
        else:
            x_min0 = float(np.min(all_pts[:, 0]))
            x_max0 = float(np.max(all_pts[:, 0]))
            y_min0 = float(np.min(all_pts[:, 1]))
            y_max0 = float(np.max(all_pts[:, 1]))
            # 反射後の広がりも含めた保守的レンジ
            if n_mirror >= 1:
                x_min = min(x_min0, 2.0 * float(cx) - x_max0)
                x_max = max(x_max0, 2.0 * float(cx) - x_min0)
            else:
                x_min, x_max = x_min0, x_max0
            if n_mirror >= 2:
                y_min = min(y_min0, 2.0 * float(cy) - y_max0)
                y_max = max(y_max0, 2.0 * float(cy) - y_min0)
            else:
                y_min, y_max = y_min0, y_max0

        plane_lines: list[np.ndarray] = []

        if n_mirror == 1:
            # 垂直線 x=cx（全高さにわたり表示）。
            vline = np.array([[cx, y_min, 0.0], [cx, y_max, 0.0]], dtype=np.float32)
            plane_lines.append(vline)
        elif n_mirror == 2:
            vline = np.array([[cx, y_min, 0.0], [cx, y_max, 0.0]], dtype=np.float32)
            hline = np.array([[x_min, cy, 0.0], [x_max, cy, 0.0]], dtype=np.float32)
            plane_lines.extend([vline, hline])
        else:
            # 放射状の 2n 本（セクタ境界）。
            n = int(n_mirror)
            if n < 1:
                n = 1
            delta = np.pi / n
            step = 2.0 * np.pi / n
            # 半径: バウンディングから推定（中心からの最大距離）。
            if all_pts.size == 0:
                r = 1.0
            else:
                dx = all_pts[:, 0] - float(cx)
                dy = all_pts[:, 1] - float(cy)
                r = float(np.sqrt(np.max(dx * dx + dy * dy)))
                if not np.isfinite(r) or r <= 0.0:
                    r = 1.0
            r *= 1.05  # 端が重ならない程度に少し余白
            for k in range(n):
                for ang in (k * step, k * step + delta):
                    cth = float(np.cos(ang))
                    sth = float(np.sin(ang))
                    p0 = np.array([cx - r * cth, cy - r * sth, 0.0], dtype=np.float32)
                    p1 = np.array([cx + r * cth, cy + r * sth, 0.0], dtype=np.float32)
                    plane_lines.append(np.vstack([p0, p1]).astype(np.float32))

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


__all__ = ["mirror"]


# GUI/量子化向けの RangeHint（float のみ量子化対象）
mirror.__param_meta__ = PARAM_META


def _normalize_source_side(n_mirror: int, source_side: bool | Sequence[bool]) -> list[int]:
    """bool/シーケンスを ±1 に正規化（True→+1, False→-1）。"""
    if isinstance(source_side, (bool, np.bool_)):
        sign = 1 if bool(source_side) else -1
        return [sign for _ in range(n_mirror)]
    seq = list(source_side)
    out: list[int] = []
    for i in range(n_mirror):
        v = bool(seq[i % len(seq)])
        out.append(1 if v else -1)
    return out


def _is_inside(val: float, thresh: float, side: int) -> bool:
    d = side * (val - thresh)
    return d >= (-EPS if INCLUDE_BOUNDARY else EPS)


def _intersect_axis(a: np.ndarray, b: np.ndarray, axis: int, thresh: float) -> np.ndarray:
    # a,b: (3,), 直線分 a->b と座標平面 axis=thresh の交点
    da = float(a[axis])
    db = float(b[axis])
    denom = db - da
    if denom == 0.0:
        # 平行: a を返す（呼び出し元で扱うためのフォールバック）。
        return a.astype(np.float32)
    t = (thresh - da) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    p = a + (b - a) * np.float32(t)
    p[axis] = np.float32(thresh)
    return p.astype(np.float32)


def _clip_polyline_halfspace(
    vertices: np.ndarray,
    *,
    axis: int,
    thresh: float,
    side: int,
) -> list[np.ndarray]:
    """1 本のポリラインを軸整列半空間でクリップして部分線リストを返す。"""
    n = vertices.shape[0]
    if n == 0:
        return []
    if n == 1:
        v = vertices[0]
        return [vertices.copy()] if _is_inside(float(v[axis]), thresh, side) else []

    out: list[np.ndarray] = []
    cur: list[np.ndarray] = []
    prev = vertices[0].astype(np.float32)
    prev_in = _is_inside(float(prev[axis]), thresh, side)
    if prev_in:
        cur.append(prev)
    for i in range(1, n):
        pt = vertices[i].astype(np.float32)
        now_in = _is_inside(float(pt[axis]), thresh, side)
        if prev_in and now_in:
            # 内→内: そのまま追加
            cur.append(pt)
        elif prev_in and not now_in:
            # 内→外: 境界でクリップして閉じる
            ip = _intersect_axis(prev, pt, axis, thresh)
            if len(cur) == 0 or not np.allclose(cur[-1], ip, atol=EPS):
                cur.append(ip)
            if len(cur) >= 1:
                out.append(np.vstack(cur).astype(np.float32))
            cur = []
        elif (not prev_in) and now_in:
            # 外→内: 境界から開始
            ip = _intersect_axis(prev, pt, axis, thresh)
            cur = [ip, pt]
        else:
            # 外→外: 交差の可能性（スキップ）
            pass
        prev, prev_in = pt, now_in

    if cur:
        out.append(np.vstack(cur).astype(np.float32))
    return out


def _clip_polyline_quadrant(
    vertices: np.ndarray,
    *,
    cx: float,
    cy: float,
    sx: int,
    sy: int,
) -> list[np.ndarray]:
    # 2 つの半空間の共通部分でクリップ（順次適用）
    pieces = _clip_polyline_halfspace(vertices, axis=0, thresh=cx, side=sx)
    res: list[np.ndarray] = []
    for p in pieces:
        res.extend(_clip_polyline_halfspace(p, axis=1, thresh=cy, side=sy))
    return res


def _reflect_x(vertices: np.ndarray, cx: float) -> np.ndarray:
    r = vertices.copy()
    r[:, 0] = np.float32(2 * cx) - r[:, 0]
    return r


def _reflect_y(vertices: np.ndarray, cy: float) -> np.ndarray:
    r = vertices.copy()
    r[:, 1] = np.float32(2 * cy) - r[:, 1]
    return r


def _dedup_lines(lines: Iterable[np.ndarray]) -> list[np.ndarray]:
    """量子化ハッシュで重複ポリラインを除去。"""
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
