# ruff: noqa: E402 -- pyglet option must be set before importing runner.

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pyglet
import pytest

pyglet.options["shadow_window"] = False

import grafix.api._runner_application as runner_module
import grafix.interactive.parameter_gui.catalog as gui_catalog_module
import grafix.interactive.runtime.parameter_gui_system as gui_system_module
import grafix.interactive.runtime.variation_thumbnail_capture as thumbnail_capture_module
from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.core.operation_catalog import OperationCatalog
from grafix.core.parameters import (
    KnownOperationSchemaSnapshot,
    ParameterCaptureState,
    ParameterLoadState,
    ParamStore,
)
from grafix.core.preset_catalog import PresetCatalog
from grafix.core.runtime_config import RuntimeConfig
from grafix.runtime_config_loader import runtime_config
from grafix.core.scene import SceneItem
from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog


def _definitions() -> AuthoringDefinitionsSnapshot:
    return AuthoringDefinitionsSnapshot(
        operations=OperationCatalog({}),
        presets=PresetCatalog({}),
    )


class _Workspace:
    diagnostic = None
    restored = False
    ui_scale = 1.0

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def attach_preview(self, _window: object) -> None:
        self._calls.append("attach preview")

    def attach_inspector(self, _window: object) -> None:
        self._calls.append("attach inspector")

    def apply_layout(self) -> None:
        self._calls.append("apply layout")

    def install_visibility_shortcut(self) -> None:
        self._calls.append("install shortcut")

    def hide_inspector(self) -> None:
        pass

    def activate(self) -> None:
        pass

    def persist(self) -> None:
        self._calls.append("persist workspace")


