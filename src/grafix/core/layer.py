"""
どこで: `src/grafix/core/layer.py`。
何を: Layer モデルとスタイル既定値適用のユーティリティを定義する。
なぜ: Geometry と描画スタイルを分離し、interactive/export のどちらでも共通のシーン表現を扱うため。
"""

from __future__ import annotations

from dataclasses import dataclass

from grafix.core.geometry import Geometry

ColorRGB = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Layer:
    """Geometry と RGB 色・線幅を束ねるシーン要素。"""

    geometry: Geometry
    site_id: str
    color: ColorRGB | None = None
    thickness: float | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class LayerStyleDefaults:
    """Layer の欠損スタイルを埋める既定値。"""

    color: ColorRGB
    thickness: float


@dataclass(frozen=True, slots=True)
class ResolvedLayer:
    """スタイルを欠損なく解決した Layer。"""

    layer: Layer
    color: ColorRGB
    thickness: float


def resolve_layer_style(layer: Layer, defaults: LayerStyleDefaults) -> ResolvedLayer:
    """Layer の色・線幅を確定させる。

    Parameters
    ----------
    layer : Layer
        スタイル未指定（None を含む）を許容する Layer。
    defaults : LayerStyleDefaults
        欠損を埋めるための既定スタイル。

    Returns
    -------
    ResolvedLayer
        色と線幅を欠損なく持つ Layer 表現。

    Raises
    ------
    ValueError
        thickness が正の値でない場合。
    """

    thickness = layer.thickness if layer.thickness is not None else defaults.thickness
    if thickness <= 0:
        raise ValueError("thickness は正の値である必要がある")

    color = layer.color if layer.color is not None else defaults.color

    return ResolvedLayer(layer=layer, color=color, thickness=thickness)
