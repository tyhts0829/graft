# どこで: `src/api/layers.py`。
# 何を: Geometry を Layer 化する公開ヘルパ L を提供する。
# なぜ: Layer 生成責務を切り出し、API を見通し良くするため。

from __future__ import annotations

from typing import Sequence

from src.core.geometry import Geometry
from src.render.layer import Layer


class LayerHelper:
    """Geometry にスタイルを付けて Layer のリストを生成するヘルパ。"""

    def __call__(
        self,
        geometry_or_list: Geometry | Sequence[Geometry],
        *,
        color: tuple[float, float, float] | None = None,
        thickness: float | None = None,
        name: str | None = None,
    ) -> list[Layer]:
        """単体/複数の Geometry から Layer を生成する。

        Parameters
        ----------
        geometry_or_list : Geometry or Sequence[Geometry]
            入力 Geometry または Geometry の列。
        color : tuple[float, float, float] or None, optional
            RGB 色。None の場合は既定値に委譲。
        thickness : float or None, optional
            線幅。None の場合は既定値に委譲。0 以下は拒否。
        name : str or None, optional
            Layer 名（任意）。

        Returns
        -------
        list[Layer]
            生成された Layer のリスト。

        Raises
        ------
        TypeError
            Geometry 以外が渡された場合。
        ValueError
            thickness が 0 以下の場合。
        """
        if thickness is not None and thickness <= 0:
            raise ValueError("thickness は正の値である必要がある")

        # geometry_or_list を Geometry のリストに正規化する。
        geometries: list[Geometry]
        if isinstance(geometry_or_list, Geometry):
            geometries = [geometry_or_list]
        elif isinstance(geometry_or_list, Sequence):
            geometries = []
            for g in geometry_or_list:
                if not isinstance(g, Geometry):
                    raise TypeError(
                        f"L には Geometry だけを渡してください: {type(g)!r}"
                    )
                geometries.append(g)
        else:
            raise TypeError(
                f"L は Geometry またはその列のみを受け付けます: {type(geometry_or_list)!r}"
            )

        if not geometries:
            raise ValueError("L に空の Geometry リストは渡せません")

        # 複数 Geometry は concat で 1 Layer にまとめる。
        if len(geometries) == 1:
            geometry = geometries[0]
        else:
            geometry = Geometry.create(op="concat", inputs=tuple(geometries), params={})

        return [Layer(geometry=geometry, color=color, thickness=thickness, name=name)]


L = LayerHelper()
"""Geometry を Layer 化する公開ヘルパ。"""

__all__ = ["L"]