class _RunnerCompositionHarness:
    """`run()` の外部 resource 境界だけを fake にした contract harness。"""

    def __init__(
        self,
        *,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        definitions: AuthoringDefinitionsSnapshot,
        gui_enabled: bool,
        loop_body: Callable[[_RunnerCompositionHarness], None] | None = None,
        draw_window_error: BaseException | None = None,
        gui_error: BaseException | None = None,
        persist_error: BaseException | None = None,
    ) -> None:
        self.config = runtime_config()
        self.definitions = definitions
        self.gui_enabled = gui_enabled
        self.loop_body = loop_body
        self.draw_window_error = draw_window_error
        self.gui_error = gui_error
        self.persist_error = persist_error
        self.calls: list[str] = []
        self.loader_calls: list[tuple[Callable[..., object], RuntimeConfig]] = []
        self.schema_projections: list[
            tuple[OperationCatalog, PresetCatalog, KnownOperationSchemaSnapshot]
        ] = []
        self.gui_projections: list[tuple[OperationCatalog, PresetCatalog, ParameterGuiCatalog]] = []
        self.draw_window_kwargs: dict[str, object] | None = None
        self.gui_kwargs: dict[str, object] | None = None
        self.draw_window: Any | None = None
        self.parameter_session: Any | None = None
        self.gui: Any | None = None
        self.provider_results: list[ParameterGuiCatalog] = []
        self.midi_close_count = 0
        self.midi_session_kwargs: list[dict[str, object]] = []
        self.output_path_calls: list[dict[str, object]] = []
        self.thumbnail_export_wiring: list[object] = []
        self.workspace_load_kwargs: dict[str, object] | None = None

        harness = self
        real_schema_projection = runner_module.known_operation_schema_snapshot
        real_gui_projection = ParameterGuiCatalog.capture

        def load_definitions(
            draw: Callable[..., object],
            *,
            config: RuntimeConfig,
        ) -> AuthoringDefinitionsSnapshot:
            harness.loader_calls.append((draw, config))
            return definitions

        def project_schema(
            operations: OperationCatalog,
            presets: PresetCatalog,
        ) -> KnownOperationSchemaSnapshot:
            projected = real_schema_projection(operations, presets)
            harness.schema_projections.append((operations, presets, projected))
            return projected

        def project_gui(
            operations: OperationCatalog,
            presets: PresetCatalog,
        ) -> ParameterGuiCatalog:
            projected = real_gui_projection(operations, presets)
            harness.gui_projections.append((operations, presets, projected))
            return projected

        class ParameterSession:
            def __init__(
                self,
                *,
                primary_path: Path | None,
                gui_enabled: bool,
                known_operations: KnownOperationSchemaSnapshot,
            ) -> None:
                self.primary_path = primary_path
                self.gui_enabled = gui_enabled
                self.store = ParamStore()
                self.load_state = ParameterLoadState()
                self.history = None
                self.snapshot_slots = None
                self.autosave = None
                self.initial_known_operations = known_operations
                self.known_operations = known_operations
                self.replacements: list[KnownOperationSchemaSnapshot] = []
                self.persisted: list[tuple[KnownOperationSchemaSnapshot, bool, object | None]] = []
                harness.parameter_session = self

            def capture_state(self) -> ParameterCaptureState:
                source = (
                    "recovery"
                    if (
                        self.primary_path is not None
                        and self.load_state.provenance == "session_recovery"
                    )
                    else ("saved" if self.primary_path is not None else "code")
                )
                return ParameterCaptureState(
                    source,
                    self.load_state.provenance,
                )

            def replace_known_operations(
                self,
                known_operations: KnownOperationSchemaSnapshot,
            ) -> None:
                self.known_operations = known_operations
                self.replacements.append(known_operations)

            def install_diagnostic_actions(self, _monitor: object) -> None:
                pass

            def persist(
                self,
                *,
                session_completed_cleanly: bool,
                monitor: object | None,
            ) -> None:
                self.persisted.append(
                    (
                        self.known_operations,
                        session_completed_cleanly,
                        monitor,
                    )
                )
                harness.calls.append("persist parameters")
                if harness.persist_error is not None:
                    raise harness.persist_error

        class MidiSession:
            def close(self) -> None:
                harness.midi_close_count += 1
                harness.calls.append("close midi")

        class CaptureService:
            def _export_owned(self, *_args: object, **_kwargs: object) -> None:
                pass

        class DrawWindowSystem:
            def __init__(self, _draw: Callable[..., object], **kwargs: object) -> None:
                harness.draw_window_kwargs = dict(kwargs)
                harness.calls.append("create draw")
                if harness.draw_window_error is not None:
                    raise harness.draw_window_error
                self.authoring_definitions = kwargs["definitions"]
                self.window = object()
                self.transport = object()
                self.is_recording = False
                self.capture_service = CaptureService()
                self._midi_session = cast(Any, kwargs["midi_session"])
                harness.draw_window = self

            def draw_frame(self) -> None:
                pass

            def final_capture_frame(self) -> None:
                return None

            def record_parameter_revision_created(
                self,
                _revision: int,
                _input_started_ns: int,
                _domain: str,
            ) -> None:
                pass

            def record_window_present(self, _name: str, _elapsed_ns: int) -> None:
                pass

            def record_full_loop(self, _elapsed_ns: int) -> None:
                pass

            def record_scheduler_jitter(self, _elapsed_ns: int) -> None:
                pass

            def close(self) -> None:
                harness.calls.append("close draw")
                self._midi_session.close()

        class ParameterGUIWindowSystem:
            def __init__(self, **kwargs: object) -> None:
                harness.gui_kwargs = dict(kwargs)
                harness.calls.append("create gui")
                if harness.gui_error is not None:
                    raise harness.gui_error
                self.window = object()
                self.catalog = cast(ParameterGuiCatalog, kwargs["catalog"])
                self.catalog_provider = cast(
                    Callable[[], ParameterGuiCatalog],
                    kwargs["catalog_provider"],
                )
                harness.gui = self

            def draw_frame(self) -> None:
                provider = self.catalog_provider
                assert callable(provider)
                self.catalog = provider()
                harness.provider_results.append(self.catalog)

            def close(self) -> None:
                harness.calls.append("close gui")

        workspace = _Workspace(self.calls)

        class WorkspaceWindowController:
            @staticmethod
            def load(**kwargs: object) -> _Workspace:
                harness.workspace_load_kwargs = dict(kwargs)
                return workspace

        class MultiWindowLoop:
            def __init__(self, _tasks: object, **_kwargs: object) -> None:
                pass

            def run(self) -> None:
                if harness.loop_body is not None:
                    harness.loop_body(harness)

        monkeypatch.setattr(
            runner_module,
            "authoring_definitions_for_draw",
            load_definitions,
        )
        monkeypatch.setattr(
            runner_module,
            "known_operation_schema_snapshot",
            project_schema,
        )
        monkeypatch.setattr(
            gui_catalog_module.ParameterGuiCatalog,
            "capture",
            staticmethod(project_gui),
        )
        monkeypatch.setattr(runner_module, "ParameterSession", ParameterSession)
        monkeypatch.setattr(runner_module, "DrawWindowSystem", DrawWindowSystem)
        monkeypatch.setattr(runner_module, "MultiWindowLoop", MultiWindowLoop)
        monkeypatch.setattr(
            runner_module,
            "WorkspaceWindowController",
            WorkspaceWindowController,
        )

        def create_midi_session(**kwargs: object) -> MidiSession:
            harness.midi_session_kwargs.append(dict(kwargs))
            return MidiSession()

        monkeypatch.setattr(
            runner_module,
            "create_midi_session",
            create_midi_session,
        )
        monkeypatch.setattr(
            runner_module,
            "default_param_store_path",
            lambda *_args, **_kwargs: tmp_path / "parameters.json",
        )

        def output_path_for_draw(
            *_args: object,
            **kwargs: object,
        ) -> Path:
            harness.output_path_calls.append(dict(kwargs))
            return tmp_path / f"{kwargs.get('kind', 'output')}.{kwargs.get('ext', 'dat')}"

        monkeypatch.setattr(
            runner_module,
            "output_path_for_draw",
            output_path_for_draw,
        )
        monkeypatch.setattr(
            gui_system_module,
            "ParameterGUIWindowSystem",
            ParameterGUIWindowSystem,
        )

        def make_variation_thumbnail_capture(
            export_owned: object,
            **_kwargs: object,
        ) -> Callable[..., None]:
            harness.thumbnail_export_wiring.append(export_owned)
            return lambda *_args, **_kwargs: None

        monkeypatch.setattr(
            thumbnail_capture_module,
            "make_variation_thumbnail_capture",
            make_variation_thumbnail_capture,
        )
        monkeypatch.setattr(runner_module.pyglet.clock, "schedule_once", lambda *_args: None)
        monkeypatch.setattr(runner_module.pyglet.clock, "unschedule", lambda *_args: None)

    def run(
        self,
        draw: Callable[[float], object],
        *,
        render_scale: float | None = None,
        canvas_size: tuple[int, int] = (800, 800),
    ) -> None:
        runner_module._run_interactive_application(
            cast(Callable[[float], SceneItem], draw),
            config=self.config,
            render_scale=render_scale,
            canvas_size=canvas_size,
            parameter_gui=self.gui_enabled,
            parameter_persistence=False,
            midi_port_name=None,
            n_worker=0,
        )


