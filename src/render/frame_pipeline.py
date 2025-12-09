"""
どこで: `src/render/frame_pipeline.py`。
何を: user_draw が生成するシーンを正規化・スタイル解決・realize し、Renderer へ描画依頼するパイプラインを提供する。
なぜ: `run` を極小化し、描画フローを一箇所に集約することで依存方向とテスト容易性を高めるため。
"""

from __future__ import annotations

from typing import Callable

from src.core.realize import realize
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

    for layer in layers:
        resolved = resolve_layer_style(layer, defaults)
        realized = realize(resolved.layer.geometry)
        indices = build_line_indices(realized.offsets)
        renderer.render_layer(
            realized=realized,
            indices=indices,
            color=resolved.color,
            thickness=resolved.thickness,
        )
