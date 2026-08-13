"""
Purpose:
    Geometry 評価結果を packed polyline 配列の immutable snapshot として固定する境界を定義する。
Use when:
    custom operation の I/O、kernel と evaluator の境界、geometry 連結、CPU/GPU/export が共有する配列契約を変更・調査する場合。
Constraints:
    - coords は exact float32/C-contiguous/finite ``(N, 3)``、offsets は exact int32/C-contiguous の1次元配列とする。
    - offsets は 0 で始まり N で終わる単調非減少とし、packed polyline 境界を壊さない。
    - caller-owned mutable array を alias せず、後から writeable に戻せない bytes-backed snapshot だけを安全に共有する。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from grafix.core.geometry_kernels.packed import empty_packed_geometry


GeomTuple = tuple[np.ndarray, np.ndarray]
"""`(coords, offsets)` で表すポリライン集合の最小表現。

- `coords`: exact ndarray、C-contiguous、shape `(N,3)`、dtype float32 の有限座標
- `offsets`: exact ndarray、C-contiguous、shape `(M+1,)`、dtype int32 の境界

Notes
-----
このタプル表現は、`@primitive` / `@effect` のユーザー定義 I/O として利用する。
core 内部では最終的に `RealizedGeometry` に統一する。
"""


def _validate_coords(coords: object) -> np.ndarray:
    """canonical coords 配列を検証して返す。"""

    if type(coords) is not np.ndarray:
        raise TypeError("coords は exact np.ndarray である必要がある")
    if coords.ndim != 2 or coords.shape[1] != 3:
        raise ValueError("coords は shape (N,3) の 2 次元配列である必要がある")
    if coords.dtype != np.float32:
        raise TypeError("coords は dtype float32 である必要がある")
    if not coords.flags.c_contiguous:
        raise ValueError("coords は C-contiguous である必要がある")
    if not np.isfinite(coords).all():
        raise ValueError("coords は有限値だけを含む必要がある")
    return coords


def _validate_offsets(offsets: object, *, vertex_count: int) -> np.ndarray:
    """canonical offsets 配列と packed geometry 整合性を検証して返す。"""

    if type(offsets) is not np.ndarray:
        raise TypeError("offsets は exact np.ndarray である必要がある")
    if offsets.ndim != 1:
        raise ValueError("offsets は 1 次元配列である必要がある")
    if offsets.dtype != np.int32:
        raise TypeError("offsets は dtype int32 である必要がある")
    if not offsets.flags.c_contiguous:
        raise ValueError("offsets は C-contiguous である必要がある")
    if offsets.size == 0:
        raise ValueError("offsets は少なくとも 1 要素を含む必要がある")
    if offsets[0] != 0:
        raise ValueError("offsets[0] は 0 である必要がある")
    if offsets[-1] != vertex_count:
        raise ValueError("offsets[-1] は coords 行数と一致する必要がある")
    if np.any(offsets[1:] < offsets[:-1]):
        raise ValueError("offsets は単調非減少である必要がある")
    return offsets


def _immutable_snapshot(array: np.ndarray) -> np.ndarray:
    """immutable bytes を backing に持つ C-order snapshot を返す。"""

    current: object = array
    while type(current) is np.ndarray:
        if current.flags.writeable:
            current = None
            break
        current = current.base
    if type(current) is bytes:
        # RealizedGeometry が既に所有する immutable snapshot は安全に共有する。
        # static topology の offsets identity もこの経路で維持される。
        return array

    snapshot = np.frombuffer(array.tobytes(order="C"), dtype=array.dtype).reshape(
        array.shape
    )
    assert snapshot.flags.c_contiguous
    assert not snapshot.flags.writeable
    return snapshot


@dataclass(frozen=True, slots=True)
class RealizedGeometry:
    """Geometry を評価した結果である実体配列を表現する。

    Parameters
    ----------
    coords : np.ndarray
        float32 型 shape (N, 3) の頂点配列。
    offsets : np.ndarray
        int32 型 shape (M+1,) のポリライン開始インデックス配列。

    Notes
    -----
    入力を暗黙変換せず canonical shape/dtype/layout を要求する。mutable な
    caller 配列はコピーし、保持する snapshot の writeable flag は後から
    有効化できない。既存の bytes-backed snapshot だけは安全に再利用する。
    """

    coords: np.ndarray
    offsets: np.ndarray

    def __post_init__(self) -> None:
        """canonical 配列を検証し、外部 alias の無い snapshot に固定する。"""

        coords = _validate_coords(self.coords)
        offsets = _validate_offsets(self.offsets, vertex_count=int(coords.shape[0]))
        object.__setattr__(self, "coords", _immutable_snapshot(coords))
        object.__setattr__(self, "offsets", _immutable_snapshot(offsets))

    @property
    def byte_size(self) -> int:
        """座標・境界配列が使用する byte 数を返す。"""

        return int(self.coords.nbytes + self.offsets.nbytes)

    def _with_coords(self, coords: object) -> RealizedGeometry | None:
        """検証済み offsets を共有できる場合だけ新しい内部 geometry を返す。

        canonical 条件を満たさない入力は、呼び出し側の通常の戻り値検証へ渡すため
        ``None`` を返す。
        """

        try:
            coords_array = _validate_coords(coords)
        except (TypeError, ValueError):
            return None
        if self.offsets[-1] != coords_array.shape[0]:
            return None

        result = object.__new__(RealizedGeometry)
        object.__setattr__(result, "coords", _immutable_snapshot(coords_array))
        object.__setattr__(result, "offsets", self.offsets)
        return result


def realized_geometry_from_tuple(value: object, *, context: str) -> RealizedGeometry:
    """`(coords, offsets)` を `RealizedGeometry` に変換する。

    Parameters
    ----------
    value : object
        `(coords, offsets)` タプル。
    context : str
        例外メッセージに含める文脈情報（op 名/関数名など）。

    Returns
    -------
    RealizedGeometry
        変換結果。

    Notes
    -----
    - `coords` は shape `(N,3)` のみを受理する（`(N,2)` はエラー）。
    - offsets の整合性（先頭 0 / 末尾 N / 単調性）は `RealizedGeometry` が検証する。
    """

    if type(value) is not tuple or len(value) != 2:
        raise TypeError(
            f"{context}: 期待する戻り値は (coords, offsets) タプルです: {type(value)!r}"
        )

    coords, offsets = value

    try:
        return RealizedGeometry(coords=coords, offsets=offsets)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context}: (coords, offsets) が不正です") from exc


def concat_geom_tuples(*geometries: GeomTuple) -> GeomTuple:
    """複数の `(coords, offsets)` を連結して 1 つにまとめる。

    Parameters
    ----------
    geometries : tuple[np.ndarray, np.ndarray]
        連結対象のジオメトリ列。

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        結合後の (coords, offsets)。
    """
    if not geometries:
        return empty_packed_geometry()
    arrays = tuple(_validate_geom_tuple(value) for value in geometries)
    if len(geometries) == 1:
        return arrays[0]
    return _concat_arrays(arrays)


def concat_realized_geometries(*geometries: RealizedGeometry) -> RealizedGeometry:
    """複数の RealizedGeometry を連結して 1 つにまとめる。

    Parameters
    ----------
    geometries : RealizedGeometry
        連結対象のジオメトリ列。

    Returns
    -------
    RealizedGeometry
        結合後の実体ジオメトリ。
    """
    if not geometries:
        coords, offsets = empty_packed_geometry()
        return RealizedGeometry(coords=coords, offsets=offsets)
    if len(geometries) == 1:
        return geometries[0]

    coords, offsets = _concat_arrays([(g.coords, g.offsets) for g in geometries])
    return RealizedGeometry(coords=coords, offsets=offsets)

def _validate_geom_tuple(value: object) -> GeomTuple:
    """canonical な raw ``GeomTuple`` を検証して返す。"""

    if type(value) is not tuple or len(value) != 2:
        raise TypeError("geometry は exact (coords, offsets) tuple である必要がある")
    coords, offsets = value
    coords_array = _validate_coords(coords)
    offsets_array = _validate_offsets(
        offsets,
        vertex_count=int(coords_array.shape[0]),
    )
    return coords_array, offsets_array


def _concat_arrays(geometries: Sequence[GeomTuple]) -> GeomTuple:
    """最終サイズを一度数え、連結配列へ直接書き込む。"""

    total_vertices = sum(int(coords.shape[0]) for coords, _ in geometries)
    total_offsets = 1 + sum(
        max(0, int(offsets.size) - 1) for _, offsets in geometries
    )
    int32_max = int(np.iinfo(np.int32).max)
    if total_vertices > int32_max or total_offsets > int32_max:
        raise OverflowError("連結結果が int32 の表現範囲を超える")

    coords_out = np.empty((total_vertices, 3), dtype=np.float32)
    offsets_out = np.empty((total_offsets,), dtype=np.int32)
    offsets_out[0] = 0

    vertex_at = 0
    offset_at = 1
    for coords, offsets in geometries:
        n_vertices = int(coords.shape[0])
        next_vertex = vertex_at + n_vertices
        coords_out[vertex_at:next_vertex] = coords

        n_offsets = max(0, int(offsets.size) - 1)
        if n_offsets:
            next_offset = offset_at + n_offsets
            offsets_out[offset_at:next_offset] = offsets[1:] + vertex_at
            offset_at = next_offset
        vertex_at = next_vertex

    return coords_out, offsets_out
