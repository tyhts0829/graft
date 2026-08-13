"""
Purpose:
    同梱assetで定義された正多面体・半正多面体を、面ごとの閉polylineとして提供する。
Use when:
    polyhedron種別、package resource、face topology、またはasset cacheを変更する場合。
Constraints:
    - assetのfloat32 packed face境界を正本とし、各faceのclosureを維持する。
    - process cacheはread-onlyに保ち、呼び出しごとにfreshなwritable出力を返す。
    - asset欠落や不正schemaを別形状へfallbackせず明示的に拒否する。
Side effects:
    kindを初めて使う際にpackage resourceを読み、process-local cacheへ保持する。
"""

from __future__ import annotations

from io import BytesIO
from importlib import resources

import numpy as np

from grafix.core.parameters.meta import ParamMeta
from grafix.core.operation_authoring import primitive
from grafix.core.realized_geometry import GeomTuple

# UI と stub の choice 順序を固定する。
_TYPE_ORDER = (
    # Platonic solids
    "tetrahedron",
    "hexahedron",
    "octahedron",
    "dodecahedron",
    "icosahedron",
    # Archimedean solids (+ snub chirality variants)
    "cuboctahedron",
    "icosidodecahedron",
    "truncated_tetrahedron",
    "truncated_cube",
    "truncated_octahedron",
    "truncated_dodecahedron",
    "truncated_icosahedron",
    "rhombicuboctahedron",
    "snub_cube_left",
    "snub_cube_right",
    "snub_dodecahedron_left",
    "snub_dodecahedron_right",
    "truncated_cuboctahedron",
    "rhombicosidodecahedron",
    "truncated_icosidodecahedron",
)

_DATA_DIR = resources.files("grafix").joinpath("resource", "regular_polyhedron")
_POLYHEDRON_CACHE: dict[str, GeomTuple] = {}

polyhedron_meta = {
    "kind": ParamMeta(
        kind="choice",
        choices=_TYPE_ORDER,
        description="ワイヤーフレームとして生成する正多面体または半正多面体を選択します。",
    ),
    "center": ParamMeta(
        kind="vec3",
        ui_min=0.0,
        ui_max=300.0,
        description="多面体全体を平行移動する XYZ 座標を指定します。",
    ),
    "scale": ParamMeta(
        kind="float",
        ui_min=0.0,
        ui_max=200.0,
        description="同梱頂点データから生成した多面体に適用する等方スケールを指定します。",
    ),
}


def _load_packed_polyhedron(kind: str) -> GeomTuple:
    """データファイルを一度だけ読み込み、immutable な packed geometry を返す。"""
    cached = _POLYHEDRON_CACHE.get(kind)
    if cached is not None:
        return cached

    npz_file = _DATA_DIR.joinpath(f"{kind}_vertices_list.npz")
    if not npz_file.is_file():
        raise FileNotFoundError(f"polyhedron データが見つかりません: {npz_file}")

    blob = npz_file.read_bytes()
    with np.load(BytesIO(blob), allow_pickle=False) as data:
        keys = [f"arr_{index}" for index in range(len(data.files))]
        if not keys or set(data.files) != set(keys):
            raise ValueError(
                "polyhedron データは arr_0 から連番の配列だけを含む必要がある"
                f": file={npz_file.name}, keys={data.files!r}"
            )
        raw_lines = [data[key] for key in keys]

    polylines: list[np.ndarray] = []
    for i, line in enumerate(raw_lines):
        arr = np.asarray(line)
        if arr.dtype != np.float32 or arr.ndim != 2 or arr.shape[1] != 3:
            raise ValueError(
                "polyhedron データの各ポリラインは float32 shape (N,3) "
                "の配列である必要がある"
                f": kind={kind!r}, index={i}, dtype={arr.dtype}, shape={arr.shape}"
            )
        polylines.append(arr)

    if polylines:
        coords = np.concatenate(polylines, axis=0).astype(np.float32, copy=False)
        offsets = np.empty(len(polylines) + 1, dtype=np.int32)
        offsets[0] = 0
        acc = 0
        for i, line in enumerate(polylines):
            acc += int(line.shape[0])
            offsets[i + 1] = acc
    else:
        coords = np.zeros((0, 3), dtype=np.float32)
        offsets = np.zeros((1,), dtype=np.int32)

    # cache は直接返さず、呼び出しごとに copy する。誤変更による後続結果の汚染を防ぐ。
    coords.setflags(write=False)
    offsets.setflags(write=False)
    cached = (coords, offsets)
    _POLYHEDRON_CACHE[kind] = cached
    return cached


def _copy_and_place_polyhedron(
    packed: GeomTuple,
    *,
    center: tuple[float, float, float],
    scale: float,
) -> GeomTuple:
    """cached packed geometry を fresh な writable 配列として返す。"""
    base_coords, base_offsets = packed
    if base_coords.shape[0] == 0:
        return base_coords.copy(), base_offsets.copy()

    cx, cy, cz = center
    s_f = scale

    coords = base_coords.copy()
    offsets = base_offsets.copy()

    if (cx, cy, cz) != (0.0, 0.0, 0.0) or s_f != 1.0:
        center_vec = np.array([cx, cy, cz], dtype=np.float32)
        coords = coords * np.float32(s_f) + center_vec

    return coords, offsets


@primitive(meta=polyhedron_meta)
def polyhedron(
    *,
    kind: str = "tetrahedron",
    center: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: float = 1.0,
) -> GeomTuple:
    """多面体を面ポリライン列として生成する。

    Parameters
    ----------
    kind : str, optional
        多面体の名前。選択肢は ``polyhedron_meta["kind"].choices`` を参照する。
    center : tuple[float, float, float], optional
        平行移動ベクトル (cx, cy, cz)。
    scale : float, optional
        等方スケール倍率 s。縦横比変更は effect を使用する。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        各面が「閉ポリライン（先頭==末尾）」になっている実体ジオメトリ（coords, offsets）。

    Raises
    ------
    FileNotFoundError
        `grafix/resource/regular_polyhedron` のデータが見つからない場合。
    ValueError
        ``kind`` が未登録、またはデータ内容が不正な場合。
    """
    if kind not in _TYPE_ORDER:
        choices = ", ".join(_TYPE_ORDER)
        raise ValueError(f"polyhedron.kind must be one of: {choices}; got {kind!r}")
    packed = _load_packed_polyhedron(kind)
    return _copy_and_place_polyhedron(packed, center=center, scale=scale)
