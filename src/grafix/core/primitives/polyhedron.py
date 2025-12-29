"""
どこで: `src/grafix/primitives/polyhedron.py`。正多面体プリミティブの実体生成。
何を: `data/regular_polyhedron/*_vertices_list.npz` から面ポリライン列を読み込み、選択して返す。
なぜ: 正多面体データを primitive として提供し、プレビューとエクスポートで再利用するため。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.primitive_registry import primitive
from grafix.core.realized_geometry import RealizedGeometry

# `type_index`（0..N-1）で参照する型順序を固定する。
_TYPE_ORDER = ["tetrahedron", "hexahedron", "octahedron", "dodecahedron", "icosahedron"]

_DATA_DIR = Path(__file__).parent / "regular_polyhedron"
_POLYHEDRON_CACHE: dict[str, tuple[np.ndarray, ...]] = {}

polyhedron_meta = {
    "type_index": ParamMeta(kind="int", ui_min=0, ui_max=len(_TYPE_ORDER) - 1),
    "center": ParamMeta(kind="vec3", ui_min=0.0, ui_max=300.0),
    "scale": ParamMeta(kind="float", ui_min=0.0, ui_max=200.0),
}


def _load_face_polylines(kind: str) -> tuple[np.ndarray, ...]:
    """データファイルから「面ポリライン列」を読み込んで返す。"""
    cached = _POLYHEDRON_CACHE.get(kind)
    if cached is not None:
        return cached

    npz_path = _DATA_DIR / f"{kind}_vertices_list.npz"
    if not npz_path.exists():
        raise FileNotFoundError(f"polyhedron データが見つかりません: {npz_path}")

    with np.load(npz_path, allow_pickle=False) as data:
        if "arrays" in data.files:
            raw_lines = list(data["arrays"])
        else:
            keys = sorted(
                [k for k in data.files if k.startswith("arr_")],
                key=lambda k: int(k.split("_")[1]),
            )
            if not keys:
                raise ValueError(f"polyhedron データが空です: {npz_path.name}")
            raw_lines = [data[k] for k in keys]

    polylines: list[np.ndarray] = []
    for i, line in enumerate(raw_lines):
        arr = np.asarray(line, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] not in (2, 3):
            raise ValueError(
                "polyhedron データの各ポリラインは shape (N,3) の配列である必要がある"
                f": kind={kind!r}, index={i}, shape={arr.shape}"
            )
        polylines.append(arr.astype(np.float32, copy=False))

    cached = tuple(polylines)
    _POLYHEDRON_CACHE[kind] = cached
    return cached


def _polylines_to_realized(
    polylines: tuple[np.ndarray, ...],
    *,
    center: tuple[float, float, float],
    scale: float,
) -> RealizedGeometry:
    """面ポリライン列を RealizedGeometry に変換する。"""
    if not polylines:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=coords, offsets=offsets)

    try:
        cx, cy, cz = center
    except Exception as exc:
        raise ValueError(
            "polyhedron の center は長さ 3 のシーケンスである必要がある"
        ) from exc
    try:
        s_f = float(scale)
    except Exception as exc:
        raise ValueError("polyhedron の scale は float である必要がある") from exc

    coords = np.concatenate(polylines, axis=0).astype(np.float32, copy=False)

    offsets = np.zeros(len(polylines) + 1, dtype=np.int32)
    acc = 0
    for i, line in enumerate(polylines):
        acc += int(line.shape[0])
        offsets[i + 1] = acc

    cx_f, cy_f, cz_f = float(cx), float(cy), float(cz)
    if (cx_f, cy_f, cz_f) != (0.0, 0.0, 0.0) or s_f != 1.0:
        center_vec = np.array([cx_f, cy_f, cz_f], dtype=np.float32)
        coords = coords * np.float32(s_f) + center_vec

    return RealizedGeometry(coords=coords, offsets=offsets)


@primitive(meta=polyhedron_meta)
def polyhedron(
    *,
    type_index: int = 0,
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> RealizedGeometry:
    """正多面体を面ポリライン列として生成する。

    Parameters
    ----------
    type_index : int, optional
        形状の選択インデックス（0..N-1）。範囲外はクランプする。
        0=tetrahedron, 1=hexahedron, 2=octahedron, 3=dodecahedron, 4=icosahedron。
    center : tuple[float, float, float], optional
        平行移動ベクトル (cx, cy, cz)。
    scale : float, optional
        等方スケール倍率 s。縦横比変更は effect を使用する。

    Returns
    -------
    RealizedGeometry
        各面が「閉ポリライン（先頭==末尾）」になっている実体ジオメトリ。

    Raises
    ------
    FileNotFoundError
        `data/regular_polyhedron` のデータが見つからない場合。
    ValueError
        データ内容が不正な場合。
    """
    idx = int(type_index)
    if idx < 0:
        idx = 0
    elif idx >= len(_TYPE_ORDER):
        idx = len(_TYPE_ORDER) - 1

    kind = _TYPE_ORDER[idx]
    polylines = _load_face_polylines(kind)
    return _polylines_to_realized(polylines, center=center, scale=scale)
