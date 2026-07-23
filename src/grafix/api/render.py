"""ヘッドレス描画の共通データ型と長寿命セッションを提供する。

``RenderSession`` は 1 回の ``draw(t)`` ではなく、作品を評価する期間を所有する。
そのため、複数フレームで ParamStore、style 解決器、設定スナップショット、
Geometry の realize cache を再利用できる。ファイルへの保存はこのモジュールの責務に
含めず、描画結果を immutable な ``Frame`` として返すところで止める。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.authoring_loader import authoring_definitions_for_draw
from grafix.core.capture_provenance import (
    CaptureProvenance,
    SessionProvenance,
)
from grafix.core.export_format import ExportFormat
from grafix.core.export_result import ExportResult
from grafix.core.evaluation_context import EvaluationContext, EvaluationResources
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.layer import LayerStyleDefaults
from grafix.core.lifecycle import CleanupErrors
from grafix.core.parameters.context import parameter_context
from grafix.export.output_paths import default_param_store_path
from grafix.export.capture_provenance import CaptureProvenanceBuilder
from grafix.core.parameters.runtime import LoadProvenance, ParameterLoadState
from grafix.core.parameters.source import ParameterLoadMode
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.style_resolver import FrameStyle, StyleResolver
from grafix.core.pipeline import RealizedLayer, realize_scene
from grafix.core.preview_quality import current_preview_quality, preview_quality_context
from grafix.core.preset_catalog import bind_preset_catalog
from grafix.core.realize import RealizeCacheStore, RealizeSession
from grafix.core.render_options import (
    Color,
    ColorInput,
    RGB01,
    RGB8,
    RenderOptions,
)
from grafix.core.runtime_limits import DEFAULT_FINAL_RUNTIME_LIMITS, RuntimeLimits
from grafix.core.runtime_config import RuntimeConfig, bind_runtime_config
from grafix.runtime_config_loader import load_runtime_config
from grafix.core.scene import SceneItem
from grafix.core.value_validation import exact_string_choice, finite_real
from grafix.parameter_storage import read_param_store, recover_param_store_session


@dataclass(frozen=True, slots=True)
class RenderSessionMetadata:
    """セッション開始時に固定した config と parameter load 情報。"""

    config_path: Path | None
    effective_config: RuntimeConfig
    parameter_source: ParameterLoadMode
    parameter_store_path: Path | None
    parameter_load_state: ParameterLoadState
    provenance: SessionProvenance

    def __post_init__(self) -> None:
        if self.config_path is not None and not isinstance(self.config_path, Path):
            raise TypeError("config_path は Path または None である必要があります")
        if not isinstance(self.effective_config, RuntimeConfig):
            raise TypeError("effective_config は RuntimeConfig である必要があります")
        if not isinstance(self.parameter_source, Path):
            exact_string_choice(
                self.parameter_source,
                name="parameter_source",
                choices=("code", "saved", "recovery"),
            )
        if self.parameter_store_path is not None and not isinstance(
            self.parameter_store_path,
            Path,
        ):
            raise TypeError(
                "parameter_store_path は Path または None である必要があります"
            )
        if type(self.parameter_load_state) is not ParameterLoadState:
            raise TypeError("parameter_load_state は exact ParameterLoadState です")
        if not isinstance(self.provenance, SessionProvenance):
            raise TypeError("provenance は SessionProvenance である必要があります")

    @property
    def parameter_load_provenance(self) -> LoadProvenance:
        """capture manifest に記録する load provenance を返す。"""

        return self.parameter_load_state.provenance


@dataclass(frozen=True, slots=True)
class Frame:
    """``RenderSession.render`` が返す 1 フレーム分の不変スナップショット。

    Parameters
    ----------
    t : float
        評価時刻（秒）。
    layers : tuple[RealizedLayer, ...]
        final quality で評価済みの描画 layer。
    options : RenderOptions
        セッションに固定した描画設定。
    style : FrameStyle
        ParamStore 適用後の frame style。
    metadata : RenderSessionMetadata
        effective config と parameter load を含むセッション情報。
    provenance : CaptureProvenance
        この frame と source/config/parameter 状態を結ぶ provenance。
    """

    t: float
    layers: tuple[RealizedLayer, ...]
    options: RenderOptions
    style: FrameStyle
    metadata: RenderSessionMetadata
    provenance: CaptureProvenance

    def __post_init__(self) -> None:
        object.__setattr__(self, "t", finite_real(self.t, name="Frame.t"))
        if type(self.layers) is not tuple or any(
            not isinstance(layer, RealizedLayer)
            for layer in self.layers
        ):
            raise TypeError("Frame.layers は RealizedLayer の tuple である必要があります")
        if not isinstance(self.options, RenderOptions):
            raise TypeError("Frame.options は RenderOptions である必要があります")
        if not isinstance(self.style, FrameStyle):
            raise TypeError("Frame.style は FrameStyle である必要があります")
        if not isinstance(self.metadata, RenderSessionMetadata):
            raise TypeError(
                "Frame.metadata は RenderSessionMetadata である必要があります"
            )
        if not isinstance(self.provenance, CaptureProvenance):
            raise TypeError(
                "Frame.provenance は CaptureProvenance である必要があります"
            )
        if self.provenance.frame.t != self.t:
            raise ValueError(
                "Frame.provenance.frame.t は Frame.t と一致する必要があります"
            )

    @property
    def canvas_size(self) -> tuple[int, int]:
        """論理キャンバス寸法を返す。"""

        return self.options.canvas_size

    @property
    def background_color(self) -> Color:
        """ParamStore override 適用後の背景色を返す。"""

        return Color(self.style.bg_color_rgb01)

    @property
    def background_color_rgb01(self) -> RGB01:
        """ParamStore override 適用後の背景色を内部 RGB01 で返す。"""

        return self.style.bg_color_rgb01


def _load_parameter_store(
    draw: Callable[[float], SceneItem],
    *,
    parameter_source: ParameterLoadMode,
    run_id: str | None,
    config: RuntimeConfig,
) -> tuple[ParamStore, ParameterLoadState, ParameterLoadMode, Path | None]:
    if isinstance(parameter_source, Path):
        source_path = Path(parameter_source).expanduser().resolve(strict=False)
        loaded = read_param_store(source_path)
        return loaded.store, loaded.load_state, source_path, source_path
    if parameter_source == "code":
        return ParamStore(), ParameterLoadState(), "code", None
    if parameter_source not in {"saved", "recovery"}:
        raise ValueError(
            "parameter_source は 'code', 'saved', 'recovery', Path のいずれかです"
        )

    source_path = default_param_store_path(draw, run_id=run_id, config=config)
    if parameter_source == "saved":
        loaded = read_param_store(source_path)
        return loaded.store, loaded.load_state, "saved", source_path
    loaded = recover_param_store_session(source_path)
    return loaded.store, loaded.load_state, "recovery", source_path


def _attempt_render_dependency_cleanup(
    errors: CleanupErrors,
    *,
    realize_session: RealizeSession | None,
    cache_store: RealizeCacheStore | None,
    evaluation_resources: EvaluationResources | None,
) -> None:
    """RenderSession が所有する dependency を構築の逆順に閉じる。"""

    if realize_session is not None:
        errors.attempt(realize_session.close, "close render realize session")
    if cache_store is not None:
        errors.attempt(cache_store.close, "close render realize cache store")
    if evaluation_resources is not None:
        errors.attempt(
            evaluation_resources.close,
            "close render evaluation resources",
        )


class RenderSession:
    """draw/store/config/style/cache を束ねる長寿命ヘッドレス描画セッション。

    Parameters
    ----------
    draw : Callable[[float], SceneItem]
        フレーム時刻を受け取り SceneItem を返す作品関数。
    options : RenderOptions, optional
        描画設定。省略時は ``RenderOptions()``。
    parameter_source : {"code", "saved", "recovery"} or Path, optional
        ``"code"`` はファイルを読まない空 store（headless の既定）、``"saved"`` は
        通常保存、``"recovery"`` は新しい session recovery を考慮し、``Path`` は
        そのファイルだけを読む。
    config_path : str or Path or None, optional
        明示 config。指定時はセッション作成時に読み、effective config を metadata へ固定する。
    config : RuntimeConfig or None, optional
        呼び出し元で確定済みの config。``config_path`` との同時指定はできない。
    run_id : str or None, optional
        saved/recovery の既定 ParamStore path に使う suffix。
    runtime_limits : RuntimeLimits, optional
        final render の operation/scene/cache/capture 上限。
    seed : int or None, optional
        manifest に固定する作品 seed。乱数 global state は変更しない。
    definitions : AuthoringDefinitionsSnapshot or None, optional
        呼び出し元で確定済みの operation/preset snapshot。省略時は draw に付与された
        generation snapshot、または builtin/default/config authoring definitions を
        セッション構築時に一度だけ解決する。
    """

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        options: RenderOptions | None = None,
        parameter_source: ParameterLoadMode = "code",
        config_path: str | Path | None = None,
        config: RuntimeConfig | None = None,
        run_id: str | None = None,
        runtime_limits: RuntimeLimits = DEFAULT_FINAL_RUNTIME_LIMITS,
        seed: int | None = None,
        definitions: AuthoringDefinitionsSnapshot | None = None,
    ) -> None:
        if not callable(draw):
            raise TypeError("draw は callable である必要があります")
        if options is not None and not isinstance(options, RenderOptions):
            raise TypeError("options は RenderOptions である必要があります")

        # config は構築時に一度だけ pure loader で確定する。session の存続期間を
        # process-global scope へ対応付けないため、close 順や thread 間で干渉しない。
        if config is not None and not isinstance(config, RuntimeConfig):
            raise TypeError("config は RuntimeConfig または None である必要があります")
        if config is not None and config_path is not None:
            raise ValueError("config と config_path は同時に指定できません")
        effective_config = load_runtime_config(config_path) if config is None else config
        store, parameter_load_state, normalized_source, store_path = _load_parameter_store(
            draw,
            parameter_source=parameter_source,
            run_id=run_id,
            config=effective_config,
        )
        render_options = RenderOptions() if options is None else options
        style_resolver = StyleResolver(
            store,
            base_background_color_rgb01=render_options.background_color.rgb01,
            base_global_thickness=render_options.line_thickness,
            base_global_line_color_rgb01=render_options.line_color.rgb01,
        )

        if not isinstance(runtime_limits, RuntimeLimits):
            raise TypeError("runtime_limits は RuntimeLimits である必要があります")
        if definitions is not None and type(definitions) is not AuthoringDefinitionsSnapshot:
            raise TypeError(
                "definitions は exact AuthoringDefinitionsSnapshot または None です"
            )

        provenance_builder = CaptureProvenanceBuilder(
            draw,
            config=effective_config,
            parameter_source=normalized_source,
            parameter_store_path=store_path,
            parameter_load_provenance=parameter_load_state.provenance,
            seed=seed,
        )
        session_definitions = authoring_definitions_for_draw(
            draw,
            config=effective_config,
            definitions=definitions,
        )
        evaluation_context = EvaluationContext(
            catalog=session_definitions.operations,
            quality="final",
            config=EvaluationConfig(font_dirs=effective_config.font_dirs),
        )
        evaluation_resources = EvaluationResources()
        cache_store: RealizeCacheStore | None = None
        realize_session: RealizeSession | None = None
        try:
            cache_store = RealizeCacheStore.from_runtime_limits(runtime_limits)
            realize_session = RealizeSession(
                context=evaluation_context,
                resources=evaluation_resources,
                cache_store=cache_store,
                runtime_limits=runtime_limits,
            )
            metadata = RenderSessionMetadata(
                config_path=effective_config.config_path,
                effective_config=effective_config,
                parameter_source=normalized_source,
                parameter_store_path=store_path,
                parameter_load_state=parameter_load_state,
                provenance=provenance_builder.session,
            )
        except BaseException as error:
            errors = CleanupErrors(initial_error=error)
            _attempt_render_dependency_cleanup(
                errors,
                realize_session=realize_session,
                cache_store=cache_store,
                evaluation_resources=evaluation_resources,
            )
            errors.raise_if_any()
            raise

        assert cache_store is not None
        assert realize_session is not None

        self._draw = draw
        self._options = render_options
        self._store = store
        self._config = effective_config
        self._style_resolver = style_resolver
        self._runtime_limits = runtime_limits
        self._definitions = session_definitions
        self._evaluation_context = evaluation_context
        self._evaluation_resources = evaluation_resources
        self._cache_store = cache_store
        self._realize_session = realize_session
        self._provenance_builder = provenance_builder
        self._frame_index = 0
        self._metadata = metadata
        self._closed = False

    def __enter__(self) -> RenderSession:
        if self._closed:
            raise RuntimeError("close 済みの RenderSession は再利用できません")
        return self

    def __exit__(
        self,
        _exc_type: object,
        exc: BaseException | None,
        _traceback: object,
    ) -> None:
        errors = CleanupErrors(initial_error=exc)
        self._close_with_errors(errors)
        if exc is None:
            errors.raise_if_any()

    @property
    def options(self) -> RenderOptions:
        return self._options

    @property
    def param_store(self) -> ParamStore:
        return self._store

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    @property
    def runtime_limits(self) -> RuntimeLimits:
        return self._runtime_limits

    @property
    def metadata(self) -> RenderSessionMetadata:
        return self._metadata

    def render(
        self,
        t: float,
        *,
        provenance_seed: int | None | Literal["session"] = "session",
    ) -> Frame:
        """時刻 ``t`` を評価し、保存処理から独立した ``Frame`` を返す。

        Parameters
        ----------
        t : float
            評価時刻。
        provenance_seed : int, None, or {"session"}, optional
            ``"session"`` は session 構築時の seed を使う。int/None はこの
            Frame/manifest の provenance だけを上書きする。乱数 global state
            や draw の実行環境は変更しない。
        """

        if self._closed:
            raise RuntimeError("close 済みの RenderSession は使用できません")
        if provenance_seed != "session" and provenance_seed is not None and (
            isinstance(provenance_seed, bool)
            or not isinstance(provenance_seed, int)
        ):
            raise TypeError("provenance_seed は int、None、'session' のいずれかです")
        render_t = finite_real(t, name="t")

        # RenderSession は headless/final 契約を所有する。呼び出し元が interactive
        # preview の draft context 内にいても、その暗黙状態を session へ漏らさない。
        with (
            bind_runtime_config(self._config),
            preview_quality_context("final"),
            bind_preset_catalog(self._definitions.presets),
        ):
            style = self._style_resolver.resolve()
            defaults = LayerStyleDefaults(
                color=style.global_line_color_rgb01,
                thickness=style.global_thickness,
            )
            with parameter_context(self._store):
                layers = tuple(
                    realize_scene(
                        self._draw,
                        render_t,
                        defaults,
                        config=self._config,
                        session=self._realize_session,
                    )
                )
            provenance = self._provenance_builder.frame(
                self._store,
                t=render_t,
                frame_index=self._frame_index,
                quality=current_preview_quality(),
                origin="headless",
                provenance_seed=provenance_seed,
            )
        frame = Frame(
            t=render_t,
            layers=layers,
            options=self._options,
            style=style,
            metadata=self._metadata,
            provenance=provenance,
        )
        self._frame_index += 1
        return frame

    def close(self) -> None:
        """realize cache を解放し、以後の評価を禁止する。"""

        errors = CleanupErrors()
        self._close_with_errors(errors)
        errors.raise_if_any()

    def _close_with_errors(self, errors: CleanupErrors) -> None:
        """close 済みにし、依存 resource の失敗を ``errors`` へ集約する。"""

        if self._closed:
            return
        self._closed = True
        _attempt_render_dependency_cleanup(
            errors,
            realize_session=self._realize_session,
            cache_store=self._cache_store,
            evaluation_resources=self._evaluation_resources,
        )


def render(
    draw: Callable[[float], SceneItem],
    t: float = 0.0,
    *,
    options: RenderOptions | None = None,
    parameter_source: ParameterLoadMode = "code",
    config_path: str | Path | None = None,
    config: RuntimeConfig | None = None,
    run_id: str | None = None,
    runtime_limits: RuntimeLimits = DEFAULT_FINAL_RUNTIME_LIMITS,
    seed: int | None = None,
    definitions: AuthoringDefinitionsSnapshot | None = None,
) -> Frame:
    """``draw(t)`` を final 品質で一度評価し、不変 ``Frame`` を返す。

    ファイル保存は行わない。複数時刻を評価する場合は、store/config/cache を再利用する
    :class:`RenderSession` を直接使用する。

    Parameters
    ----------
    draw : Callable[[float], SceneItem]
        フレーム時刻を受け取り SceneItem を返す作品関数。
    t : float, optional
        評価する時刻。既定は ``0.0``。
    options : RenderOptions or None, optional
        描画設定。省略時は ``RenderOptions()``。
    parameter_source : {"code", "saved", "recovery"} or Path, optional
        parameter の読み込み元。headless 既定の ``"code"`` は暗黙ファイルを読まない。
    config_path : str, Path or None, optional
        明示 config path。指定時は探索より優先する。
    config : RuntimeConfig or None, optional
        呼び出し元で確定済みの config。``config_path`` との同時指定はできない。
    run_id : str or None, optional
        saved/recovery の既定 ParamStore path に使う suffix。
    runtime_limits : RuntimeLimits, optional
        final render の統合上限。
    seed : int or None, optional
        manifest に固定する作品 seed。乱数 global state は変更しない。
    definitions : AuthoringDefinitionsSnapshot or None, optional
        呼び出し元で確定済みの operation/preset snapshot。省略時は
        :class:`RenderSession` の通常規則で一度だけ解決する。

    Returns
    -------
    Frame
        保存処理から独立した final 品質のフレーム。
    """

    with RenderSession(
        draw,
        options=options,
        parameter_source=parameter_source,
        config_path=config_path,
        config=config,
        run_id=run_id,
        runtime_limits=runtime_limits,
        seed=seed,
        definitions=definitions,
    ) as session:
        return session.render(t)


__all__ = [
    "Color",
    "ColorInput",
    "ExportFormat",
    "ExportResult",
    "Frame",
    "ParameterLoadMode",
    "RGB01",
    "RGB8",
    "RenderOptions",
    "RenderSession",
    "RenderSessionMetadata",
    "render",
]
