"""
どこで: `src/graft/core/primitives/text.py`。テキストプリミティブの実体生成。
何を: `data/input/font/` のフォントアウトラインからテキストのポリライン列を生成する。
なぜ: 最小依存で実用的なテキスト描画（サイズ/整列/追い込み）を提供するため。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from graft.core.parameters.meta import ParamMeta
from graft.core.primitive_registry import primitive
from graft.core.realized_geometry import RealizedGeometry

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FONT_DIR = _REPO_ROOT / "data" / "input" / "font"
_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")


def _empty_geometry() -> RealizedGeometry:
    coords = np.zeros((0, 3), dtype=np.float32)
    offsets = np.zeros((1,), dtype=np.int32)
    return RealizedGeometry(coords=coords, offsets=offsets)


def _rotate_closed_polyline_start_for_fill(polyline: np.ndarray) -> np.ndarray:
    """閉ポリラインの開始点を「角に近い」位置へ回して返す。

    目的: `fill` 側の `transform_to_xy_plane` が先頭 3 点だけで法線を推定するため、
    先頭が直線（共線）になりやすい輪郭だと平面判定が不安定になる。
    ここでは polyline の点列自体は変えず、開始点だけを回して「非共線な 3 点」になりやすくする。
    """
    if polyline.ndim != 2 or polyline.shape[1] != 3:
        raise ValueError("polyline は shape (N,3) の配列である必要がある")

    n = int(polyline.shape[0])
    if n < 4:
        return polyline

    # 閉曲線のみ対象（終点が始点と一致する前提）。
    if not np.allclose(polyline[0], polyline[-1], atol=1e-6, rtol=0.0):
        return polyline

    unique = polyline[:-1]
    m = int(unique.shape[0])
    if m < 3:
        return polyline

    xy = unique[:, :2].astype(np.float64, copy=False)
    mins = np.min(xy, axis=0)
    maxs = np.max(xy, axis=0)
    diag = float(np.linalg.norm(maxs - mins))
    if not np.isfinite(diag) or diag <= 0.0:
        return polyline

    best_i = 0
    best_area2 = 0.0
    for i in range(m):
        a = xy[i]
        b = xy[(i + 1) % m]
        c = xy[(i + 2) % m]
        v1x = float(b[0] - a[0])
        v1y = float(b[1] - a[1])
        v2x = float(c[0] - a[0])
        v2y = float(c[1] - a[1])
        area2 = abs(v1x * v2y - v1y * v2x)
        if area2 > best_area2:
            best_area2 = area2
            best_i = int(i)

    # 図形スケールに対して十分な “曲がり” が無いなら回さない（ほぼ直線など）。
    eps = max(1e-12, (diag * diag) * 1e-10)
    if not np.isfinite(best_area2) or best_area2 <= eps:
        return polyline
    if best_i == 0:
        return polyline

    rotated = np.concatenate(
        [unique[best_i:], unique[:best_i], unique[best_i : best_i + 1]], axis=0
    ).astype(np.float32, copy=False)
    return rotated


class _LRU:
    """単純な上限付き LRU キャッシュ（キー: str）。"""

    def __init__(self, maxsize: int = 4096) -> None:
        self.maxsize = int(maxsize)
        self._od: "OrderedDict[str, Any]" = OrderedDict()

    def get(self, key: str) -> Any | None:
        value = self._od.get(key)
        if value is not None:
            self._od.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._od[key] = value
        self._od.move_to_end(key)
        if len(self._od) > self.maxsize:
            self._od.popitem(last=False)


_FONT_PATHS: tuple[Path, ...] | None = None


def _list_font_files() -> tuple[Path, ...]:
    """`data/input/font` 配下のフォントファイルを安定順で列挙して返す。"""
    global _FONT_PATHS
    if _FONT_PATHS is not None:
        return _FONT_PATHS

    if not _FONT_DIR.exists():
        _FONT_PATHS = tuple()
        return _FONT_PATHS

    seen: set[Path] = set()
    for ext in _FONT_EXTENSIONS:
        for fp in _FONT_DIR.glob(f"**/*{ext}"):
            try:
                resolved = fp.resolve()
            except Exception:
                continue
            if resolved.is_file():
                seen.add(resolved)

    _FONT_PATHS = tuple(sorted(seen))
    return _FONT_PATHS


def _default_font_path() -> Path:
    """既定フォントパスを返す。存在しない場合は列挙先頭を使用する。"""
    sfns = _FONT_DIR / "SFNS.ttf"
    if sfns.is_file():
        return sfns.resolve()
    fonts = _list_font_files()
    if fonts:
        return fonts[0]
    raise FileNotFoundError(f"フォントが見つかりません: {_FONT_DIR}")


def _resolve_font_path(font: str) -> Path:
    """`font` 指定を `data/input/font/` のファイルへ解決して返す。"""
    raw = str(font).strip()
    if not raw:
        return _default_font_path()

    # 1) `data/input/font/<raw>` を優先（ファイル名/相対パス想定）
    direct = (_FONT_DIR / raw)
    if direct.is_file():
        return direct.resolve()

    # 2) repo root からの相対パスを許容（例: "data/input/font/SFNS.ttf"）
    repo_rel = (_REPO_ROOT / raw)
    if repo_rel.is_file():
        try:
            resolved = repo_rel.resolve()
        except Exception:
            resolved = repo_rel
        try:
            font_dir_resolved = _FONT_DIR.resolve()
        except Exception:
            font_dir_resolved = _FONT_DIR
        if resolved == font_dir_resolved or font_dir_resolved in resolved.parents:
            return resolved

    # 3) 部分一致で探索（安定ソート済み先頭を採用）
    key = raw.lower().replace(" ", "")
    for fp in _list_font_files():
        name = fp.name.lower().replace(" ", "")
        stem = fp.stem.lower().replace(" ", "")
        if key in name or key in stem:
            return fp

    raise FileNotFoundError(
        "指定フォントを data/input/font から解決できませんでした"
        f": font={raw!r}, font_dir={_FONT_DIR}"
    )


class TextRenderer:
    """TTFont とグリフ平坦化コマンドを提供するキャッシュ。"""

    _instance: "TextRenderer | None" = None
    _fonts: dict[str, Any] = {}
    _glyph_cache = _LRU(maxsize=4096)

    def __new__(cls) -> "TextRenderer":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_font(cls, path: Path, font_index: int) -> Any:
        """TTFont を取得する（キャッシュ）。"""
        from fontTools.ttLib import TTFont  # type: ignore[import-untyped]

        idx = int(font_index)
        if idx < 0:
            idx = 0

        resolved = path.resolve()
        cache_key = f"{resolved}|{idx}"
        cached = cls._fonts.get(cache_key)
        if cached is not None:
            return cached

        if resolved.suffix.lower() == ".ttc":
            font = TTFont(resolved, fontNumber=idx)
        else:
            font = TTFont(resolved)
        cls._fonts[cache_key] = font
        return font

    @classmethod
    def get_glyph_commands(
        cls,
        *,
        char: str,
        font_path: Path,
        font_index: int,
        flat_seg_len_units: float,
    ) -> tuple:
        """平坦化済みのグリフコマンド（`RecordingPen.value` 互換タプル）を返す。"""
        from fontPens.flattenPen import FlattenPen  # type: ignore[import-untyped]
        from fontTools.pens.recordingPen import RecordingPen  # type: ignore[import-untyped]

        resolved = font_path.resolve()
        key = f"{resolved}|{int(font_index)}|{char}|{round(float(flat_seg_len_units), 6)}"
        cached = cls._glyph_cache.get(key)
        if cached is not None:
            return cached

        tt_font = cls.get_font(resolved, int(font_index))
        cmap = tt_font.getBestCmap()
        if cmap is None:
            cls._glyph_cache.set(key, tuple())
            return tuple()

        glyph_name = cmap.get(ord(char))
        if glyph_name is None:
            if char.isascii() and char.isprintable():
                glyph_name = char
            else:
                logger.warning(
                    "Character '%s' (U+%04X) not found in font '%s'",
                    char,
                    ord(char),
                    str(resolved),
                )
                cls._glyph_cache.set(key, tuple())
                return tuple()

        glyph_set = tt_font.getGlyphSet()
        glyph = glyph_set.get(glyph_name)
        if glyph is None:
            logger.warning("Glyph '%s' not found in font '%s'", glyph_name, str(resolved))
            cls._glyph_cache.set(key, tuple())
            return tuple()

        rec = RecordingPen()
        glyph.draw(rec)

        flat = RecordingPen()
        flatten_pen = FlattenPen(
            flat,
            approximateSegmentLength=float(flat_seg_len_units),
            segmentLines=True,
        )
        rec.replay(flatten_pen)

        result = tuple(flat.value)
        cls._glyph_cache.set(key, result)
        return result


TEXT_RENDERER = TextRenderer()


def _get_char_advance_em(char: str, tt_font: Any) -> float:
    """1em を 1.0 とした advance の比率を返す。"""
    if char == " ":
        try:
            space_width = tt_font["hmtx"].metrics["space"][0]  # type: ignore[index]
            return float(space_width) / float(tt_font["head"].unitsPerEm)  # type: ignore[index]
        except Exception:
            return 0.25

    cmap = tt_font.getBestCmap()
    if cmap is None:
        return 0.0
    glyph_name = cmap.get(ord(char))
    if glyph_name is None:
        return 0.0
    try:
        advance_width = tt_font["hmtx"].metrics[glyph_name][0]  # type: ignore[index]
        return float(advance_width) / float(tt_font["head"].unitsPerEm)  # type: ignore[index]
    except Exception:
        return 0.0


def _glyph_commands_to_polylines_em(
    glyph_commands: Iterable,
    *,
    units_per_em: float,
    x_em: float,
    y_em: float,
) -> list[np.ndarray]:
    """RecordingPen.value から「1em=1」の座標系でポリライン列へ変換して返す。"""
    scale = 1.0 / float(units_per_em)
    x_offset = float(x_em) * float(units_per_em)
    y_offset = float(y_em) * float(units_per_em)

    polylines: list[np.ndarray] = []
    current: list[list[float]] = []

    def flush(*, close: bool) -> None:
        nonlocal current
        if not current:
            return
        if close and len(current) > 1:
            x0, y0 = current[0]
            x1, y1 = current[-1]
            if x0 != x1 or y0 != y1:
                current.append([x0, y0])

        arr2 = np.asarray(current, dtype=np.float32)
        # フォント座標（Y+上）を描画座標（Y+下）へ反転
        arr2[:, 1] *= -1.0
        arr2[:, 0] = (arr2[:, 0] + np.float32(x_offset)) * np.float32(scale)
        arr2[:, 1] = (arr2[:, 1] + np.float32(y_offset)) * np.float32(scale)

        arr3 = np.zeros((arr2.shape[0], 3), dtype=np.float32)
        arr3[:, :2] = arr2
        if close:
            arr3 = _rotate_closed_polyline_start_for_fill(arr3)
        polylines.append(arr3)
        current = []

    for cmd_type, cmd_values in glyph_commands:
        if cmd_type == "moveTo":
            flush(close=False)
            x, y = cmd_values[0]
            current.append([float(x), float(y)])
            continue
        if cmd_type == "lineTo":
            x, y = cmd_values[0]
            current.append([float(x), float(y)])
            continue
        if cmd_type == "closePath":
            flush(close=True)
            continue

    flush(close=False)
    return polylines


def _polylines_to_realized(
    polylines: list[np.ndarray],
    *,
    center: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> RealizedGeometry:
    """ポリライン列を RealizedGeometry へ変換して返す。"""
    filtered = [p.astype(np.float32, copy=False) for p in polylines if int(p.shape[0]) >= 2]
    if not filtered:
        return _empty_geometry()

    coords = np.concatenate(filtered, axis=0).astype(np.float32, copy=False)

    offsets = np.zeros(len(filtered) + 1, dtype=np.int32)
    acc = 0
    for i, line in enumerate(filtered):
        acc += int(line.shape[0])
        offsets[i + 1] = acc

    try:
        cx, cy, cz = center
    except Exception as exc:
        raise ValueError("text の center は長さ 3 のシーケンスである必要がある") from exc
    try:
        sx, sy, sz = scale
    except Exception as exc:
        raise ValueError("text の scale は長さ 3 のシーケンスである必要がある") from exc

    cx_f, cy_f, cz_f = float(cx), float(cy), float(cz)
    sx_f, sy_f, sz_f = float(sx), float(sy), float(sz)
    if (cx_f, cy_f, cz_f) != (0.0, 0.0, 0.0) or (sx_f, sy_f, sz_f) != (
        1.0,
        1.0,
        1.0,
    ):
        center_vec = np.array([cx_f, cy_f, cz_f], dtype=np.float32)
        scale_vec = np.array([sx_f, sy_f, sz_f], dtype=np.float32)
        coords = coords * scale_vec + center_vec

    return RealizedGeometry(coords=coords, offsets=offsets)


text_meta = {
    "text": ParamMeta(kind="str"),
    "font": ParamMeta(kind="str"),
    "font_index": ParamMeta(kind="int", ui_min=0, ui_max=32),
    "text_align": ParamMeta(kind="choice", choices=("left", "center", "right")),
    "letter_spacing_em": ParamMeta(kind="float", ui_min=0.0, ui_max=0.5),
    "line_height": ParamMeta(kind="float", ui_min=0.8, ui_max=3.0),
    "tolerance": ParamMeta(kind="float", ui_min=0.001, ui_max=0.1),
    "center": ParamMeta(kind="vec3", ui_min=-500.0, ui_max=500.0),
    "scale": ParamMeta(kind="vec3", ui_min=0.0, ui_max=200.0),
}


@primitive(meta=text_meta)
def text(
    *,
    text: str = "HELLO",
    font: str = "SFNS.ttf",
    font_index: int | float = 0,
    text_align: str = "left",
    letter_spacing_em: float = 0.0,
    line_height: float = 1.2,
    tolerance: float = 0.01,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> RealizedGeometry:
    """フォントアウトラインからテキストのポリライン列を生成する。

    Parameters
    ----------
    text : str, optional
        描画する文字列。`\\n` 区切りで複数行を表す。
    font : str, optional
        `data/input/font/` から解決するフォント指定（ファイル名/ステム/部分一致）。
    font_index : int | float, optional
        `.ttc` の subfont 番号（0 以上）。`.ttf/.otf` では無視される。
    text_align : str, optional
        行揃え（`left|center|right`）。
    letter_spacing_em : float, optional
        文字間の追加スペーシング（em 比）。
    line_height : float, optional
        行送り（em 比）。
    tolerance : float, optional
        平坦化許容差（em 基準の近似セグメント長）。
    center : tuple[float, float, float], optional
        平行移動ベクトル (cx, cy, cz)。
    scale : tuple[float, float, float], optional
        成分ごとのスケール (sx, sy, sz)。

    Returns
    -------
    RealizedGeometry
        テキスト輪郭をポリライン列として持つ実体ジオメトリ。

    Raises
    ------
    FileNotFoundError
        `data/input/font/` からフォントを解決できない場合。

    Notes
    -----
    基準の座標系は「1em=1.0」で生成し、最後に `scale` と `center` を適用する。
    """
    fi = int(font_index)
    if fi < 0:
        fi = 0

    font_path = _resolve_font_path(font)
    tt_font = TEXT_RENDERER.get_font(font_path, fi)
    units_per_em = float(tt_font["head"].unitsPerEm)  # type: ignore[index]
    seg_len_units = max(1.0, float(tolerance) * units_per_em)

    lines = str(text).split("\n")
    polylines: list[np.ndarray] = []

    y_em = 0.0
    for li, line_str in enumerate(lines):
        width_em = 0.0
        for ch in line_str:
            width_em += _get_char_advance_em(ch, tt_font) + float(letter_spacing_em)
        if line_str:
            width_em -= float(letter_spacing_em)

        if text_align == "center":
            x_em = -width_em / 2.0
        elif text_align == "right":
            x_em = -width_em
        else:
            x_em = 0.0

        cur_x_em = x_em
        for ch in line_str:
            if ch != " ":
                cmds = TEXT_RENDERER.get_glyph_commands(
                    char=ch,
                    font_path=font_path,
                    font_index=fi,
                    flat_seg_len_units=seg_len_units,
                )
                if cmds:
                    polylines.extend(
                        _glyph_commands_to_polylines_em(
                            cmds,
                            units_per_em=units_per_em,
                            x_em=cur_x_em,
                            y_em=y_em,
                        )
                    )
            cur_x_em += _get_char_advance_em(ch, tt_font) + float(letter_spacing_em)

        if li < len(lines) - 1:
            y_em += float(line_height)

    return _polylines_to_realized(polylines, center=center, scale=scale)
