"""
どこで: `src/grafix/core/layer.py`。
何を: Layer モデルとスタイル既定値適用のユーティリティを定義する。
なぜ: Geometry と描画スタイルを分離し、interactive/export のどちらでも共通のシーン表現を扱うため。
"""

from __future__ import annotations

from dataclasses import dataclass

from grafix.core.geometry import Geometry
from grafix.core.value_validation import exact_bool, exact_string, finite_real, rgb01_tuple

ColorRGB = tuple[float, float, float]


def _color_rgb01(value: object, *, field: str) -> ColorRGB:
    """内部 Layer 用の RGB01 tuple を検証して返す。"""

    return rgb01_tuple(value, name=field)


@dataclass(frozen=True, slots=True)
class Layer:
    """Geometry、描画style、G-code最適化の一括指定を束ねるシーン要素。

    ``gcode_optimize`` はG-code exporterだけが解釈する。Falseの場合、そのLayerでは
    strokeの並べ替え、反転、短距離bridgeをすべて無効にする。
    """

    geometry: Geometry
    site_id: str
    color: ColorRGB | None = None
    thickness: float | None = None
    name: str | None = None
    gcode_optimize: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.geometry, Geometry):
            raise TypeError("Layer.geometry は Geometry である必要があります")
        site_id = exact_string(self.site_id, name="Layer.site_id")
        if not site_id:
            raise ValueError("Layer.site_id は空にできません")
        if self.color is not None:
            object.__setattr__(
                self,
                "color",
                _color_rgb01(self.color, field="Layer.color"),
            )
        if self.thickness is not None:
            object.__setattr__(
                self,
                "thickness",
                finite_real(
                    self.thickness,
                    name="Layer.thickness",
                    minimum=0.0,
                    minimum_inclusive=False,
                ),
            )
        if self.name is not None:
            exact_string(self.name, name="Layer.name")
        object.__setattr__(
            self,
            "gcode_optimize",
            exact_bool(self.gcode_optimize, name="Layer.gcode_optimize"),
        )


@dataclass(frozen=True, slots=True)
class LayerStyleDefaults:
    """Layer の欠損スタイルを埋める既定値。"""

    color: ColorRGB
    thickness: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "color",
            _color_rgb01(self.color, field="LayerStyleDefaults.color"),
        )
        object.__setattr__(
            self,
            "thickness",
            finite_real(
                self.thickness,
                name="LayerStyleDefaults.thickness",
                minimum=0.0,
                minimum_inclusive=False,
            ),
        )


@dataclass(frozen=True, slots=True)
class ResolvedLayer:
    """スタイルを欠損なく解決した Layer。"""

    layer: Layer
    color: ColorRGB
    thickness: float

    def __post_init__(self) -> None:
        if not isinstance(self.layer, Layer):
            raise TypeError("ResolvedLayer.layer は Layer である必要があります")
        object.__setattr__(
            self,
            "color",
            _color_rgb01(self.color, field="ResolvedLayer.color"),
        )
        object.__setattr__(
            self,
            "thickness",
            finite_real(
                self.thickness,
                name="ResolvedLayer.thickness",
                minimum=0.0,
                minimum_inclusive=False,
            ),
        )


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

    """

    if not isinstance(layer, Layer):
        raise TypeError("layer は Layer である必要があります")
    if not isinstance(defaults, LayerStyleDefaults):
        raise TypeError("defaults は LayerStyleDefaults である必要があります")

    thickness = layer.thickness if layer.thickness is not None else defaults.thickness
    color = layer.color if layer.color is not None else defaults.color

    return ResolvedLayer(layer=layer, color=color, thickness=thickness)
