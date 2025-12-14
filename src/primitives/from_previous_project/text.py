from __future__ import annotations

# pyright: reportMissingImports=false

"""
どこで: `shapes.text`。
何を: フォントアウトラインからテキストのポリラインを生成する（mm 単位）。
なぜ: 実用的なテキスト描画（サイズ/整列/追い込み）を最小依存で提供するため。
"""

import json
import logging
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from numba import njit  # type: ignore[attr-defined]

from engine.core.geometry import Geometry

from .registry import shape

logger = logging.getLogger(__name__)


@njit(fastmath=True, cache=True)
def _apply_offset_scale(
    vertices: np.ndarray, x_offset: float, y_offset: float, scale: float
) -> np.ndarray:
    """xy オフセットと一様スケールを適用する。"""
    out = vertices.copy()
    out[:, 0] = (out[:, 0] + x_offset) * scale
    out[:, 1] = (out[:, 1] + y_offset) * scale
    return out


class _LRU:
    """単純な上限付き LRU キャッシュ（キー: str）。"""

    def __init__(self, maxsize: int = 4096) -> None:
        self.maxsize = int(maxsize)
        self._od: "OrderedDict[str, Any]" = OrderedDict()

    def get(self, key: str) -> Any | None:
        v = self._od.get(key)
        if v is not None:
            self._od.move_to_end(key)
        return v

    def set(self, key: str, value: Any) -> None:
        self._od[key] = value
        self._od.move_to_end(key)
        if len(self._od) > self.maxsize:
            self._od.popitem(last=False)


class TextRenderer:
    """フォントとグリフコマンドを提供するシングルトン。"""

    _instance = None
    _fonts: dict[str, Any] = {}
    _glyph_cache = _LRU(maxsize=4096)
    _font_paths: list[Path] | None = None

    EXTENSIONS = (".ttf", ".otf", ".ttc")

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_font_path_list(cls) -> list[Path]:
        """利用可能なフォントファイルのパス一覧を取得する。"""
        if cls._font_paths is None:
            from util.fonts import (  # type: ignore
                glob_font_files,
                os_font_dirs,
                resolve_search_dirs,
            )
            from util.utils import load_config  # type: ignore

            cfg = load_config() or {}
            fonts_cfg = cfg.get("fonts", {}) if isinstance(cfg, dict) else {}
            include_os = True
            try:
                include_os = bool(fonts_cfg.get("include_os", True))
            except Exception:
                include_os = True
            cfg_dirs_raw = fonts_cfg.get("search_dirs", []) if isinstance(fonts_cfg, dict) else []
            if isinstance(cfg_dirs_raw, (str, Path)):
                cfg_dirs_raw = [str(cfg_dirs_raw)]

            dirs: list[Path] = []
            try:
                dirs.extend(resolve_search_dirs(cfg_dirs_raw))
            except Exception:
                pass
            if include_os:
                try:
                    dirs.extend(os_font_dirs())
                except Exception:
                    pass
            # 列挙（重複除去/安定ソート）
            cls._font_paths = glob_font_files(dirs, cls.EXTENSIONS)
        return cls._font_paths

    @classmethod
    def _default_font_candidate(cls) -> Path | None:
        # OS 別の素直な候補
        candidates: list[Path] = []
        if sys.platform == "darwin":
            candidates = [
                Path("/System/Library/Fonts/Helvetica.ttc"),
                Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            ]
        elif sys.platform.startswith("linux"):
            candidates = [
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
            ]
        elif os.name == "nt":
            windir = os.environ.get("WINDIR", r"C:\\Windows")
            candidates = [Path(windir) / "Fonts" / "arial.ttf"]
        for c in candidates:
            if c.exists():
                return c
        # 最後に探索済み一覧の先頭
        font_paths = cls.get_font_path_list()
        return font_paths[0] if font_paths else None

    @classmethod
    def get_font(cls, font_name: str = "SFNS", font_index: int = 0) -> Any:
        """TTFont を取得（キャッシュ）。`font_name` は名前の部分一致 or パス。"""
        from fontTools.ttLib import TTFont  # type: ignore

        try:
            idx = int(font_index)
        except Exception:
            idx = 0
        if idx < 0:
            idx = 0

        cache_key = f"{font_name}|{idx}"
        cached = cls._fonts.get(cache_key)
        if cached is not None:
            return cached

        # パス指定ならそれを優先
        path = Path(font_name)
        if path.exists():
            if path.suffix.lower() == ".ttc":
                font = TTFont(path, fontNumber=idx)
            else:
                font = TTFont(path)
            cls._fonts[cache_key] = font
            return font

        # 名前の部分一致で探索
        for fp in cls.get_font_path_list():
            if font_name.lower() in fp.name.lower():
                if fp.suffix.lower() == ".ttc":
                    font = TTFont(fp, fontNumber=idx)
                else:
                    font = TTFont(fp)
                cls._fonts[cache_key] = font
                return font

        # 既定フォント
        fallback = cls._default_font_candidate()
        if fallback is None:
            raise FileNotFoundError(
                f"フォント '{font_name}' が見つからず、既定のフォントも検出できませんでした"
            )
        # フォールバック時も静かに進める（ログ出力なし）
        if fallback.suffix.lower() == ".ttc":
            font = TTFont(fallback, fontNumber=0)
        else:
            font = TTFont(fallback)
        cls._fonts[cache_key] = font
        return font

    @classmethod
    def get_glyph_commands(
        cls,
        *,
        char: str,
        font_name: str,
        font_index: int,
        flat_seg_len_units: float,
    ) -> tuple:
        """平坦化済みのグリフコマンド（`RecordingPen.value` 互換タプル）を返す。"""
        from fontPens.flattenPen import FlattenPen  # type: ignore
        from fontTools.pens.recordingPen import RecordingPen  # type: ignore

        key = f"{font_name}|{font_index}|{char}|{round(float(flat_seg_len_units), 6)}"
        cached = cls._glyph_cache.get(key)
        if cached is not None:
            return cached

        tt_font = cls.get_font(font_name, font_index)
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
                    "Character '%s' (U+%04X) not found in font '%s'", char, ord(char), font_name
                )
                cls._glyph_cache.set(key, tuple())
                return tuple()

        glyph_set = tt_font.getGlyphSet()
        glyph = glyph_set.get(glyph_name)
        if glyph is None:
            logger.warning("Glyph '%s' not found in font '%s'", glyph_name, font_name)
            cls._glyph_cache.set(key, tuple())
            return tuple()

        rec = RecordingPen()
        glyph.draw(rec)

        flat = RecordingPen()
        flatten_pen = FlattenPen(
            flat, approximateSegmentLength=float(flat_seg_len_units), segmentLines=True  # type: ignore
        )
        rec.replay(flatten_pen)

        result = tuple(flat.value)
        cls._glyph_cache.set(key, result)
        return result


