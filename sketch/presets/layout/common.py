"""
Purpose:
    composableなlayout guide preset群で共有する矩形・軸・分割規則を一つの幾何契約へ集約する。
Use when:
    複数layout presetにまたがるmargin、axes、grid、ratio lineの意味を変更するとき。
Constraints:
    - 全helperはcanvas左上基準の同じrect表現とoffsetを共有する。
    - axes指定はvertical/horizontal出力だけを制御し、非選択方向へ線を追加しない。
    - marginや分割が退化してもrect端の順序を反転させず、空出力は零長geometryで表す。
    - META_COMMONのkeyと意味を利用する全layout presetで同期させる。
"""

from __future__ import annotations

from collections.abc import Mapping
from bisect import bisect_left
import math
from typing import cast

from grafix import G
from grafix.core.geometry import Geometry

CANVAS_SIZE = (148, 210)  # A5 (mm)

_GOLDEN_F = (math.sqrt(5.0) - 1.0) / 2.0  # 0.618...
_GOLDEN_T = 1.0 - _GOLDEN_F  # 0.382...

META_COMMON: dict[str, Mapping[str, object]] = {
    "canvas_w": {
        "kind": "float",
        "ui_min": 10.0,
        "ui_max": 1000.0,
        "description": "ガイドを構成するキャンバス矩形の横幅を指定する。",
    },
    "canvas_h": {
        "kind": "float",
        "ui_min": 10.0,
        "ui_max": 1000.0,
        "description": "ガイドを構成するキャンバス矩形の高さを指定する。",
    },
    "axes": {
        "kind": "choice",
        "choices": ["both", "vertical", "horizontal"],
        "description": "縦線と横線の両方、またはいずれか一方向だけを描く。",
    },
    "margin_l": {
        "kind": "float",
        "ui_min": 0.0,
        "ui_max": 100.0,
        "description": "ガイド対象矩形をキャンバス左端から内側へずらす距離を指定する。",
    },
    "margin_r": {
        "kind": "float",
        "ui_min": 0.0,
        "ui_max": 100.0,
        "description": "ガイド対象矩形をキャンバス右端から内側へずらす距離を指定する。",
    },
    "margin_t": {
        "kind": "float",
        "ui_min": 0.0,
        "ui_max": 100.0,
        "description": "ガイド対象矩形をキャンバス上端から内側へずらす距離を指定する。",
    },
    "margin_b": {
        "kind": "float",
        "ui_min": 0.0,
        "ui_max": 100.0,
        "description": "ガイド対象矩形をキャンバス下端から内側へずらす距離を指定する。",
    },
    "show_center": {
        "kind": "bool",
        "description": "ガイド対象矩形の中心を通る縦横線を追加する。",
    },
    "offset": {
        "kind": "vec3",
        "ui_min": -50.0,
        "ui_max": 50.0,
        "description": "ガイド全体をキャンバス原点から移動する量を指定する。",
    },
}


def _axes_flags(axes: str) -> tuple[bool, bool]:
    if axes == "vertical":
        return True, False
    if axes == "horizontal":
        return False, True
    return True, True


def _v_line(*, x: float, y0: float, y1: float, z: float) -> object:
    return G.line(
        center=(float(x), 0.5 * (float(y0) + float(y1)), float(z)),
        length=abs(float(y1) - float(y0)),
        angle=90.0,
    )


def _h_line(*, y: float, x0: float, x1: float, z: float) -> object:
    return G.line(
        center=(0.5 * (float(x0) + float(x1)), float(y), float(z)),
        length=abs(float(x1) - float(x0)),
        angle=0.0,
    )


def _line_between(*, x0: float, y0: float, x1: float, y1: float, z: float) -> object:
    dx = float(x1) - float(x0)
    dy = float(y1) - float(y0)
    length = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(dy, dx)) if length > 0.0 else 0.0
    return G.line(
        center=(0.5 * (float(x0) + float(x1)), 0.5 * (float(y0) + float(y1)), float(z)),
        length=float(length),
        angle=float(angle),
    )


def _concat(geoms: list[object]) -> object:
    if not geoms:
        raise ValueError("空の Geometry 連結はできません")
    out = geoms[0]
    for g in geoms[1:]:
        out = cast(Geometry, out) + cast(Geometry, g)
    return out


