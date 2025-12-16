"""
どこで: `src/core/pipeline.py`。
何を: user_draw が生成するシーンを正規化・スタイル解決・realize し、描画/出力に使える “最終形” を返す。
なぜ: interactive（GL 描画）と export（ヘッドレス出力）で共通のパイプラインを共有し、依存方向を単純化するため。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.core.realize import realize
from src.core.realized_geometry import RealizedGeometry
from src.core.layer import Layer, LayerStyleDefaults, resolve_layer_style
from src.core.scene import SceneItem, normalize_scene
from src.core.parameters import current_frame_params, current_param_store
from src.core.parameters.layer_style import (
    LAYER_STYLE_OP,
    LAYER_STYLE_LINE_COLOR,
    LAYER_STYLE_LINE_THICKNESS,
    layer_style_key,
    layer_style_records,
)
from src.core.parameters.style import rgb255_to_rgb01


@dataclass(frozen=True, slots=True)
class RealizedLayer:
    """描画/出力のために realize 済みにした Layer 表現。"""

    layer: Layer
    realized: RealizedGeometry
    color: tuple[float, float, float]
    thickness: float


def realize_scene(
    draw: Callable[[float], SceneItem],
    t: float,
    defaults: LayerStyleDefaults,
) -> list[RealizedLayer]:
    """1 フレーム分のシーンを realize して返す。

    Parameters
    ----------
    draw : Callable[[float], SceneItem]
        フレーム時刻 t を受け取り Geometry / Layer / Sequence を返すコールバック。
    t : float
        現在フレームの経過秒。
    defaults : LayerStyleDefaults
        スタイル欠損を埋める既定値。

    Returns
    -------
    list[RealizedLayer]
        realize 済みの Layer 列。
    """

    scene = draw(t)
    layers = normalize_scene(scene)

    store = current_param_store()

    def _coerce_rgb255(value: object) -> tuple[int, int, int]:
        try:
            r, g, b = value  # type: ignore[misc]
        except Exception as exc:
            raise ValueError(
                f"rgb value must be a length-3 sequence: {value!r}"
            ) from exc

        out: list[int] = []
        for v in (r, g, b):
            iv = int(v)
            iv = max(0, min(255, iv))
            out.append(iv)
        return int(out[0]), int(out[1]), int(out[2])

    out: list[RealizedLayer] = []
    for layer in layers:
        resolved = resolve_layer_style(layer, defaults)

        thickness = float(resolved.thickness)
        color = resolved.color

        # Layer style を観測し、override=True の場合だけ GUI 値で上書きして描画する。
        if store is not None:
            if layer.name is not None:
                store.set_label(LAYER_STYLE_OP, layer.site_id, layer.name)

            frame_params = current_frame_params()
            if frame_params is not None:
                frame_params.records.extend(
                    layer_style_records(
                        layer_site_id=layer.site_id,
                        base_line_thickness=thickness,
                        base_line_color_rgb01=color,
                        explicit_line_thickness=(layer.thickness is not None),
                        explicit_line_color=(layer.color is not None),
                    )
                )

            thickness_state = store.get_state(
                layer_style_key(layer.site_id, LAYER_STYLE_LINE_THICKNESS)
            )
            if thickness_state is not None and thickness_state.override:
                thickness = float(thickness_state.ui_value)

            color_state = store.get_state(
                layer_style_key(layer.site_id, LAYER_STYLE_LINE_COLOR)
            )
            if color_state is not None and color_state.override:
                rgb255 = _coerce_rgb255(color_state.ui_value)
                color = rgb255_to_rgb01(rgb255)

        geometry = resolved.layer.geometry
        # Geometry は L 側で concat 済みのためそのまま扱う
        realized = realize(geometry)
        out.append(
            RealizedLayer(
                layer=resolved.layer,
                realized=realized,
                color=color,
                thickness=thickness,
            )
        )

    return out