# パフォーマンスのためのグローバルインスタンス
TEXT_RENDERER = TextRenderer()
_FONT_FILTER_PATH = Path(__file__).resolve().parents[2] / "data" / "fonts" / "usable_fonts.json"
_FILTERED_FONT_LOOKUP: dict[str, tuple[str, int]] = {}


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


def _glyph_commands_to_vertices_mm(
    glyph_commands: Iterable,
    units_per_em: float,
    em_size_mm: float,
    x_em: float,
    y_em: float,
) -> list[np.ndarray]:
    """RecordingPen.value から mm 単位の頂点列へ変換する。"""
    vertices_list: list[np.ndarray] = []
    cur: list[list[float]] = []
    for command in glyph_commands:
        cmd_type, cmd_values = command
        if cmd_type == "moveTo":
            if cur:
                arr = np.array(cur, dtype=np.float32)
                # Y 軸反転（描画座標系に合わせる）
                arr[:, 1] *= -1.0
                arr = _apply_offset_scale(
                    arr, x_em * units_per_em, y_em * units_per_em, em_size_mm / units_per_em
                )
                vertices_list.append(arr)
                cur = []
            x, y = cmd_values[0]
            cur.append([x, y])
        elif cmd_type == "lineTo":
            x, y = cmd_values[0]
            cur.append([x, y])
        elif cmd_type == "closePath":
            if cur:
                if len(cur) > 1 and (cur[0][0] != cur[-1][0] or cur[0][1] != cur[-1][1]):
                    cur.append([cur[0][0], cur[0][1]])
                arr = np.array(cur, dtype=np.float32)
                # Y 軸反転（描画座標系に合わせる）
                arr[:, 1] *= -1.0
                arr = _apply_offset_scale(
                    arr, x_em * units_per_em, y_em * units_per_em, em_size_mm / units_per_em
                )
                vertices_list.append(arr)
                cur = []
    if cur:
        arr = np.array(cur, dtype=np.float32)
        # Y 軸反転（描画座標系に合わせる）
        arr[:, 1] *= -1.0
        arr = _apply_offset_scale(
            arr, x_em * units_per_em, y_em * units_per_em, em_size_mm / units_per_em
        )
        vertices_list.append(arr)
    return vertices_list


