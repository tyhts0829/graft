# src/core/realized_geometry.py
# Geometry 評価結果である RealizedGeometry 配列のモデルと検証ロジック。

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


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
    不変性を契約とし、配列は writeable=False で返す。
    offsets と coords の整合性はコンストラクタ内で検証する。
    """

    coords: np.ndarray
    offsets: np.ndarray

    def __post_init__(self) -> None:
        """配列形状と整合性を検証し、不変条件を満たす形に固定する。"""
        coords = np.asarray(self.coords)
        offsets = np.asarray(self.offsets)

        if coords.ndim == 2 and coords.shape[1] == 2:
            # 2D 入力は z=0 を補完して (N,3) に揃える。
            z = np.zeros((coords.shape[0], 1), dtype=coords.dtype)
            coords = np.concatenate([coords, z], axis=1)

        if coords.ndim != 2 or coords.shape[1] != 3:
            raise ValueError("coords は shape (N,3) の 2 次元配列である必要がある")

        if coords.dtype != np.float32:
            coords = coords.astype(np.float32, copy=False)

        if offsets.ndim != 1:
            raise ValueError("offsets は 1 次元配列である必要がある")

        if offsets.dtype != np.int32:
            offsets = offsets.astype(np.int32, copy=False)

        if offsets.size == 0:
            raise ValueError("offsets は少なくとも 1 要素を含む必要がある")

        if offsets[0] != 0:
            raise ValueError("offsets[0] は 0 である必要がある")

        if offsets[-1] != coords.shape[0]:
            raise ValueError("offsets[-1] は coords 行数と一致する必要がある")

        if np.any(np.diff(offsets) < 0):
            raise ValueError("offsets は単調非減少である必要がある")

        # 不変性確保のため writeable=False に設定する。
        coords.setflags(write=False)
        offsets.setflags(write=False)

        object.__setattr__(self, "coords", coords)
        object.__setattr__(self, "offsets", offsets)


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
        empty_coords = np.zeros((0, 3), dtype=np.float32)
        empty_offsets = np.zeros((1,), dtype=np.int32)
        return RealizedGeometry(coords=empty_coords, offsets=empty_offsets)

    coords_list = [g.coords for g in geometries]
    offsets_list = [g.offsets for g in geometries]

    total_coords = np.concatenate(coords_list, axis=0)

    new_offsets: list[int] = []
    offset_base = 0
    for offsets in offsets_list:
        # 先頭 0 を除いた差分部分だけをシフトして足し込む。
        shifted = offsets[1:] + offset_base
        if not new_offsets:
            new_offsets.append(0)
        new_offsets.extend(shifted.tolist())
        offset_base += offsets[-1]

    new_offsets_array = np.asarray(new_offsets, dtype=np.int32)
    return RealizedGeometry(coords=total_coords, offsets=new_offsets_array)