def _assert_projection_source(
    projection: tuple[OperationCatalog, PresetCatalog, object],
    definitions: AuthoringDefinitionsSnapshot,
) -> None:
    operations, presets, _projected = projection
    assert operations is definitions.operations
    assert presets is definitions.presets


def test_runner_normal_lifetime_persists_then_closes_owned_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公開 run の正常経路が acquisition 済み resource を最後まで解放する。"""

    harness = _RunnerCompositionHarness(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        definitions=_definitions(),
        gui_enabled=True,
    )

    harness.run(lambda _t: None)

    session = harness.parameter_session
    assert session is not None
    assert len(session.persisted) == 1
    assert session.persisted[0][1] is True
    assert harness.calls[-5:] == [
        "persist parameters",
        "persist workspace",
        "close gui",
        "close draw",
        "close midi",
    ]
    assert harness.midi_close_count == 1
    midi_path_calls = [call for call in harness.output_path_calls if call.get("kind") == "midi"]
    assert len(midi_path_calls) == 1
    assert len(harness.midi_session_kwargs) == 1
    assert harness.midi_session_kwargs[0]["snapshot_path"] == tmp_path / "midi.json"
    assert "profile_name" not in harness.midi_session_kwargs[0]
    assert "save_dir" not in harness.midi_session_kwargs[0]
    assert harness.draw_window is not None
    assert harness.thumbnail_export_wiring == [harness.draw_window.capture_service._export_owned]


def test_runner_projects_one_config_snapshot_and_switches_gui_by_generation_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _definitions()
    reloaded = _definitions()
    assert reloaded == initial
    assert reloaded is not initial

    def drive_generations(harness: _RunnerCompositionHarness) -> None:
        assert harness.gui is not None
        assert harness.draw_window is not None
        harness.gui.draw_frame()
        harness.gui.draw_frame()
        harness.draw_window.authoring_definitions = reloaded
        harness.gui.draw_frame()
        harness.gui.draw_frame()

    harness = _RunnerCompositionHarness(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        definitions=initial,
        gui_enabled=True,
        loop_body=drive_generations,
    )

    def draw(_t: float) -> None:
        return None

    harness.run(draw)

    assert harness.loader_calls == [(draw, harness.config)]
    assert harness.draw_window_kwargs is not None
    assert harness.draw_window_kwargs["definitions"] is initial
    assert harness.draw_window_kwargs["effective_config"] is harness.config
    provenance_provider = cast(
        Callable[[], ParameterCaptureState],
        harness.draw_window_kwargs["parameter_capture_state"],
    )
    assert provenance_provider() == ParameterCaptureState("code", "primary")
    assert len(harness.schema_projections) == 2
    assert len(harness.gui_projections) == 2
    _assert_projection_source(harness.schema_projections[0], initial)
    _assert_projection_source(harness.schema_projections[1], reloaded)
    _assert_projection_source(harness.gui_projections[0], initial)
    _assert_projection_source(harness.gui_projections[1], reloaded)

    session = harness.parameter_session
    assert session is not None
    session.load_state = ParameterLoadState(provenance="session_recovery")
    assert provenance_provider() == ParameterCaptureState(
        "code",
        "session_recovery",
    )
    initial_schema = harness.schema_projections[0][2]
    reloaded_schema = harness.schema_projections[1][2]
    assert session.initial_known_operations is initial_schema
    assert session.replacements == [reloaded_schema]
    assert session.persisted[0][0] is reloaded_schema
    assert session.persisted[0][1] is True

    assert harness.gui_kwargs is not None
    initial_gui_catalog = harness.gui_projections[0][2]
    reloaded_gui_catalog = harness.gui_projections[1][2]
    assert harness.gui_kwargs["catalog"] is initial_gui_catalog
    assert harness.provider_results == [
        initial_gui_catalog,
        initial_gui_catalog,
        reloaded_gui_catalog,
        reloaded_gui_catalog,
    ]


def test_runner_without_gui_adopts_last_draw_generation_before_persist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _definitions()
    reloaded = _definitions()

    def replace_generation(harness: _RunnerCompositionHarness) -> None:
        assert harness.draw_window is not None
        harness.draw_window.authoring_definitions = reloaded

    harness = _RunnerCompositionHarness(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        definitions=initial,
        gui_enabled=False,
        loop_body=replace_generation,
    )

    harness.run(lambda _t: None)

    assert len(harness.schema_projections) == 2
    _assert_projection_source(harness.schema_projections[0], initial)
    _assert_projection_source(harness.schema_projections[1], reloaded)
    assert harness.gui_projections == []
    session = harness.parameter_session
    assert session is not None
    reloaded_schema = harness.schema_projections[1][2]
    assert session.replacements == [reloaded_schema]
    assert session.persisted[0][0] is reloaded_schema
    assert session.persisted[0][1] is True


@pytest.mark.parametrize(
    ("render_scale", "preview_size", "restore_preview_size"),
    [
        (8.0, (1184, 1680), False),
        (1.0, (148, 210), False),
        (None, (148, 210), True),
    ],
)
def test_runner_composes_preview_size_ownership_policy(
    render_scale: float | None,
    preview_size: tuple[int, int],
    restore_preview_size: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _RunnerCompositionHarness(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        definitions=_definitions(),
        gui_enabled=False,
    )

    harness.run(
        lambda _t: None,
        render_scale=render_scale,
        canvas_size=(148, 210),
    )

    assert harness.workspace_load_kwargs is not None
    assert harness.workspace_load_kwargs["preview_size"] == preview_size
    assert harness.workspace_load_kwargs["restore_preview_size"] is restore_preview_size
    assert "apply layout" in harness.calls


@pytest.mark.parametrize("failure_stage", ["draw", "gui"])
def test_runner_construction_failure_persists_initial_schema_without_masking_error(
    failure_stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _definitions()
    construction_error = RuntimeError(f"{failure_stage} construction failed")
    cleanup_error = OSError("parameter cleanup failed")
    harness = _RunnerCompositionHarness(
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        definitions=initial,
        gui_enabled=failure_stage == "gui",
        draw_window_error=construction_error if failure_stage == "draw" else None,
        gui_error=construction_error if failure_stage == "gui" else None,
        persist_error=cleanup_error,
    )

    with pytest.raises(RuntimeError, match=f"{failure_stage} construction failed") as exc_info:
        harness.run(lambda _t: None)

    assert exc_info.value is construction_error
    assert len(harness.schema_projections) == 1
    _assert_projection_source(harness.schema_projections[0], initial)
    session = harness.parameter_session
    assert session is not None
    initial_schema = harness.schema_projections[0][2]
    assert session.initial_known_operations is initial_schema
    assert session.replacements == []
    assert len(session.persisted) == 1
    persisted_schema, completed_cleanly, _monitor = session.persisted[0]
    assert persisted_schema is initial_schema
    assert completed_cleanly is False
    assert harness.draw_window_kwargs is not None
    assert harness.draw_window_kwargs["definitions"] is initial
    if failure_stage == "draw":
        assert harness.gui_projections == []
        assert harness.midi_close_count == 1
        assert "close draw" not in harness.calls
    else:
        assert len(harness.gui_projections) == 1
        _assert_projection_source(harness.gui_projections[0], initial)
        assert harness.gui_kwargs is not None
        assert harness.gui_kwargs["catalog"] is harness.gui_projections[0][2]
        assert harness.calls[-2:] == ["close draw", "close midi"]