@shape
def text(
    *,
    text: str = "HELLO",
    em_size_mm: float = 10.0,
    font: str = "Helvetica",
    font_index: int = 0,
    text_align: str = "left",
    tracking_em: float = 0.0,
    line_height: float = 1.2,
    flatten_tol_em: float = 0.01,
) -> Geometry:
    """フォントアウトラインからテキストを生成する（mm）。

    引数:
        text: 描画する文字列（`\n` で複数行）。
        em_size_mm: 1em の高さを mm で指定。
        font: フォント名（部分一致）またはパス。
        font_index: `.ttc` のサブフォント番号。
        text_align: 行揃え（`left|center|right`）。
        tracking_em: 文字間の追加トラッキング（em 比）。
        line_height: 行送り（em 比）。
        flatten_tol_em: 平坦化許容差（em 基準の近似セグメント長）。

    返り値:
        Geometry: mm 単位のポリライン集合。
    """
    try:
        fi = int(font_index)
    except Exception:
        fi = 0
    if fi < 0:
        fi = 0
    hint = _FILTERED_FONT_LOOKUP.get(font)
    if hint is not None:
        font_path, hint_idx = hint
        font = font_path
        if fi == 0 and hint_idx != 0:
            fi = hint_idx

    tt_font = TEXT_RENDERER.get_font(font, fi)
    units_per_em = float(tt_font["head"].unitsPerEm)  # type: ignore[index]
    seg_len_units = max(1.0, float(flatten_tol_em) * units_per_em)

    lines = text.split("\n")
    all_vertices: list[np.ndarray] = []
    y_em = 0.0
    for li, line in enumerate(lines):
        # 行幅（em）
        width_em = 0.0
        for ch in line:
            width_em += _get_char_advance_em(ch, tt_font) + (tracking_em if ch != "\n" else 0.0)
        if line:
            width_em -= tracking_em  # 末尾のトラッキングは除去

        if text_align == "center":
            x_em = -width_em / 2.0
        elif text_align == "right":
            x_em = -width_em
        else:
            x_em = 0.0

        # 1 文字ずつアウトラインを積む
        cur_x_em = x_em
        for ch in line:
            if ch != " ":
                cmds = TEXT_RENDERER.get_glyph_commands(
                    char=ch, font_name=font, font_index=fi, flat_seg_len_units=seg_len_units
                )
                if cmds:
                    all_vertices.extend(
                        _glyph_commands_to_vertices_mm(
                            cmds, units_per_em, float(em_size_mm), cur_x_em, y_em
                        )
                    )
            cur_x_em += _get_char_advance_em(ch, tt_font) + tracking_em

        # 次の行へ（下方向へ）。描画座標は Y 正が下方向のため、正方向に進める。
        if li < len(lines) - 1:
            y_em += float(line_height)

    return Geometry.from_lines(all_vertices)


# --- GUI 用フォント候補（import 時に 1 回だけ列挙） ---
def _font_choices() -> list[str]:
    """Parameter GUI 用のフォント候補。

    - `data/fonts/usable_fonts.json` があればそれを優先（フィルター済み、display_name を使用）。
    - 無ければ config の search_dirs のフォントを stem 名で列挙する。
    - 列挙失敗時は空配列を返す。
    """
    try:
        with _FONT_FILTER_PATH.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        entries = data.get("fonts", []) if isinstance(data, dict) else data
        choices: list[str] = []
        mapping: dict[str, tuple[str, int]] = {}
        seen: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            try:
                idx = int(entry.get("font_index", 0))
            except Exception:
                idx = 0
            if idx < 0:
                idx = 0
            path_obj = Path(raw_path)
            if not path_obj.exists():
                continue
            display = entry.get("display_name") or path_obj.stem
            name = str(display)
            candidate = name
            if candidate in seen:
                tail = path_obj.name
                candidate = f"{name} ({tail})"
                counter = 1
                while candidate in seen:
                    counter += 1
                    candidate = f"{name} ({tail}-{counter})"
            seen.add(candidate)
            mapping[candidate] = (str(path_obj), idx)
            choices.append(candidate)
        if choices:
            choices.sort(key=lambda s: s.lower())
            _FILTERED_FONT_LOOKUP.clear()
            _FILTERED_FONT_LOOKUP.update(mapping)
            return choices
    except Exception:
        _FILTERED_FONT_LOOKUP.clear()
    try:
        from util.fonts import glob_font_files, resolve_search_dirs  # type: ignore
        from util.utils import load_config  # type: ignore

        cfg = load_config() or {}
        fonts_cfg = cfg.get("fonts", {}) if isinstance(cfg, dict) else {}
        sdirs = fonts_cfg.get("search_dirs", []) if isinstance(fonts_cfg, dict) else []
        if isinstance(sdirs, (str, int, Path)):
            sdirs = [str(sdirs)]
        dirs = resolve_search_dirs(sdirs)
        files = glob_font_files(dirs)
    except Exception:
        return []
    seen: set[str] = set()
    stems: list[str] = []
    for p in files:
        try:
            name = str(p.stem)
            if name and name not in seen:
                stems.append(name)
                seen.add(name)
        except Exception:
            continue
    stems.sort(key=lambda s: s.lower())
    _FILTERED_FONT_LOOKUP.clear()
    return stems


setattr(
    text,
    "__param_meta__",
    {
        "text": {"type": "string", "multiline": True, "height": 160},
        "em_size_mm": {"type": "number", "min": 1.0, "max": 100.0, "step": 0.5},
        # font は GUI から選択可能にする（候補は設定/OS のフォント列挙に基づく）
        "font": {"choices": _font_choices(), "searchable": True},
        "font_index": {"type": "integer", "min": 0, "max": 32, "step": 1},
        "text_align": {"choices": ["left", "center", "right"]},
        "tracking_em": {"type": "number", "min": 0.0, "max": 0.5, "step": 0.01},
        "line_height": {"type": "number", "min": 0.8, "max": 3.0, "step": 0.1},
        "flatten_tol_em": {"type": "number", "min": 0.001, "max": 0.1, "step": 0.001},
    },
)