def _empty_geometry(*, offset: tuple[float, float, float]) -> object:
    ox, oy, oz = offset
    return G.line(center=(float(ox), float(oy), float(oz)), length=0.0, angle=0.0)


def _finish(*, geoms: list[object], offset: tuple[float, float, float]) -> object:
    if not geoms:
        return _empty_geometry(offset=offset)
    return _concat(geoms)


def _has_margin(
    *,
    margin_l: float,
    margin_r: float,
    margin_t: float,
    margin_b: float,
) -> bool:
    return any(
        float(v) != 0.0
        for v in (
            margin_l,
            margin_r,
            margin_t,
            margin_b,
        )
    )


def _rect_from_canvas(
    *,
    canvas_w: float,
    canvas_h: float,
    offset: tuple[float, float, float],
) -> tuple[float, float, float, float]:
    ox, oy, _oz = offset
    x0 = float(ox)
    y0 = float(oy)
    x1 = float(ox) + float(canvas_w)
    y1 = float(oy) + float(canvas_h)
    return (x0, y0, x1, y1)


def _inset_rect(
    rect: tuple[float, float, float, float],
    *,
    left: float,
    right: float,
    top: float,
    bottom: float,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    x0 = float(x0) + float(left)
    x1 = float(x1) - float(right)
    y0 = float(y0) + float(top)
    y1 = float(y1) - float(bottom)
    if x1 < x0:
        mid = 0.5 * (x0 + x1)
        x0 = mid
        x1 = mid
    if y1 < y0:
        mid = 0.5 * (y0 + y1)
        y0 = mid
        y1 = mid
    return (float(x0), float(y0), float(x1), float(y1))


def _rect_outline(
    rect: tuple[float, float, float, float],
    *,
    axes: str,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    show_v, show_h = _axes_flags(axes)
    out: list[object] = []
    if show_h:
        out.append(_h_line(y=y0, x0=x0, x1=x1, z=z))
        out.append(_h_line(y=y1, x0=x0, x1=x1, z=z))
    if show_v:
        out.append(_v_line(x=x0, y0=y0, y1=y1, z=z))
        out.append(_v_line(x=x1, y0=y0, y1=y1, z=z))
    return out


def _center_lines(
    rect: tuple[float, float, float, float],
    *,
    axes: str,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    show_v, show_h = _axes_flags(axes)
    w = float(x1 - x0)
    h = float(y1 - y0)
    out: list[object] = []
    if show_v:
        out.append(_v_line(x=float(x0) + 0.5 * w, y0=y0, y1=y1, z=z))
    if show_h:
        out.append(_h_line(y=float(y0) + 0.5 * h, x0=x0, x1=x1, z=z))
    return out


def _square_grid(
    rect: tuple[float, float, float, float],
    *,
    cell_size: float,
    axes: str,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    cell = float(cell_size)
    if cell <= 0.0:
        cell = 1.0

    show_v, show_h = _axes_flags(axes)
    out: list[object] = []

    if show_v:
        x = float(x0)
        while x <= float(x1) + 1e-9:
            out.append(_v_line(x=x, y0=y0, y1=y1, z=z))
            x += cell
    if show_h:
        y = float(y0)
        while y <= float(y1) + 1e-9:
            out.append(_h_line(y=y, x0=x0, x1=x1, z=z))
            y += cell

    return out


def _insert_position(
    positions: list[float],
    *,
    value: float,
    min_spacing: float,
) -> bool:
    if min_spacing <= 0.0:
        positions.append(float(value))
        return True

    v = float(value)
    i = bisect_left(positions, v)
    if i > 0 and abs(v - positions[i - 1]) < min_spacing:
        return False
    if i < len(positions) and abs(positions[i] - v) < min_spacing:
        return False
    positions.insert(i, v)
    return True


def _ratio_positions(
    *,
    length: float,
    ratio: float,
    levels: int,
    min_spacing: float,
    max_lines: int,
) -> list[float]:
    length = float(length)
    ratio = float(ratio)
    if ratio <= 1.0:
        ratio = 1.0001
    levels = int(levels)
    if levels < 1:
        levels = 1
    min_spacing = float(min_spacing)
    max_lines = int(max_lines)

    f = 1.0 / ratio
    segs: list[tuple[float, float]] = [(0.0, length)]
    positions: list[float] = []
    for _ in range(levels):
        next_segs: list[tuple[float, float]] = []
        for a, b in segs:
            span = float(b - a)
            p1 = a + span * f
            p2 = b - span * f
            lo, hi = (p1, p2) if p1 <= p2 else (p2, p1)

            # ratio によっては 2 本がほぼ同一点になる（ratio≈2 など）ので、その場合は 1 本に潰す。
            if abs(float(hi) - float(lo)) < 1e-9:
                _insert_position(positions, value=lo, min_spacing=min_spacing)
                if max_lines > 0 and len(positions) >= max_lines:
                    return positions
                next_segs.append((a, lo))
                next_segs.append((lo, b))
            else:
                _insert_position(positions, value=lo, min_spacing=min_spacing)
                _insert_position(positions, value=hi, min_spacing=min_spacing)
                if max_lines > 0 and len(positions) >= max_lines:
                    return positions
                next_segs.append((a, lo))
                next_segs.append((lo, hi))
                next_segs.append((hi, b))
        segs = next_segs
        if max_lines > 0 and len(positions) >= max_lines:
            return positions
    return positions


def _ratio_lines(
    *,
    rect: tuple[float, float, float, float],
    ratio: float,
    levels: int,
    axes: str,
    z: float,
    min_spacing: float,
    max_lines: int,
) -> list[object]:
    x0, y0, x1, y1 = rect
    w = float(x1 - x0)
    h = float(y1 - y0)
    show_v, show_h = _axes_flags(axes)

    out: list[object] = []
    if show_v:
        for x in _ratio_positions(
            length=w,
            ratio=ratio,
            levels=levels,
            min_spacing=min_spacing,
            max_lines=max_lines,
        ):
            out.append(_v_line(x=float(x0) + float(x), y0=y0, y1=y1, z=z))
    if show_h:
        for y in _ratio_positions(
            length=h,
            ratio=ratio,
            levels=levels,
            min_spacing=min_spacing,
            max_lines=max_lines,
        ):
            out.append(_h_line(y=float(y0) + float(y), x0=x0, x1=x1, z=z))
    return out


def _metallic_mean(n: int) -> float:
    """貴金属比（metallic mean）を返す。"""
    n = int(n)
    if n < 1:
        n = 1
    return 0.5 * (float(n) + math.sqrt(float(n) * float(n) + 4.0))


def _fit_rect(*, w: float, h: float, ratio: float) -> tuple[float, float]:
    w = float(w)
    h = float(h)
    ratio = float(ratio)
    if w <= 0.0 or h <= 0.0:
        return (0.0, 0.0)
    if ratio <= 1.0:
        ratio = 1.0001

    # 最大面積の「内接」矩形を選ぶ（w/h = ratio）。
    cand1 = (w, w / ratio)
    cand2 = (h * ratio, h)

    ok1 = cand1[1] <= h + 1e-9
    ok2 = cand2[0] <= w + 1e-9
    if ok1 and ok2:
        a1 = cand1[0] * cand1[1]
        a2 = cand2[0] * cand2[1]
        return cand1 if a1 >= a2 else cand2
    if ok1:
        return cand1
    if ok2:
        return cand2
    return (min(w, cand2[0]), min(h, cand1[1]))


def _metallic_rectangles(
    *,
    rect: tuple[float, float, float, float],
    metallic_n: int,
    levels: int,
    axes: str,
    corner: str,
    clockwise: bool,
    z: float,
) -> list[object]:
    x0_rect, y0_rect, x1_rect, y1_rect = rect
    canvas_w = float(x1_rect - x0_rect)
    canvas_h = float(y1_rect - y0_rect)
    show_v, show_h = _axes_flags(axes)

    n = int(metallic_n)
    if n < 1:
        n = 1
    levels = int(levels)
    if levels < 1:
        levels = 1

    ratio = _metallic_mean(n)
    rect_w, rect_h = _fit_rect(w=canvas_w, h=canvas_h, ratio=ratio)
    if rect_w <= 0.0 or rect_h <= 0.0:
        return []

    # 内接する比率矩形を corner にアンカーする（余白は corner と反対側へ出る）。
    rw = float(rect_w)
    rh = float(rect_h)
    if corner == "tl":
        x0, x1 = float(x0_rect), float(x0_rect) + rw
        y0, y1 = float(y0_rect), float(y0_rect) + rh
    elif corner == "tr":
        x0, x1 = float(x1_rect) - rw, float(x1_rect)
        y0, y1 = float(y0_rect), float(y0_rect) + rh
    elif corner == "br":
        x0, x1 = float(x1_rect) - rw, float(x1_rect)
        y0, y1 = float(y1_rect) - rh, float(y1_rect)
    elif corner == "bl":
        x0, x1 = float(x0_rect), float(x0_rect) + rw
        y0, y1 = float(y1_rect) - rh, float(y1_rect)
    else:
        raise ValueError(f"未対応の corner です: {corner!r}")

    def next_dir(d: str) -> str:
        if clockwise:
            return {"right": "down", "down": "left", "left": "up", "up": "right"}[d]
        return {"right": "up", "up": "left", "left": "down", "down": "right"}[d]

    if clockwise:
        start_dir = {"tl": "right", "tr": "down", "br": "left", "bl": "up"}[corner]
    else:
        start_dir = {"tl": "down", "tr": "left", "br": "up", "bl": "right"}[corner]

    out: list[object] = []

    # フレーム（このパターンの基準矩形の外周）は常に描く。
    if show_h:
        out.append(_h_line(y=y0, x0=x0, x1=x1, z=z))
        out.append(_h_line(y=y1, x0=x0, x1=x1, z=z))
    if show_v:
        out.append(_v_line(x=x0, y0=y0, y1=y1, z=z))
        out.append(_v_line(x=x1, y0=y0, y1=y1, z=z))

    d = start_dir
    for _ in range(levels):
        w = float(x1 - x0)
        h = float(y1 - y0)
        if w <= 0.0 or h <= 0.0:
            break

        if w >= h:
            s = h
            if d not in {"right", "left"}:
                d = "right"
            strip = float(n) * float(s)
            if strip >= w:
                break

            if show_v:
                if d == "right":
                    for i in range(1, n + 1):
                        out.append(_v_line(x=x0 + float(i) * s, y0=y0, y1=y1, z=z))
                    x0 = x0 + strip
                else:
                    for i in range(1, n + 1):
                        out.append(_v_line(x=x1 - float(i) * s, y0=y0, y1=y1, z=z))
                    x1 = x1 - strip
            else:
                x0 = x0 + strip if d == "right" else x0
                x1 = x1 - strip if d == "left" else x1
        else:
            s = w
            if d not in {"up", "down"}:
                d = "up"
            strip = float(n) * float(s)
            if strip >= h:
                break

            if show_h:
                if d == "up":
                    for i in range(1, n + 1):
                        out.append(_h_line(y=y0 + float(i) * s, x0=x0, x1=x1, z=z))
                    y0 = y0 + strip
                else:
                    for i in range(1, n + 1):
                        out.append(_h_line(y=y1 - float(i) * s, x0=x0, x1=x1, z=z))
                    y1 = y1 - strip
            else:
                y0 = y0 + strip if d == "up" else y0
                y1 = y1 - strip if d == "down" else y1

        d = next_dir(d)

    return out


def _columns(
    rect: tuple[float, float, float, float],
    *,
    cols: int,
    gutter_x: float,
    axes: str,
    show_centers: bool,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    show_v, _show_h = _axes_flags(axes)
    if not show_v:
        return []

    cols_i = int(cols)
    if cols_i < 1:
        cols_i = 1

    w = float(x1 - x0)
    gutter = float(gutter_x)
    if gutter < 0.0:
        gutter = 0.0

    usable = w - float(cols_i - 1) * gutter
    if usable <= 0.0:
        return []
    col_w = usable / float(cols_i)

    xs: list[float] = []
    x = float(x0)
    for _ in range(cols_i):
        xs.append(x)
        xs.append(x + col_w)
        x = x + col_w + gutter

    out: list[object] = []
    for xv in xs:
        out.append(_v_line(x=xv, y0=y0, y1=y1, z=z))

    if show_centers:
        x = float(x0)
        for _ in range(cols_i):
            out.append(_v_line(x=x + 0.5 * col_w, y0=y0, y1=y1, z=z))
            x = x + col_w + gutter

    return out


def _modular(
    rect: tuple[float, float, float, float],
    *,
    cols: int,
    rows: int,
    gutter_x: float,
    gutter_y: float,
    axes: str,
    show_column_centers: bool,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    show_v, show_h = _axes_flags(axes)
    out: list[object] = []

    if show_v:
        out.extend(
            _columns(
                rect,
                cols=cols,
                gutter_x=gutter_x,
                axes="vertical",
                show_centers=show_column_centers,
                z=z,
            )
        )

    if show_h:
        rows_i = int(rows)
        if rows_i < 1:
            rows_i = 1

        h = float(y1 - y0)
        gutter = float(gutter_y)
        if gutter < 0.0:
            gutter = 0.0

        usable = h - float(rows_i - 1) * gutter
        if usable > 0.0:
            row_h = usable / float(rows_i)
            ys: list[float] = []
            y = float(y0)
            for _ in range(rows_i):
                ys.append(y)
                ys.append(y + row_h)
                y = y + row_h + gutter
            for yv in ys:
                out.append(_h_line(y=yv, x0=x0, x1=x1, z=z))

    return out


def _baseline(
    rect: tuple[float, float, float, float],
    *,
    baseline_step: float,
    baseline_offset: float,
    axes: str,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    _show_v, show_h = _axes_flags(axes)
    if not show_h:
        return []

    step = float(baseline_step)
    if step <= 0.0:
        return []

    start = float(y0) + float(baseline_offset)
    if start < float(y0):
        k = math.ceil((float(y0) - start) / step)
        start = start + float(k) * step

    out: list[object] = []
    y = float(start)
    while y <= float(y1) + 1e-9:
        out.append(_h_line(y=y, x0=x0, x1=x1, z=z))
        y += step
    return out


def _cross_mark(*, x: float, y: float, size: float, z: float) -> list[object]:
    s = float(size)
    if s <= 0.0:
        return []
    half = 0.5 * s
    return [
        _h_line(y=float(y), x0=float(x) - half, x1=float(x) + half, z=z),
        _v_line(x=float(x), y0=float(y) - half, y1=float(y) + half, z=z),
    ]


def _golden_lines(
    rect: tuple[float, float, float, float],
    *,
    axes: str,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    show_v, show_h = _axes_flags(axes)
    w = float(x1 - x0)
    h = float(y1 - y0)
    out: list[object] = []
    if show_v:
        out.append(_v_line(x=float(x0) + _GOLDEN_T * w, y0=y0, y1=y1, z=z))
        out.append(_v_line(x=float(x0) + _GOLDEN_F * w, y0=y0, y1=y1, z=z))
    if show_h:
        out.append(_h_line(y=float(y0) + _GOLDEN_T * h, x0=x0, x1=x1, z=z))
        out.append(_h_line(y=float(y0) + _GOLDEN_F * h, x0=x0, x1=x1, z=z))
    return out


def _thirds_lines(
    rect: tuple[float, float, float, float],
    *,
    axes: str,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    show_v, show_h = _axes_flags(axes)
    w = float(x1 - x0)
    h = float(y1 - y0)
    out: list[object] = []
    if show_v:
        out.append(_v_line(x=float(x0) + w / 3.0, y0=y0, y1=y1, z=z))
        out.append(_v_line(x=float(x0) + 2.0 * w / 3.0, y0=y0, y1=y1, z=z))
    if show_h:
        out.append(_h_line(y=float(y0) + h / 3.0, x0=x0, x1=x1, z=z))
        out.append(_h_line(y=float(y0) + 2.0 * h / 3.0, x0=x0, x1=x1, z=z))
    return out


def _diagonals(
    rect: tuple[float, float, float, float],
    *,
    z: float,
) -> list[object]:
    x0, y0, x1, y1 = rect
    return [
        _line_between(x0=x0, y0=y0, x1=x1, y1=y1, z=z),
        _line_between(x0=x0, y0=y1, x1=x1, y1=y0, z=z),
    ]
