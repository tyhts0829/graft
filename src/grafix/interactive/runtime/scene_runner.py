"""
Purpose:
    sync/mpのdraw評価をrealizeへ接続し、採用中authoring generationのlast-good sceneを供給する。
Use when:
    評価戦略、source reload世代交換、draft/final resource、失敗時の表示継続を変更するとき。
Constraints:
    - replacement generationは全context/resource/workerを構築してから一括swapする。
    - replacement構築失敗では現世代を変えず、成功後に旧世代の子resourceを閉じる。
    - RealizeCacheStoreとParamStoreの寿命はdraw generation交換を越えて維持する。
    - mp workerはdraw/normalizeまでとし、realizeとresource budget適用は親側で行う。
    - 評価失敗や結果待ちでlast-good geometryを失わず、fresh成功時だけframe metadataを進める。
Side effects:
    evaluation resource/cacheと必要時のmp-draw worker generationを所有する。
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable, Sequence
from typing import Protocol

from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.authoring_loader import authoring_definitions_for_draw
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.evaluation_context import EvaluationContext, EvaluationResources
from grafix.core.layer import Layer, LayerStyleDefaults, resolve_layer_style
from grafix.core.lifecycle import CleanupErrors
from grafix.core.operation_catalog import OperationCatalog
from grafix.core.operation_diagnostics import (
    OperationDiagnostic,
    OperationDiagnosticBuffer,
    extend_operation_diagnostics,
)
from grafix.core.parameters import (
    EffectOrderSnapshot,
    MidiFrameSnapshot,
    ParamStore,
    current_effect_order_snapshot,
    current_frame_params,
    current_param_snapshot,
    parameter_context,
)
from grafix.core.parameters.layer_style import observe_and_apply_layer_style
from grafix.core.parameters.snapshot_ops import ParamSnapshot
from grafix.core.pipeline import RealizedLayer, realize_scene
from grafix.core.realize import RealizeCacheStore, RealizeSession
from grafix.core.preview_quality import PreviewQuality
from grafix.core.resource_budget import ResourceLimitError
from grafix.core.runtime_limits import (
    DEFAULT_RUNTIME_LIMIT_PROFILES,
    RuntimeLimitProfiles,
)
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.scene import SceneItem
from grafix.core.value_validation import exact_integer, finite_real
from grafix.interactive.runtime.mp_draw import DrawResult, MpDraw, MpDrawStats
from grafix.interactive.runtime.perf import PerfCollector
from grafix.interactive.diagnostics import DiagnosticCenter, DiagnosticEvent


_logger = logging.getLogger(__name__)


class _MpDrawEventCallback(Protocol):
    """MpDraw が親 process の causal event を通知する callback。"""

    def __call__(
        self,
        name: str,
        *,
        frame_id: int | None = None,
        revision: int | None = None,
    ) -> None: ...


class _MpDrawClient(Protocol):
    """SceneRunner が background draw に要求する最小契約。"""

    @property
    def stats(self) -> MpDrawStats: ...

    def submit(
        self,
        *,
        t: float,
        snapshot_revision: int,
        snapshot: ParamSnapshot,
        effect_order_snapshot: EffectOrderSnapshot,
        cc_snapshot: MidiFrameSnapshot | None,
        epoch: int,
        quality: PreviewQuality,
    ) -> None: ...

    def poll_latest(self) -> DrawResult | None: ...

    def latest_successful_result(self) -> DrawResult | None: ...

    def begin_epoch(self, epoch: int | None = None) -> int: ...

    def close(self) -> None: ...


class _MpDrawFactory(Protocol):
    """一つの draw generation に対応する background client を作る。"""

    def __call__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        n_worker: int,
        evaluation_timeout: float | None,
        effective_config: RuntimeConfig,
        definitions: AuthoringDefinitionsSnapshot,
        event_callback: _MpDrawEventCallback | None,
    ) -> _MpDrawClient: ...


def _default_mp_draw_factory(
    draw: Callable[[float], SceneItem],
    *,
    n_worker: int,
    evaluation_timeout: float | None,
    effective_config: RuntimeConfig,
    definitions: AuthoringDefinitionsSnapshot,
    event_callback: _MpDrawEventCallback | None,
) -> _MpDrawClient:
    """production の concrete MpDraw を生成する既定 factory。"""

    return MpDraw(
        draw,
        n_worker=n_worker,
        evaluation_timeout=evaluation_timeout,
        effective_config=effective_config,
        definitions=definitions,
        event_callback=event_callback,
    )


def _make_evaluation_generation(
    definitions: AuthoringDefinitionsSnapshot,
    *,
    config: RuntimeConfig,
    profiles: RuntimeLimitProfiles,
    cache_store: RealizeCacheStore,
    profiler: PerfCollector,
) -> tuple[
    OperationCatalog,
    dict[PreviewQuality, EvaluationContext],
    EvaluationResources,
    dict[PreviewQuality, RealizeSession],
]:
    """一つの immutable catalog generation と子 session を構築する。"""

    if type(definitions) is not AuthoringDefinitionsSnapshot:
        raise TypeError("definitions は exact AuthoringDefinitionsSnapshot です")
    catalog = definitions.operations
    qualities: tuple[PreviewQuality, PreviewQuality] = ("draft", "final")
    contexts: dict[PreviewQuality, EvaluationContext] = {
        quality: EvaluationContext(
            catalog=catalog,
            quality=quality,
            config=EvaluationConfig(font_dirs=config.font_dirs),
        )
        for quality in qualities
    }
    resources = EvaluationResources()
    sessions: dict[PreviewQuality, RealizeSession] = {}
    try:
        for quality in qualities:
            limits = profiles.for_quality(quality)
            sessions[quality] = RealizeSession(
                context=contexts[quality],
                resources=resources,
                cache_store=cache_store,
                runtime_limits=limits,
                profiler=profiler,
            )
    except BaseException as error:
        errors = CleanupErrors(initial_error=error)
        _attempt_evaluation_generation_cleanup(errors, sessions, resources)
        errors.raise_if_any()
        raise
    return catalog, contexts, resources, sessions


def _attempt_evaluation_generation_cleanup(
    errors: CleanupErrors,
    sessions: dict[PreviewQuality, RealizeSession],
    resources: EvaluationResources,
) -> None:
    """generation の全 close を、呼び出し側の error 集約器へ記録する。"""

    for index, session in enumerate(tuple(dict.fromkeys(sessions.values())), start=1):
        errors.attempt(
            session.close,
            f"close evaluation session {index}",
        )
    errors.attempt(resources.close, "close evaluation resources")


class SceneRunner:
    """このフレームで描くべき realized_layers を生成する。"""

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        perf: PerfCollector,
        n_worker: int,
        evaluation_timeout: float | None = 5.0,
        runtime_limit_profiles: RuntimeLimitProfiles = DEFAULT_RUNTIME_LIMIT_PROFILES,
        diagnostic_center: DiagnosticCenter | None = None,
        effective_config: RuntimeConfig,
        definitions: AuthoringDefinitionsSnapshot | None = None,
        mp_draw_factory: _MpDrawFactory = _default_mp_draw_factory,
    ) -> None:
        worker_count = exact_integer(n_worker, name="n_worker", minimum=0)
        timeout = (
            None
            if evaluation_timeout is None
            else finite_real(
                evaluation_timeout,
                name="evaluation_timeout",
                minimum=0.0,
                minimum_inclusive=False,
            )
        )

        if not isinstance(runtime_limit_profiles, RuntimeLimitProfiles):
            raise TypeError(
                "runtime_limit_profiles は RuntimeLimitProfiles である必要があります"
            )

        self._draw = draw
        self._perf = perf
        self._worker_count = worker_count
        self._evaluation_timeout = timeout
        self._runtime_limit_profiles = runtime_limit_profiles
        self._mp_draw_factory = mp_draw_factory
        if not isinstance(effective_config, RuntimeConfig):
            raise TypeError("effective_config は RuntimeConfig である必要があります")
        self._effective_config = effective_config
        selected_definitions = authoring_definitions_for_draw(
            draw,
            config=effective_config,
            definitions=definitions,
        )
        cache_store = RealizeCacheStore(
            max_bytes=max(
                runtime_limit_profiles.preview.cpu_cache_bytes,
                runtime_limit_profiles.final.cpu_cache_bytes,
            ),
            max_entries=max(
                runtime_limit_profiles.preview.cpu_cache_entries,
                runtime_limit_profiles.final.cpu_cache_entries,
            ),
        )
        try:
            (
                operation_catalog,
                evaluation_contexts,
                evaluation_resources,
                realize_sessions,
            ) = _make_evaluation_generation(
                selected_definitions,
                config=effective_config,
                profiles=runtime_limit_profiles,
                cache_store=cache_store,
                profiler=perf,
            )
        except BaseException as error:
            errors = CleanupErrors(initial_error=error)
            errors.attempt(cache_store.close, "close scene realize cache store")
            errors.raise_if_any()
            raise
        self._cache_store = cache_store
        self._definitions = selected_definitions
        self._operation_catalog = operation_catalog
        self._evaluation_contexts = evaluation_contexts
        self._evaluation_resources = evaluation_resources
        self._realize_sessions = realize_sessions
        self._diagnostic_center = diagnostic_center
        self._last_operation_diagnostics: tuple[OperationDiagnostic, ...] = ()
        try:
            self._mp_draw: _MpDrawClient | None = self._create_mp_draw(
                draw,
                definitions=selected_definitions,
            )
        except BaseException as error:
            errors = CleanupErrors(initial_error=error)
            _attempt_evaluation_generation_cleanup(
                errors,
                self._realize_sessions,
                self._evaluation_resources,
            )
            errors.attempt(
                self._cache_store.close,
                "close scene realize cache store",
            )
            errors.raise_if_any()
            raise
        # mp-draw は結果未到着の frame でも前回 scene を再利用して返すため、単なる
        # run() の正常 return だけでは user draw の回復を判定できない。None は
        # 「この呼び出しでは新しい評価結果が無い」を表す。
        self._last_evaluation_succeeded: bool | None = None
        self._last_output_updated = False
        self._last_evaluation_t: float | None = None
        # `last_evaluation_*` は worker から新しい終端結果を受け取ったかを
        # 表す。一方、後続 error と同時に drain された成功 frame は、
        # error 通知の次の run で初めて realize される。その出力時刻を
        # manifest と結び付けるため、終端 status とは別に保持する。
        self._last_realized_t: float | None = None
        self._last_realized_snapshot_revision: int | None = None
        self._last_merged_mp_success_frame_id: int | None = None
        self._last_merged_mp_success_epoch: int | None = None
        # transport discontinuity 後に fresh mp result を待つ間も、実際に
        # 画面へ出していた frame とその時刻を対で維持する。
        self._retained_realized_layers: list[RealizedLayer] = []
        self._retained_source_layers: list[Layer] = []
        self._retained_style_revision = -1
        self._retained_realized_t: float | None = None
        self._retained_realized_snapshot_revision: int | None = None
        self._retained_realized_frame_id: int | None = None
        self._last_realized_frame_id: int | None = None
        self._waiting_for_fresh_result = False
        self._mp_epoch = 0
        self._last_transport_epoch: int | None = None
        self._last_recording: bool | None = None
        self._last_quality: PreviewQuality | None = None

    def _create_mp_draw(
        self,
        draw: Callable[[float], SceneItem],
        *,
        definitions: AuthoringDefinitionsSnapshot,
    ) -> _MpDrawClient | None:
        """設定済みの同一 factory から generation client を作る。"""

        if self._worker_count < 1:
            return None
        return self._mp_draw_factory(
            draw,
            n_worker=self._worker_count,
            evaluation_timeout=self._evaluation_timeout,
            effective_config=self._effective_config,
            definitions=definitions,
            event_callback=(self._perf.record_event if self._perf.enabled else None),
        )

    def replace_draw(
        self,
        draw: Callable[[float], SceneItem],
        *,
        definitions: AuthoringDefinitionsSnapshot | None = None,
    ) -> None:
        """draw callable と background worker 世代を一つの境界で交換する。

        新 worker の構築に失敗した場合は現在の callable/worker/last-good frame を
        変更しない。成功時は旧 worker の結果を無効化し、次の fresh result までは
        直近の表示を維持する。ParamStore と realize cache の寿命は変えない。
        """

        if not callable(draw):
            raise TypeError("draw は callable である必要があります")

        next_epoch = self._mp_epoch + 1
        replacement: _MpDrawClient | None = None
        next_definitions = authoring_definitions_for_draw(
            draw,
            config=self._effective_config,
            definitions=definitions,
        )
        (
            next_catalog,
            next_contexts,
            next_resources,
            next_sessions,
        ) = _make_evaluation_generation(
            next_definitions,
            config=self._effective_config,
            profiles=self._runtime_limit_profiles,
            cache_store=self._cache_store,
            profiler=self._perf,
        )
        try:
            replacement = self._create_mp_draw(
                draw,
                definitions=next_definitions,
            )
            if replacement is not None:
                replacement.begin_epoch(next_epoch)
        except BaseException as startup_error:  # noqa: BLE001
            errors = CleanupErrors(initial_error=startup_error)
            if replacement is not None:
                errors.attempt(
                    replacement.close,
                    "close replacement draw worker",
                )
            _attempt_evaluation_generation_cleanup(
                errors,
                next_sessions,
                next_resources,
            )
            errors.raise_if_any()
            raise

        previous = self._mp_draw
        previous_sessions = self._realize_sessions
        previous_resources = self._evaluation_resources
        self._draw = draw
        self._mp_draw = replacement
        self._definitions = next_definitions
        self._operation_catalog = next_catalog
        self._evaluation_contexts = next_contexts
        self._evaluation_resources = next_resources
        self._realize_sessions = next_sessions
        self._mp_epoch = next_epoch
        self._last_merged_mp_success_frame_id = None
        self._last_merged_mp_success_epoch = None
        self._last_evaluation_succeeded = None
        self._last_output_updated = False
        self._last_evaluation_t = None
        self._last_realized_t = self._retained_realized_t
        self._last_realized_snapshot_revision = (
            self._retained_realized_snapshot_revision
        )
        self._last_realized_frame_id = self._retained_realized_frame_id
        self._waiting_for_fresh_result = replacement is not None

        close_errors = CleanupErrors()
        if previous is not None:
            close_errors.attempt(previous.close, "close previous draw worker")
        _attempt_evaluation_generation_cleanup(
            close_errors,
            previous_sessions,
            previous_resources,
        )
        try:
            close_errors.raise_if_any()
        except BaseException as first_close_error:  # noqa: BLE001
            center = self._diagnostic_center
            if center is not None:
                details = "".join(
                    traceback.format_exception_only(
                        type(first_close_error),
                        first_close_error,
                    )
                ).rstrip()
                center.publish(
                    DiagnosticEvent(
                        category="reload",
                        severity="warning",
                        summary="旧 draw generation の終了に失敗しました",
                        details=details,
                        dedupe_key=(
                            "reload-old-generation-close:"
                            f"{type(first_close_error).__name__}:{first_close_error}"
                        ),
                    )
                )
            else:
                _logger.warning(
                    "旧 draw generation の終了に失敗しました",
                    exc_info=first_close_error,
                )

    @property
    def last_evaluation_succeeded(self) -> bool | None:
        """直近 `run()` で新しい scene 評価が成功したかを返す。

        `None` は mp-draw の結果待ちなど、この frame で新しい評価結果が無かった状態。
        DrawWindowSystem はこの値を使い、error 表示を本当の成功結果まで保持する。
        """

        return self._last_evaluation_succeeded

    @property
    def last_output_updated(self) -> bool:
        """直近 `run()` で新しい realized geometry を表示へ採用したか。"""

        return bool(self._last_output_updated)

    @property
    def last_evaluation_t(self) -> float | None:
        """直近に新しく成功した scene が評価された `t` を返す。"""

        return self._last_evaluation_t

    @property
    def last_realized_t(self) -> float | None:
        """直近の `run()` が実際に realize して返した成功 frame の `t`。

        mp-draw で成功 frame とより新しい error frame が同時に
        drain された場合、`last_evaluation_succeeded` は error 表示を
        維持するため `True` にしない。それでも、次の `run()` で
        実際に描画出力となった成功 frame の時刻はこの値で返す。
        """

        return self._last_realized_t

    @property
    def last_realized_snapshot_revision(self) -> int | None:
        """直近の `run()` が実際に返した表示 frame の parameter revision。

        sync 評価では run 開始時の store revision、MP 評価では worker が実際に
        適用した snapshot revision を返す。fresh MP result 待ちで直前の表示を
        保持する場合は、その保持中 frame の revision を返す。
        """

        return self._last_realized_snapshot_revision

    @property
    def last_realized_frame_id(self) -> int | None:
        """直近の表示候補を作った MP frame ID。同期評価では ``None``。"""

        return self._last_realized_frame_id

    @property
    def is_waiting_for_fresh_result(self) -> bool:
        """不連続操作後の current-epoch mp result を待っているか。"""

        return bool(self._waiting_for_fresh_result)

    @property
    def last_operation_diagnostics(self) -> tuple[OperationDiagnostic, ...]:
        """直近 evaluation で収集した immutable operation 診断を返す。"""

        return self._last_operation_diagnostics

    @property
    def operation_catalog(self) -> OperationCatalog:
        """現在の draw generation が所有する immutable catalog。"""

        return self._operation_catalog

    @property
    def definitions(self) -> AuthoringDefinitionsSnapshot:
        """現在の draw generation が所有する authoring snapshot。"""

        return self._definitions

    @property
    def evaluation_contexts(self) -> tuple[EvaluationContext, EvaluationContext]:
        """draft/final 順の immutable evaluation context を返す。"""

        return (
            self._evaluation_contexts["draft"],
            self._evaluation_contexts["final"],
        )

    @property
    def cache_store(self) -> RealizeCacheStore:
        """全 draw generation が共有する bounded CPU cache。"""

        return self._cache_store

    def _commit_operation_diagnostics(
        self,
        buffer: OperationDiagnosticBuffer | None,
    ) -> None:
        diagnostics = () if buffer is None else buffer.snapshot()
        self._last_operation_diagnostics = diagnostics
        center = self._diagnostic_center
        if center is None:
            return
        for diagnostic in diagnostics:
            center.publish(
                DiagnosticEvent(
                    category="operation",
                    severity=diagnostic.severity,
                    summary=f"{diagnostic.op}: {diagnostic.reason}",
                    details=(
                        f"original={diagnostic.original_value!r}\n"
                        f"effective={diagnostic.effective_value!r}\n"
                        f"reason={diagnostic.reason}"
                    ),
                    dedupe_key=f"operation:{diagnostic.identity()!r}",
                )
            )

    def _publish_resource_limit(self, error: Exception) -> None:
        center = self._diagnostic_center
        if center is None:
            return
        current: BaseException | None = error
        seen: set[int] = set()
        resource_error: ResourceLimitError | None = None
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, ResourceLimitError):
                resource_error = current
                break
            current = current.__cause__
        if resource_error is None:
            return
        message = str(resource_error)
        center.publish(
            DiagnosticEvent(
                category="resource",
                severity="error",
                summary=message,
                details=message,
                dedupe_key=f"resource-limit:{message}",
            )
        )

    def run(
        self,
        t: float,
        *,
        store: ParamStore,
        cc_snapshot: MidiFrameSnapshot | None,
        defaults: LayerStyleDefaults,
        recording: bool,
        transport_epoch: int,
        quality: PreviewQuality,
    ) -> list[RealizedLayer]:
        """シーンを実行して realized_layers を返す。

        Raises
        ------
        MpDrawWorkerError
            mp-draw worker が予期せず終了した場合。同期実行には切り替えない。
        """

        if quality not in {"draft", "final"}:
            raise ValueError(f"unknown preview quality: {quality!r}")
        if recording and quality != "final":
            raise ValueError(
                "recording=True の場合は quality='final' が必要です"
            )
        self._last_evaluation_succeeded = None
        self._last_output_updated = False
        self._last_evaluation_t = None
        self._last_realized_t = None
        self._last_realized_snapshot_revision = None
        self._last_realized_frame_id = None
        self._waiting_for_fresh_result = False
        operation_diagnostics: OperationDiagnosticBuffer | None = None
        input_snapshot_revision = int(store.revision)
        try:
            self._update_epoch(
                recording=bool(recording),
                transport_epoch=transport_epoch,
                quality=quality,
            )
            with parameter_context(
                store, cc_snapshot=cc_snapshot
            ) as operation_diagnostics:
                if quality == "final" or self._mp_draw is None:
                    realized_layers = self._run_sync(
                        t,
                        defaults=defaults,
                        quality=quality,
                    )
                    frame_params = current_frame_params()
                    if frame_params is not None:
                        frame_params.complete_effect_chain_observation()
                    self._last_evaluation_succeeded = True
                    self._last_output_updated = True
                    self._last_evaluation_t = float(t)
                    self._last_realized_t = float(t)
                    self._last_realized_snapshot_revision = input_snapshot_revision
                    self._retain_output(
                        realized_layers,
                        t=float(t),
                        snapshot_revision=input_snapshot_revision,
                        frame_id=None,
                        source_layers=[item.layer for item in realized_layers],
                        style_revision=int(store.style_revision),
                    )
                    return realized_layers
                return self._run_mp(
                    t,
                    snapshot_revision=store.revision,
                    cc_snapshot=cc_snapshot,
                    defaults=defaults,
                    quality=quality,
                    style_revision=int(store.style_revision),
                )
        except Exception as exc:
            self._last_evaluation_succeeded = False
            self._last_evaluation_t = None
            self._publish_resource_limit(exc)
            raise
        finally:
            self._commit_operation_diagnostics(operation_diagnostics)

    def _update_epoch(
        self,
        *,
        recording: bool,
        transport_epoch: int,
        quality: PreviewQuality,
    ) -> None:
        """transport/recording の不連続を mp generation へ写像する。"""

        discontinuity = False
        if isinstance(transport_epoch, bool) or not isinstance(
            transport_epoch, int
        ):
            raise TypeError("transport_epoch は int である必要があります")
        requested = transport_epoch
        if requested < 0:
            raise ValueError("transport_epoch は 0 以上である必要があります")
        previous = self._last_transport_epoch
        if previous is not None and requested < previous:
            raise ValueError(
                "transport_epoch は単調増加である必要があります: "
                f"previous={previous}, got={requested}"
            )
        if previous is None:
            # SceneRunner 作成前に seek 済みの場合も、epoch=0 の既定 task と
            # 混同しない。ただし初期値 0 は通常の連続開始として扱う。
            discontinuity = requested > 0
        elif requested != previous:
            discontinuity = True
        self._last_transport_epoch = requested

        previous_recording = self._last_recording
        if previous_recording is not None and recording != previous_recording:
            # 録画中は同期 draw に切り替わる。開始・終了の両側で旧 preview
            # task/result を無効化し、終了後に録画前へ巻き戻らないようにする。
            discontinuity = True
        self._last_recording = bool(recording)

        previous_quality = self._last_quality
        if previous_quality is not None and quality != previous_quality:
            discontinuity = True
        self._last_quality = quality

        if not discontinuity or self._mp_draw is None:
            return
        self._mp_epoch += 1
        self._mp_draw.begin_epoch(self._mp_epoch)
        self._last_merged_mp_success_frame_id = None
        self._last_merged_mp_success_epoch = None

    def _retain_output(
        self,
        layers: list[RealizedLayer],
        *,
        t: float,
        snapshot_revision: int,
        frame_id: int | None,
        source_layers: list[Layer],
        style_revision: int,
    ) -> None:
        """実表示として採用できた layers/t/revision を同時に更新する。"""

        self._retained_realized_layers = list(layers)
        self._retained_source_layers = list(source_layers)
        self._retained_style_revision = int(style_revision)
        self._retained_realized_t = float(t)
        self._retained_realized_snapshot_revision = int(snapshot_revision)
        self._retained_realized_frame_id = (
            None if frame_id is None else int(frame_id)
        )

    def _fresh_result_pending_output(self) -> list[RealizedLayer]:
        """fresh mp result 待ち中に直近の実表示 frame を返す。"""

        self._waiting_for_fresh_result = True
        self._last_realized_t = self._retained_realized_t
        self._last_realized_snapshot_revision = (
            self._retained_realized_snapshot_revision
        )
        self._last_realized_frame_id = self._retained_realized_frame_id
        return list(self._retained_realized_layers)

    def _restyle_retained_output(
        self,
        layers: Sequence[Layer],
        *,
        defaults: LayerStyleDefaults,
        style_revision: int,
    ) -> list[RealizedLayer]:
        """保持 geometry に現在の global/layer style だけを再適用する。"""

        if int(style_revision) == self._retained_style_revision:
            return self._fresh_result_pending_output()
        retained = self._retained_realized_layers
        if len(layers) != len(retained):
            raise RuntimeError("retained scene layer count is inconsistent")
        styled: list[RealizedLayer] = []
        for layer, item in zip(layers, retained, strict=True):
            resolved = resolve_layer_style(layer, defaults)
            thickness, color = observe_and_apply_layer_style(
                layer_site_id=layer.site_id,
                layer_name=layer.name,
                base_line_thickness=float(resolved.thickness),
                base_line_color_rgb01=resolved.color,
                explicit_line_thickness=(layer.thickness is not None),
                explicit_line_color=(layer.color is not None),
            )
            styled.append(
                RealizedLayer(
                    layer=resolved.layer,
                    realized=item.realized,
                    cache_key=item.cache_key,
                    color=color,
                    thickness=thickness,
                )
            )
        self._retained_realized_layers = styled
        self._retained_source_layers = list(layers)
        self._retained_style_revision = int(style_revision)
        return self._fresh_result_pending_output()

    def _run_sync(
        self,
        t: float,
        *,
        defaults: LayerStyleDefaults,
        quality: PreviewQuality,
    ) -> list[RealizedLayer]:
        perf = self._perf

        draw_fn = self._draw
        if perf.enabled:

            def draw_fn_timed(t_arg: float) -> SceneItem:
                with perf.section("draw"):
                    return self._draw(t_arg)

            draw_fn = draw_fn_timed

        with perf.section("scene"):
            return realize_scene(
                draw_fn,
                t,
                defaults,
                config=self._effective_config,
                session=self._realize_sessions[quality],
                presets=self._definitions.presets,
            )

    def _run_mp(
        self,
        t: float,
        *,
        snapshot_revision: int,
        cc_snapshot: MidiFrameSnapshot | None,
        defaults: LayerStyleDefaults,
        quality: PreviewQuality,
        style_revision: int,
    ) -> list[RealizedLayer]:
        perf = self._perf
        mp_draw = self._mp_draw
        assert mp_draw is not None

        # 1) draw（worker 側）: 入力を投げて、届いた観測結果だけ main のバッファへマージする。
        # submit/poll の worker health error は意図的に伝播させる。worker 数を減らした継続や
        # sync draw への暗黙 fallback は、処理量と結果順序を実行中に変えるため行わない。
        mp_draw.submit(
            t=t,
            snapshot_revision=int(snapshot_revision),
            snapshot=current_param_snapshot(),
            effect_order_snapshot=current_effect_order_snapshot(),
            cc_snapshot=cc_snapshot,
            epoch=int(self._mp_epoch),
            quality=quality,
        )
        stats = mp_draw.stats
        perf.record_event(
            "mp_task_submitted",
            frame_id=stats.last_submitted_frame_id,
            revision=int(snapshot_revision),
        )

        new_result = mp_draw.poll_latest()
        if new_result is not None:
            perf.record_event(
                "mp_result_received",
                frame_id=int(new_result.frame_id),
                revision=int(new_result.snapshot_revision),
            )
            if new_result.worker_lag_ms is not None:
                perf.record_worker_lag(new_result.worker_lag_ms)
            if new_result.error is not None:
                extend_operation_diagnostics(new_result.diagnostics)
                raise RuntimeError(f"mp-draw worker で例外が発生しました:\n{new_result.error}")

        latest_successful = mp_draw.latest_successful_result()
        if latest_successful is None:
            if self._retained_source_layers:
                return self._restyle_retained_output(
                    self._retained_source_layers,
                    defaults=defaults,
                    style_revision=style_revision,
                )
            return self._fresh_result_pending_output()

        latest_success_frame_id = int(latest_successful.frame_id)
        latest_success_epoch = int(latest_successful.epoch)
        if (
            new_result is None
            and self._last_merged_mp_success_frame_id == latest_success_frame_id
            and self._last_merged_mp_success_epoch == latest_success_epoch
        ):
            # 同じ worker result の再表示では geometry/cache key を保持し、
            # current global/layer style だけを main process で再適用する。
            # CPU realize/aggregate/cache-lock の固定費を避けつつ、style slider を
            # worker result 待ちにしない。
            return self._restyle_retained_output(
                latest_successful.layers,
                defaults=defaults,
                style_revision=style_revision,
            )

        # worker は ParamStore を触れないので、実際に preview 候補となる
        # 最新成功 frame の観測を main 側で 1 回だけ反映する。
        # success と後続 error が同時に drain されると poll_latest() は
        # error だけを返すため、new_result ではなく latest_successful を基準にする。
        should_merge_success = (
            self._last_merged_mp_success_frame_id != latest_success_frame_id
            or self._last_merged_mp_success_epoch != latest_success_epoch
        )
        if should_merge_success:
            frame_params = current_frame_params()
            if frame_params is not None:
                frame_params.records.extend(latest_successful.records)
                frame_params.labels.extend(latest_successful.labels)
                frame_params.effect_chains.extend(
                    latest_successful.effect_chains
                )
            extend_operation_diagnostics(latest_successful.diagnostics)

        # 2) realize（main 側）: 最新の layers を通常パイプラインへ流して表示/出力する。
        def draw_from_mp(_t_arg: float) -> SceneItem:
            return latest_successful.layers

        perf.record_event(
            "realize_started",
            frame_id=latest_success_frame_id,
            revision=int(latest_successful.snapshot_revision),
        )
        with perf.section("scene"):
            realized_layers = realize_scene(
                draw_from_mp,
                t,
                defaults,
                config=self._effective_config,
                session=self._realize_sessions[quality],
                presets=self._definitions.presets,
            )
        perf.record_event(
            "realize_finished",
            frame_id=latest_success_frame_id,
            revision=int(latest_successful.snapshot_revision),
        )
        # worker 成功だけではなく、main 側の realize が成功した後にだけ
        # output time を更新する。error result や realize 失敗の t を manifest へ
        # 誤って記録しないための順序。
        if should_merge_success:
            # parameter_context は例外 frame の records/labels を rollback する。
            # realize 成功前に consumed 扱いすると、次回 retry で観測を
            # 再マージできないため、成功後にだけ frame id を進める。
            self._last_merged_mp_success_frame_id = latest_success_frame_id
            self._last_merged_mp_success_epoch = latest_success_epoch
            frame_params = current_frame_params()
            if frame_params is not None:
                # chainが0件のworker resultも完全な成功観測であることを明示し、
                # result待ちの空bufferとは区別する。
                frame_params.complete_effect_chain_observation()
        self._last_realized_t = float(latest_successful.t)
        self._last_realized_snapshot_revision = int(
            latest_successful.snapshot_revision
        )
        self._last_realized_frame_id = latest_success_frame_id
        self._retain_output(
            realized_layers,
            t=float(latest_successful.t),
            snapshot_revision=int(latest_successful.snapshot_revision),
            frame_id=latest_success_frame_id,
            source_layers=list(latest_successful.layers),
            style_revision=style_revision,
        )
        self._last_output_updated = bool(should_merge_success)
        if new_result is not None:
            # ここに到達する new_result は error=None。新しい成功結果が
            # 終端結果である場合のみ、既存 error 表示を解除する。
            self._last_evaluation_succeeded = True
            self._last_evaluation_t = float(latest_successful.t)
        return realized_layers

    def close(self) -> None:
        """worker、generation resources、共有 cache の順に終了する。"""

        mp_draw = self._mp_draw
        self._mp_draw = None
        sessions = self._realize_sessions
        resources = self._evaluation_resources
        errors = CleanupErrors()
        if mp_draw is not None:
            errors.attempt(mp_draw.close, "close draw worker")
        _attempt_evaluation_generation_cleanup(errors, sessions, resources)
        errors.attempt(self._cache_store.close, "close scene realize cache store")
        errors.raise_if_any()
