"""interactive runner の private application composition。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import pyglet

from grafix.api.render import RenderOptions
from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.authoring_loader import authoring_definitions_for_draw
from grafix.core.lifecycle import CleanupErrors
from grafix.core.runtime_limits import (
    DEFAULT_RUNTIME_LIMIT_PROFILES,
    RuntimeLimitProfiles,
)
from grafix.core.runtime_config import RuntimeConfig, RuntimeConfigFallback
from grafix.core.scene import SceneItem
from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string_choice,
    finite_real,
)
from grafix.export.output_paths import default_param_store_path, output_path_for_draw
from grafix.interactive.diagnostics import DiagnosticAction, DiagnosticEvent
from grafix.interactive.midi import MidiSession
from grafix.interactive.midi.factory import create_midi_session
from grafix.interactive.runtime.draw_window_system import DrawWindowSystem
from grafix.interactive.runtime.parameter_session import (
    ParameterSession,
    known_operation_schema_snapshot,
)
from grafix.interactive.runtime.window_loop import MultiWindowLoop, WindowTask
from grafix.interactive.runtime.workspace_window_controller import (
    WorkspaceWindowController,
)
from grafix.runtime_config_loader import runtime_config_with_fallback

if TYPE_CHECKING:
    from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog
    from grafix.interactive.runtime.monitor import RuntimeMonitor
    from grafix.interactive.runtime.parameter_gui_system import (
        ParameterGUIWindowSystem,
    )

_logger = logging.getLogger(__name__)


def _run_cleanup_steps(
    steps: list[tuple[str, Callable[[], None]]],
    *,
    initial_error: BaseException | None = None,
) -> None:
    """shutdown step を全て試し、最初の例外を最後に再送出する。"""

    def report_secondary(label: str) -> None:
        _logger.exception(
            "Shutdown step failed after an earlier error: %s",
            label,
        )

    errors = CleanupErrors(
        initial_error=initial_error,
        report_secondary=report_secondary,
    )
    for label, step in steps:
        errors.attempt(step, label)
    errors.raise_if_any()


def _publish_runtime_config_fallback(
    monitor: RuntimeMonitor,
    fallback: RuntimeConfigFallback,
) -> DiagnosticEvent:
    """interactive config fallbackを共通DiagnosticCenterへ常設する。"""

    actions = [DiagnosticAction("copy", "Copy details")]
    if fallback.source is not None:
        actions.append(DiagnosticAction("open", "Open config"))
    return monitor.publish_diagnostic(
        DiagnosticEvent(
            category="config",
            severity="error",
            summary="Runtime config is invalid; using packaged defaults",
            details=fallback.details,
            source=None if fallback.source is None else str(fallback.source),
            actions=tuple(actions),
            dedupe_key=f"config-fallback:{fallback.summary}",
        )
    )


class _InteractiveApplication:
    """1 interactive run の application owner と逆順 teardown を束ねる。"""

    def __init__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        config: RuntimeConfig,
        config_fallback: RuntimeConfigFallback | None,
        options: RenderOptions,
        preview_scale: float,
        gui_enabled: bool,
        persistence_enabled: bool,
        midi_port_name: str | None,
        midi_mode: str,
        worker_count: int,
        evaluation_timeout: float | None,
        frame_rate: float,
        capture_seed: int | None,
        run_id: str | None,
        runtime_limit_profiles: RuntimeLimitProfiles,
    ) -> None:
        self._draw = draw
        self._config = config
        self._config_fallback = config_fallback
        self._options = options
        self._preview_scale = preview_scale
        self._gui_enabled = gui_enabled
        self._persistence_enabled = persistence_enabled
        self._midi_port_name = midi_port_name
        self._midi_mode = midi_mode
        self._worker_count = worker_count
        self._evaluation_timeout = evaluation_timeout
        self._frame_rate = frame_rate
        self._capture_seed = capture_seed
        self._run_id = run_id
        self._runtime_limit_profiles = runtime_limit_profiles

        self._definitions: AuthoringDefinitionsSnapshot | None = None
        self._schema_definitions: AuthoringDefinitionsSnapshot | None = None
        self._gui_catalog_definitions: AuthoringDefinitionsSnapshot | None = None
        self._gui_catalog: ParameterGuiCatalog | None = None
        self._workspace: WorkspaceWindowController | None = None
        self._parameter_session: ParameterSession | None = None
        self._midi_session: MidiSession | None = None
        self._unowned_midi: MidiSession | None = None
        self._draw_window: DrawWindowSystem | None = None
        self._gui: ParameterGUIWindowSystem | None = None
        self._monitor: RuntimeMonitor | None = None
        self._loop: MultiWindowLoop | None = None
        self._session_completed_cleanly = False
        self._activation_callback = self._activate_windows
        self._activation_scheduled = False

    def run(self) -> None:
        """resource を構成して loop を実行し、成功・失敗とも全 cleanup を試す。"""

        session_error: BaseException | None = None
        try:
            self._compose()
            assert self._loop is not None
            self._loop.run()
            self._session_completed_cleanly = True
        except BaseException as error:  # root error を cleanup 後まで保持する
            session_error = error
        self._cleanup(initial_error=session_error)

    def _compose(self) -> None:
        """application owner を acquisition 順に一度だけ構成する。"""

        from grafix.interactive.runtime.source_reload import current_source_reload

        definitions = authoring_definitions_for_draw(
            self._draw,
            config=self._config,
        )
        self._definitions = definitions
        self._schema_definitions = definitions

        fallback = self._config_fallback
        if fallback is not None and not self._gui_enabled:
            _logger.error(
                "Runtime config invalid; using packaged defaults: %s\n%s",
                fallback.summary,
                fallback.details,
            )

        # Window 作成前に固定する必要がある process option。
        pyglet.options["vsync"] = False

        default_store_path = default_param_store_path(
            self._draw,
            run_id=self._run_id,
            config=self._config,
        )
        workspace_path = output_path_for_draw(
            kind="workspace",
            ext="json",
            draw=self._draw,
            run_id=self._run_id,
            config=self._config,
        )
        preview_width = max(
            1,
            int(round(self._options.canvas_size[0] * self._preview_scale)),
        )
        preview_height = max(
            1,
            int(round(self._options.canvas_size[1] * self._preview_scale)),
        )
        workspace = WorkspaceWindowController.load(
            path=workspace_path,
            preview_size=(preview_width, preview_height),
            inspector_size=self._config.parameter_gui_window_size,
            preferred_preview_position=self._config.window_pos_draw,
            preferred_inspector_position=self._config.window_pos_parameter_gui,
        )
        self._workspace = workspace

        param_store_path = (
            default_store_path if self._persistence_enabled else None
        )
        parameter_session = ParameterSession(
            primary_path=param_store_path,
            gui_enabled=self._gui_enabled,
            known_operations=known_operation_schema_snapshot(
                definitions.operations,
                definitions.presets,
            ),
        )
        self._parameter_session = parameter_session

        if self._gui_enabled:
            from grafix.interactive.runtime.monitor import RuntimeMonitor

            self._monitor = RuntimeMonitor()

        midi_path = output_path_for_draw(
            kind="midi",
            ext="json",
            draw=self._draw,
            run_id=self._run_id,
            config=self._config,
        )
        midi_session = create_midi_session(
            port_name=self._midi_port_name,
            mode=self._midi_mode,
            snapshot_path=midi_path,
            priority_inputs=self._config.midi_inputs,
            diagnostics=(
                None
                if self._monitor is None
                else self._monitor.diagnostic_center
            ),
        )
        self._midi_session = midi_session
        # DrawWindowSystem constructor が成功するまでは application が所有する。
        self._unowned_midi = midi_session

        self._publish_startup_diagnostics()

        draw_window = DrawWindowSystem(
            self._draw,
            options=self._options,
            render_scale=self._preview_scale,
            store=parameter_session.store,
            midi_session=midi_session,
            monitor=self._monitor,
            fps=self._frame_rate,
            n_worker=self._worker_count,
            evaluation_timeout=self._evaluation_timeout,
            run_id=self._run_id,
            runtime_limit_profiles=self._runtime_limit_profiles,
            source_reload=current_source_reload(),
            definitions=definitions,
            effective_config=self._config,
            parameter_capture_state=parameter_session.capture_state,
            parameter_store_path=param_store_path,
            seed=self._capture_seed,
        )
        self._draw_window = draw_window
        # 以後は DrawWindowSystem が MIDI の save/close を所有する。
        self._unowned_midi = None
        workspace.attach_preview(draw_window.window)

        tasks = [
            WindowTask(
                window=draw_window.window,
                draw_frame=draw_window.draw_frame,
                on_close=pyglet.app.exit,
                on_presented=self._record_preview_presented,
            )
        ]
        if self._gui_enabled:
            self._compose_gui(tasks)
        elif workspace.restored:
            workspace.apply_layout()

        pyglet.clock.schedule_once(self._activation_callback, 0.0)
        self._activation_scheduled = True
        self._loop = MultiWindowLoop(
            tuple(tasks),
            fps=self._frame_rate,
            on_frame_start=(
                None if self._monitor is None else self._monitor.tick_frame
            ),
            on_frame_finished=draw_window.record_full_loop,
            on_scheduler_jitter=draw_window.record_scheduler_jitter,
        )

    def _publish_startup_diagnostics(self) -> None:
        workspace = self._workspace
        parameter_session = self._parameter_session
        assert workspace is not None
        assert parameter_session is not None

        diagnostic = workspace.diagnostic
        if diagnostic is not None:
            if self._monitor is None:
                _logger.warning(
                    "%s: %s",
                    diagnostic.summary,
                    diagnostic.details,
                )
            else:
                self._monitor.publish_diagnostic(diagnostic)

        if self._monitor is None:
            return
        parameter_session.install_diagnostic_actions(self._monitor)
        center = self._monitor.diagnostic_center
        if self._config_fallback is not None:
            _publish_runtime_config_fallback(
                self._monitor,
                self._config_fallback,
            )
        center.register_action(
            "retry",
            self._retry_midi,
            category="midi",
        )
        center.register_action(
            "discard",
            self._discard_midi,
            category="midi",
        )

    def _retry_midi(self, event: DiagnosticEvent) -> None:
        """登録時には MIDI 実装へ触れず、action 実行時だけ委譲する。"""

        midi_session = self._midi_session
        if midi_session is None:
            raise RuntimeError("MIDI session is not available")
        midi_session.retry_for_diagnostic(event)

    def _discard_midi(self, event: DiagnosticEvent) -> None:
        """登録時には MIDI 実装へ触れず、action 実行時だけ委譲する。"""

        midi_session = self._midi_session
        if midi_session is None:
            raise RuntimeError("MIDI session is not available")
        midi_session.discard_for_diagnostic(event)

    def _compose_gui(self, tasks: list[WindowTask]) -> None:
        """重い GUI dependency を遅延 import し、inspector task を追加する。"""

        from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog
        from grafix.interactive.parameter_gui.variation_thumbnail import (
            draw_variation_thumbnail_status,
        )
        from grafix.interactive.runtime.parameter_gui_system import (
            ParameterGUIWindowSystem,
        )
        from grafix.interactive.runtime.variation_thumbnail_capture import (
            make_variation_thumbnail_capture,
        )

        definitions = self._definitions
        parameter_session = self._parameter_session
        draw_window = self._draw_window
        workspace = self._workspace
        assert definitions is not None
        assert parameter_session is not None
        assert draw_window is not None
        assert workspace is not None
        assert self._midi_session is not None

        thumbnail_base = output_path_for_draw(
            kind="variation_thumbnail",
            ext="png",
            draw=self._draw,
            run_id=self._run_id,
            canvas_size=self._options.canvas_size,
            config=self._config,
        )
        thumbnail_capture = make_variation_thumbnail_capture(
            draw_window.capture_service._export_owned,
            frame_provider=draw_window.final_capture_frame,
            base_path=thumbnail_base,
            canvas_size=self._options.canvas_size,
        )
        gui_catalog = ParameterGuiCatalog.capture(
            definitions.operations,
            definitions.presets,
        )
        self._gui_catalog_definitions = definitions
        self._gui_catalog = gui_catalog

        gui = ParameterGUIWindowSystem(
            effective_config=self._config,
            store=parameter_session.store,
            midi_session=self._midi_session,
            monitor=self._monitor,
            transport=draw_window.transport,
            transport_fps=self._frame_rate,
            history=parameter_session.history,
            snapshot_slots=parameter_session.snapshot_slots,
            autosave=parameter_session.autosave,
            is_recording=self._is_recording,
            variation_thumbnail_capture=thumbnail_capture,
            variation_thumbnail_preview=draw_variation_thumbnail_status,
            ui_scale=workspace.ui_scale,
            catalog=gui_catalog,
            catalog_provider=self._current_parameter_gui_catalog,
            on_parameter_revision_created=(
                draw_window.record_parameter_revision_created
            ),
        )
        self._gui = gui
        workspace.attach_inspector(gui.window)
        workspace.apply_layout()
        tasks.insert(
            0,
            WindowTask(
                window=gui.window,
                draw_frame=gui.draw_frame,
                on_close=workspace.hide_inspector,
                on_presented=self._record_inspector_presented,
            ),
        )
        workspace.install_visibility_shortcut()

    def _current_parameter_gui_catalog(self) -> ParameterGuiCatalog:
        """最後に採用された authoring generation の GUI projection を返す。"""

        from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog

        draw_window = self._draw_window
        assert draw_window is not None
        active = draw_window.authoring_definitions
        if active is not self._gui_catalog_definitions:
            projected = ParameterGuiCatalog.capture(
                active.operations,
                active.presets,
            )
            self._adopt_parameter_schema(active)
            self._gui_catalog = projected
            self._gui_catalog_definitions = active
        assert self._gui_catalog is not None
        return self._gui_catalog

    def _adopt_parameter_schema(
        self,
        definitions: AuthoringDefinitionsSnapshot,
    ) -> None:
        """最後に採用された generation だけを終了時 schema に反映する。"""

        if definitions is self._schema_definitions:
            return
        parameter_session = self._parameter_session
        assert parameter_session is not None
        parameter_session.replace_known_operations(
            known_operation_schema_snapshot(
                definitions.operations,
                definitions.presets,
            )
        )
        self._schema_definitions = definitions

    def _is_recording(self) -> bool:
        draw_window = self._draw_window
        return False if draw_window is None else bool(draw_window.is_recording)

    def _record_preview_presented(self, elapsed_ns: int) -> None:
        draw_window = self._draw_window
        if draw_window is not None:
            draw_window.record_window_present("preview_draw_flip", elapsed_ns)

    def _record_inspector_presented(self, elapsed_ns: int) -> None:
        draw_window = self._draw_window
        if draw_window is not None:
            draw_window.record_window_present(
                "parameter_gui_draw_flip",
                elapsed_ns,
            )

    def _activate_windows(self, _dt: float) -> None:
        workspace = self._workspace
        if workspace is not None:
            workspace.activate()

    def _persist_parameter_session(self) -> None:
        parameter_session = self._parameter_session
        if parameter_session is None:
            return
        draw_window = self._draw_window
        if draw_window is not None:
            self._adopt_parameter_schema(draw_window.authoring_definitions)
        parameter_session.persist(
            session_completed_cleanly=self._session_completed_cleanly,
            monitor=self._monitor,
        )

    def _persist_workspace(self) -> None:
        workspace = self._workspace
        if workspace is not None:
            workspace.persist()

    def _unschedule_activation(self) -> None:
        if not self._activation_scheduled:
            return
        self._activation_scheduled = False
        pyglet.clock.unschedule(self._activation_callback)

    def _close_gui(self) -> None:
        gui = self._gui
        self._gui = None
        if gui is not None:
            gui.close()

    def _close_draw_window(self) -> None:
        draw_window = self._draw_window
        self._draw_window = None
        if draw_window is not None:
            draw_window.close()

    def _close_unowned_midi(self) -> None:
        midi = self._unowned_midi
        self._unowned_midi = None
        if midi is not None:
            midi.close()

    def _cleanup(self, *, initial_error: BaseException | None) -> None:
        """persist 後、取得済み resource だけを acquisition の逆順に閉じる。"""

        steps: list[tuple[str, Callable[[], None]]] = []
        if self._parameter_session is not None:
            steps.append(("persist ParameterStore", self._persist_parameter_session))
        if self._workspace is not None:
            steps.append(("persist WorkspaceState", self._persist_workspace))
        if self._activation_scheduled:
            steps.append(("unschedule window activation", self._unschedule_activation))
        if self._gui is not None:
            steps.append(("close parameter GUI", self._close_gui))
        if self._draw_window is not None:
            steps.append(("close draw window", self._close_draw_window))
        if self._unowned_midi is not None:
            steps.append(("close unowned MIDI", self._close_unowned_midi))
        _run_cleanup_steps(steps, initial_error=initial_error)


def _run_interactive_application(
    draw: Callable[[float], SceneItem],
    *,
    config_path: str | Path | None = None,
    config: RuntimeConfig | None = None,
    config_fallback: RuntimeConfigFallback | None = None,
    run_id: str | None = None,
    background_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
    line_thickness: float = 0.001,
    line_color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    render_scale: float = 1.0,
    canvas_size: tuple[int, int] = (800, 800),
    parameter_gui: bool = True,
    parameter_persistence: bool = True,
    midi_port_name: str | None = "auto",
    midi_mode: str = "7bit",
    n_worker: int = 1,
    evaluation_timeout: float | None = 5.0,
    fps: float = 60.0,
    seed: int | None = None,
    runtime_limit_profiles: RuntimeLimitProfiles = DEFAULT_RUNTIME_LIMIT_PROFILES,
) -> None:
    """検証済み public 入力と内部 fallback 診断から application を実行する。

    Parameters
    ----------
    draw : Callable[[float], SceneItem]
        フレーム経過秒 t を受け取り Geometry / Layer / それらの列を返すコールバック。
    config_path : str | Path | None
        設定ファイル（config.yaml）のパス。指定した場合は探索より優先する。
    config : RuntimeConfig | None
        呼び出し元で確定済みの設定。``config_path`` との同時指定はできない。
    config_fallback : RuntimeConfigFallback | None
        private config composition が得た fallback 診断。
    run_id : str | None
        作品スクリプトの同一性を表す識別子。出力ファイル名の接尾辞として使う。
        interactive capture は同名の完成品を上書きせず、必要に応じて連番を付ける。
    background_color : tuple[float, float, float]
        背景色 RGB。alpha は 1.0 固定。既定は白。
    line_thickness : float
        Layer.thickness 未指定時の線幅。キャンバス短辺に対する比率で、既定
        ``0.001`` は短辺の 0.1% に相当する。
    line_color : tuple[float, float, float]
        線色 RGB。既定は黒。
    render_scale : float
        キャンバス寸法に掛けるピクセル倍率。高精細プレビュー用。
    canvas_size : tuple[int, int]
        キャンバス寸法（任意単位）。投影行列生成とウィンドウサイズ決定に使用。
    parameter_gui : bool
        True の場合、別ウィンドウで Parameter GUI を起動し、ParamStore を編集できるようにする。
    parameter_persistence : bool
        True の場合、ParamStore を JSON 保存し、次回起動時に復元する。
        保存先は `output/param_store/` 配下で sketch_dir の構造をミラーし、run_id があればファイル名に付く。
        GUI 変更は短い debounce 後に atomic autosave し、終了時にも未保存分を確定する。
    midi_port_name : str | None
        MIDI 入力ポート名。
        - `"auto"`: 利用可能な入力ポートがあれば 1 つ目へ自動接続する（既定）。
          config.yaml に `midi.inputs`（接続優先リスト）があれば、その順に接続を試す。
          優先リストがある場合、どの候補も見つからなければ接続しない。
          接続できない場合でも、前回保存した CC スナップショットを凍結して使う（描画が変わらない）。
        - `"TX-6 Bluetooth"` のような文字列: 指定ポートへ接続する。
        - None: MIDI を無効化する。
    midi_mode : str
        MIDI CC の解釈モード。`"7bit"` または `"14bit"`。
    n_worker : int
        `draw(t)` を multiprocessing で実行する background worker 数。
        既定の 1 は UI event loop を塞がない 1 worker 非同期評価。`>=1` は
        spawn + Queue（pickle）で非同期化し、`0` の場合だけ main process で同期実行する。
        非同期評価では `draw` をモジュールトップレベルに定義し、起動側に
        `if __name__ == "__main__":` guard を置く必要がある。
    evaluation_timeout : float | None
        background worker の 1 回の `draw(t)` を待つ秒数。超過時は直近の成功表示を
        保ったまま worker を再起動する。`None` の場合は timeout を無効にする。
    fps : float
        目標フレームレート。`<=0` の場合はフレーム末尾で sleep せず、可能な限り速く回す。
        録画機能（V キー）は fps > 0 が必要。
    seed : int or None
        capture manifest に記録する作品 seed。乱数 global state は変更しない。
    runtime_limit_profiles : RuntimeLimitProfiles
        preview/final ごとの per-operation、scene aggregate、CPU/GPU cache、
        capture queue 上限。

    Returns
    -------
    None
        preview ウィンドウを閉じると制御を返す。
        Inspector の close はウィンドウを hide し、Cmd/Ctrl+I で再表示できる。
    """

    gui_enabled = exact_bool(parameter_gui, name="parameter_gui")
    persistence_enabled = exact_bool(
        parameter_persistence,
        name="parameter_persistence",
    )
    midi_mode_value = exact_string_choice(
        midi_mode,
        name="midi_mode",
        choices=("7bit", "14bit"),
    )
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
    frame_rate = finite_real(fps, name="fps")
    preview_scale = finite_real(
        render_scale,
        name="render_scale",
        minimum=0.0,
        minimum_inclusive=False,
    )
    capture_seed = None if seed is None else exact_integer(seed, name="seed")
    if type(runtime_limit_profiles) is not RuntimeLimitProfiles:
        raise TypeError(
            "runtime_limit_profiles は RuntimeLimitProfiles である必要があります"
        )

    if config is not None and not isinstance(config, RuntimeConfig):
        raise TypeError("config は RuntimeConfig または None である必要があります")
    if config_fallback is not None and not isinstance(
        config_fallback,
        RuntimeConfigFallback,
    ):
        raise TypeError(
            "config_fallback は RuntimeConfigFallback または None である必要があります"
        )
    if config is not None and config_path is not None:
        raise ValueError("config と config_path は同時に指定できません")
    if config is None and config_fallback is not None:
        raise ValueError("config_fallback は config と同時に指定する必要があります")
    if config is None:
        effective_config, effective_fallback = runtime_config_with_fallback(config_path)
    else:
        effective_config = config
        effective_fallback = config_fallback

    options = RenderOptions(
        background_color=background_color,
        line_thickness=line_thickness,
        line_color=line_color,
        canvas_size=canvas_size,
    )
    _InteractiveApplication(
        draw,
        config=effective_config,
        config_fallback=effective_fallback,
        options=options,
        preview_scale=preview_scale,
        gui_enabled=gui_enabled,
        persistence_enabled=persistence_enabled,
        midi_port_name=midi_port_name,
        midi_mode=midi_mode_value,
        worker_count=worker_count,
        evaluation_timeout=timeout,
        frame_rate=frame_rate,
        capture_seed=capture_seed,
        run_id=run_id,
        runtime_limit_profiles=runtime_limit_profiles,
    ).run()


__all__: list[str] = []
