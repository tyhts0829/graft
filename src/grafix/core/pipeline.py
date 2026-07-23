"""
どこで: `src/grafix/core/pipeline.py`。
何を: user_draw が生成するシーンを正規化・スタイル解決・realize し、描画/出力に使える “最終形” を返す。
なぜ: interactive（GL 描画）と export（ヘッドレス出力）で共通のパイプラインを共有し、依存方向を単純化するため。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.layer import Layer, LayerStyleDefaults, resolve_layer_style
from grafix.core.parameters.layer_style import observe_and_apply_layer_style
from grafix.core.evaluation_context import EvaluationContext
from grafix.core.operation_catalog import bind_operation_catalog, current_operation_catalog
from grafix.core.preview_quality import current_preview_quality, preview_quality_context
from grafix.core.preset_catalog import (
    PresetCatalog,
    bind_preset_catalog,
    current_preset_catalog,
)
from grafix.core.realize import GeometryCacheKey, RealizeSession
from grafix.core.realized_geometry import RealizedGeometry
from grafix.core.resource_budget import ensure_resource_usage
from grafix.core.scene import SceneItem, normalize_scene
from grafix.core.runtime_config import RuntimeConfig, bind_runtime_config
from grafix.core.value_validation import finite_real, rgb01_tuple


@dataclass(frozen=True, slots=True)
class RealizedLayer:
    """描画/出力のために realize 済みにした Layer 表現。"""

    layer: Layer
    realized: RealizedGeometry
    cache_key: GeometryCacheKey
    color: tuple[float, float, float]
    thickness: float

    def __post_init__(self) -> None:
        if not isinstance(self.layer, Layer):
            raise TypeError("RealizedLayer.layer は Layer である必要があります")
        if not isinstance(self.realized, RealizedGeometry):
            raise TypeError("RealizedLayer.realized は RealizedGeometry である必要があります")
        if type(self.cache_key) is not GeometryCacheKey:
            raise TypeError("RealizedLayer.cache_key は exact GeometryCacheKey です")
        if self.cache_key.geometry_id != self.layer.geometry.id:
            raise ValueError(
                "RealizedLayer.cache_key geometry_id は Layer.geometry.id と一致する必要があります"
            )
        object.__setattr__(
            self,
            "color",
            rgb01_tuple(self.color, name="RealizedLayer.color"),
        )
        object.__setattr__(
            self,
            "thickness",
            finite_real(
                self.thickness,
                name="RealizedLayer.thickness",
                minimum=0.0,
                minimum_inclusive=False,
            ),
        )


def realize_scene(
    draw: Callable[[float], SceneItem],
    t: float,
    defaults: LayerStyleDefaults,
    *,
    config: RuntimeConfig,
    session: RealizeSession | None = None,
    presets: PresetCatalog | None = None,
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
    config : RuntimeConfig
        draw の authoring scope に束縛する確定済み設定。
    session : RealizeSession or None, optional
        複数フレームで共有する評価セッション。省略時はこの呼び出しだけが所有する。
    presets : PresetCatalog or None, optional
        ``draw`` に束縛する preset snapshot。省略時は現在の snapshot を使う。

    Returns
    -------
    list[RealizedLayer]
        realize 済みの Layer 列。
    """

    if presets is not None and type(presets) is not PresetCatalog:
        raise TypeError("presets は exact PresetCatalog または None です")
    if type(config) is not RuntimeConfig:
        raise TypeError("config は exact RuntimeConfig です")

    evaluation_config = EvaluationConfig(font_dirs=config.font_dirs)

    if session is not None:
        if session.context.config != evaluation_config:
            raise ValueError("session の EvaluationConfig と config.font_dirs が一致しません")
        return _realize_scene_with_session(
            draw,
            t,
            defaults,
            config=config,
            session=session,
            presets=presets,
        )

    context = EvaluationContext(
        catalog=current_operation_catalog(),
        quality=current_preview_quality(),
        config=evaluation_config,
    )
    with RealizeSession(context=context) as owned_session:
        return _realize_scene_with_session(
            draw,
            t,
            defaults,
            config=config,
            session=owned_session,
            presets=presets,
        )


def _realize_scene_with_session(
    draw: Callable[[float], SceneItem],
    t: float,
    defaults: LayerStyleDefaults,
    *,
    config: RuntimeConfig,
    session: RealizeSession,
    presets: PresetCatalog | None,
) -> list[RealizedLayer]:
    """借用中の session で一フレームを評価する。lifecycle は caller が持つ。"""

    preset_catalog = current_preset_catalog() if presets is None else presets
    context = session.context
    with (
        bind_operation_catalog(context.catalog),
        bind_preset_catalog(preset_catalog),
        bind_runtime_config(config),
        preview_quality_context(context.quality),
    ):
        scene = draw(t)
    layers = normalize_scene(scene)

    out: list[RealizedLayer] = []
    total_vertices = 0
    total_lines = 0
    total_bytes = 0
    # 各 layer を評価しないと scene 実測量は分からないため、新しい CPU cache
    # entry は aggregate 検査が完了するまで transaction 内に留める。
    with session.cache_transaction() as cache_transaction:
        for layer_index, layer in enumerate(layers):
            layer_label = layer.name or layer.site_id or f"Layer {layer_index + 1}"
            with session.profile_layer(layer_label):
                resolved = resolve_layer_style(layer, defaults)
                thickness, color = observe_and_apply_layer_style(
                    layer_site_id=layer.site_id,
                    layer_name=layer.name,
                    base_line_thickness=float(resolved.thickness),
                    base_line_color_rgb01=resolved.color,
                    explicit_line_thickness=(layer.thickness is not None),
                    explicit_line_color=(layer.color is not None),
                )

                geometry = resolved.layer.geometry
                # Geometry は L 側で concat 済みのためそのまま扱う。
                realized, cache_key = session.realize_with_key(geometry)
                total_vertices += int(realized.coords.shape[0])
                total_lines += max(0, int(realized.offsets.size) - 1)
                total_bytes += int(realized.byte_size)
                ensure_resource_usage(
                    "scene aggregate",
                    vertices=total_vertices,
                    lines=total_lines,
                    byte_size=total_bytes,
                    budget=session.runtime_limits.scene,
                    hint=("layer 数、各 layer の密度、または final 出力設定を見直してください"),
                )
                out.append(
                    RealizedLayer(
                        layer=resolved.layer,
                        realized=realized,
                        cache_key=cache_key,
                        color=color,
                        thickness=thickness,
                    )
                )

        cache_transaction.commit()

    return out
