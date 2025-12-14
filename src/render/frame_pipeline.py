"""
どこで: `src/render/frame_pipeline.py`。
何を: user_draw が生成するシーンを正規化・スタイル解決・realize し、Renderer へ描画依頼するパイプラインを提供する。
なぜ: `run` を極小化し、描画フローを一箇所に集約することで依存方向とテスト容易性を高めるため。
"""

from __future__ import annotations

from typing import Callable

from src.core.realize import realize
from src.parameters import current_param_store
from src.parameters.layer_style import (
    LAYER_STYLE_LINE_COLOR,
    LAYER_STYLE_LINE_THICKNESS,
    ensure_layer_style_entries,
    layer_style_key,
)
from src.parameters.style import rgb255_to_rgb01
from src.render.index_buffer import build_line_indices
from src.render.layer import LayerStyleDefaults, resolve_layer_style
from src.render.scene import SceneItem, normalize_scene


def render_scene(
    draw: Callable[[float], SceneItem],
    t: float,
    defaults: LayerStyleDefaults,
    renderer,
) -> None:
    """1 フレーム分のシーンを生成して描画する。

    Parameters
    ----------
    draw : Callable[[float], SceneItem]
        フレーム時刻 t を受け取り Geometry / Layer / Sequence を返すコールバック。
    t : float
        現在フレームの経過秒。
    defaults : LayerStyleDefaults
        スタイル欠損を埋める既定値。
    renderer : DrawRenderer
        `render_layer` を提供する描画オブジェクト。
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

    for layer in layers:
        resolved = resolve_layer_style(layer, defaults)

        thickness = float(resolved.thickness)
        color = resolved.color

        # Layer を観測し、override=True の場合だけ GUI 値で上書きして描画する。
        if store is not None:
            ensure_layer_style_entries(
                store,
                layer_site_id=layer.site_id,
                base_line_thickness=thickness,
                base_line_color_rgb01=color,
                initial_override_line_thickness=(layer.thickness is None),
                initial_override_line_color=(layer.color is None),
                label_name=layer.name,
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
        indices = build_line_indices(realized.offsets)
        renderer.render_layer(
            realized=realized,
            indices=indices,
            color=color,
            thickness=thickness,
        )
