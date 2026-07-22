from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Literal

import pytest

from grafix import G, P, Frame, RenderOptions, RenderSession, RuntimeLimits, render
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.font_resources import FontResources
from grafix.core.parameters import ParamStore
from grafix.core.resource_budget import ResourceBudget, ResourceLimitError
from grafix.core.parameters.style import style_key
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.core.runtime_config import current_runtime_config
from grafix.runtime_config_loader import load_runtime_config, runtime_config
from grafix.core.preview_quality import current_preview_quality, preview_quality_context
from grafix.parameter_storage import ParamStoreReadResult


def _constant_draw():
    geometry = G.line(
        center=(0.0, 0.0, 0.0),
        anchor="left",
        length=10.0,
        angle=0.0,
    )

    def draw(_t: float):
        return geometry

    return draw


def _install_render_dependency_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
    close_errors: dict[str, BaseException] | None = None,
    construction_error: tuple[str, BaseException] | None = None,
) -> None:
    """RenderSession の owned dependency の構築・close 順を観測する。"""

    render_module = importlib.import_module("grafix.api.render")
    selected_close_errors = {} if close_errors is None else close_errors
    fail_stage = None if construction_error is None else construction_error[0]
    root_error = None if construction_error is None else construction_error[1]

    def fail_construction(stage: str) -> None:
        if fail_stage == stage:
            assert root_error is not None
            raise root_error

    def close(label: str) -> None:
        events.append(f"close {label}")
        error = selected_close_errors.get(label)
        if error is not None:
            raise error

    class FakeEvaluationResources:
        def __init__(self) -> None:
            events.append("create resources")

        def close(self) -> None:
            close("resources")

    class FakeCacheStore:
        @classmethod
        def from_runtime_limits(cls, _runtime_limits: RuntimeLimits) -> FakeCacheStore:
            events.append("create cache")
            fail_construction("cache")
            return cls()

        def close(self) -> None:
            close("cache")

    class FakeRealizeSession:
        def __init__(self, **_kwargs: object) -> None:
            events.append("create realize")
            fail_construction("realize")

        def close(self) -> None:
            close("realize")

    class FakeDefinitions:
        operations = object()
        presets = object()

    original_metadata = render_module.RenderSessionMetadata

    def build_metadata(**kwargs: object):
        events.append("create metadata")
        fail_construction("metadata")
        return original_metadata(**kwargs)

    monkeypatch.setattr(render_module, "EvaluationResources", FakeEvaluationResources)
    monkeypatch.setattr(render_module, "EvaluationContext", lambda **_kwargs: object())
    monkeypatch.setattr(render_module, "RealizeCacheStore", FakeCacheStore)
    monkeypatch.setattr(render_module, "RealizeSession", FakeRealizeSession)
    monkeypatch.setattr(render_module, "RenderSessionMetadata", build_metadata)
    monkeypatch.setattr(
        render_module,
        "authoring_definitions_for_draw",
        lambda *_args, **_kwargs: FakeDefinitions(),
    )


def test_render_session_reuses_store_config_style_and_internal_realize_cache() -> None:
    session = RenderSession(
        _constant_draw(),
        options=RenderOptions(background_color="white"),
    )
    store = session.param_store
    config = session.config

    first = session.render(0.0)

    background_key = style_key("background_color")
    background_meta = store.get_meta(background_key)
    assert background_meta is not None
    ok, error = update_state_from_ui(
        store,
        background_key,
        (255, 0, 0),
        meta=background_meta,
    )
    assert ok, error

    second = session.render(1.0)

    assert session.param_store is store
    assert session.config is config
    assert first.metadata is session.metadata
    assert second.metadata is session.metadata
    assert first.metadata.effective_config is config
    assert first.background_color.rgb01 == (1.0, 1.0, 1.0)
    assert second.background_color.rgb01 == (1.0, 0.0, 0.0)
    assert first.layers[0].realized is second.layers[0].realized

    session.close()


def test_render_session_is_context_managed_and_close_is_idempotent() -> None:
    with RenderSession(_constant_draw()) as session:
        frame = session.render(2.5)
        assert frame.t == pytest.approx(2.5)
        assert isinstance(frame.layers, tuple)

    session.close()
    with pytest.raises(RuntimeError, match="close 済み"):
        session.render(3.0)
    with pytest.raises(RuntimeError, match="close 済み"):
        session.__enter__()


@pytest.mark.parametrize(
    ("failure_stage", "expected_events"),
    [
        (
            "cache",
            ["create resources", "create cache", "close resources"],
        ),
        (
            "realize",
            [
                "create resources",
                "create cache",
                "create realize",
                "close cache",
                "close resources",
            ],
        ),
        (
            "metadata",
            [
                "create resources",
                "create cache",
                "create realize",
                "create metadata",
                "close realize",
                "close cache",
                "close resources",
            ],
        ),
    ],
)
def test_render_session_constructor_failure_closes_created_dependencies_in_reverse_order(
    failure_stage: str,
    expected_events: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    root_error = RuntimeError(f"{failure_stage} construction failed")
    _install_render_dependency_fakes(
        monkeypatch,
        events=events,
        construction_error=(failure_stage, root_error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        RenderSession(lambda _t: ())

    assert exc_info.value is root_error
    assert events == expected_events


def test_render_session_constructor_keeps_root_and_notes_all_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    root_error = RuntimeError("metadata construction failed")
    close_errors = {
        "realize": RuntimeError("realize close failed"),
        "cache": OSError("cache close failed"),
        "resources": KeyboardInterrupt("resources close failed"),
    }
    _install_render_dependency_fakes(
        monkeypatch,
        events=events,
        close_errors=close_errors,
        construction_error=("metadata", root_error),
    )

    with pytest.raises(RuntimeError) as exc_info:
        RenderSession(lambda _t: ())

    assert exc_info.value is root_error
    assert events[-3:] == ["close realize", "close cache", "close resources"]
    assert root_error.__notes__ == [
        "Secondary cleanup failure (close render realize session): "
        "RuntimeError: realize close failed",
        "Secondary cleanup failure (close render realize cache store): "
        "OSError: cache close failed",
        "Secondary cleanup failure (close render evaluation resources): "
        "KeyboardInterrupt: resources close failed",
    ]


@pytest.mark.parametrize("close_mode", ["direct", "context"])
def test_render_session_close_raises_first_cleanup_error_and_is_idempotent(
    close_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    first_error = RuntimeError("realize close failed")
    close_errors = {
        "realize": first_error,
        "cache": OSError("cache close failed"),
        "resources": KeyboardInterrupt("resources close failed"),
    }
    _install_render_dependency_fakes(
        monkeypatch,
        events=events,
        close_errors=close_errors,
    )
    session = RenderSession(lambda _t: ())
    events.clear()

    with pytest.raises(RuntimeError) as exc_info:
        if close_mode == "direct":
            session.close()
        else:
            with session:
                pass

    assert exc_info.value is first_error
    assert events == ["close realize", "close cache", "close resources"]
    assert first_error.__notes__ == [
        "Secondary cleanup failure (close render realize cache store): "
        "OSError: cache close failed",
        "Secondary cleanup failure (close render evaluation resources): "
        "KeyboardInterrupt: resources close failed",
    ]
    events.clear()
    session.close()
    assert events == []


def test_render_session_exit_keeps_body_error_and_notes_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    body_error = ValueError("draw body failed")
    _install_render_dependency_fakes(
        monkeypatch,
        events=events,
        close_errors={
            "realize": RuntimeError("realize close failed"),
            "cache": OSError("cache close failed"),
            "resources": KeyboardInterrupt("resources close failed"),
        },
    )
    session = RenderSession(lambda _t: ())
    events.clear()

    with pytest.raises(ValueError) as exc_info:
        with session:
            raise body_error

    assert exc_info.value is body_error
    assert events == ["close realize", "close cache", "close resources"]
    assert body_error.__notes__ == [
        "Secondary cleanup failure (close render realize session): "
        "RuntimeError: realize close failed",
        "Secondary cleanup failure (close render realize cache store): "
        "OSError: cache close failed",
        "Secondary cleanup failure (close render evaluation resources): "
        "KeyboardInterrupt: resources close failed",
    ]


def test_render_session_exposes_only_non_owner_properties() -> None:
    public_properties = {
        name
        for name, value in vars(RenderSession).items()
        if isinstance(value, property) and not name.startswith("_")
    }

    assert public_properties == {
        "config",
        "metadata",
        "options",
        "param_store",
        "runtime_limits",
    }


def test_public_render_returns_one_final_headless_frame() -> None:
    observed_quality: list[str] = []

    def draw(t: float):
        observed_quality.append(current_preview_quality())
        return _constant_draw()(t)

    with preview_quality_context("draft"):
        frame = render(draw, 1.5, options=RenderOptions(canvas_size=(120, 80)))

    assert isinstance(frame, Frame)
    assert frame.t == pytest.approx(1.5)
    assert frame.canvas_size == (120, 80)
    assert observed_quality == ["final"]


def test_render_session_forces_final_quality_inside_draft_context() -> None:
    observed_quality: list[str] = []

    def draw(t: float):
        observed_quality.append(current_preview_quality())
        return _constant_draw()(t)

    with preview_quality_context("draft"), RenderSession(draw) as session:
        frame = session.render(0.0)

    assert observed_quality == ["final"]
    assert frame.provenance.frame.quality == "final"


def test_render_session_uses_final_runtime_limits() -> None:
    limits = RuntimeLimits(
        per_operation=ResourceBudget(
            max_output_vertices=10,
            max_output_lines=10,
            max_output_bytes=10_000,
        ),
        scene=ResourceBudget(
            max_output_vertices=1,
            max_output_lines=10,
            max_output_bytes=10_000,
        ),
    )
    with RenderSession(_constant_draw(), runtime_limits=limits) as session:
        assert session.runtime_limits is limits
        with pytest.raises(ResourceLimitError, match="scene aggregate"):
            session.render(0.0)


def test_code_parameter_source_does_not_read_implicit_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    render_module = importlib.import_module("grafix.api.render")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("code mode must not inspect or load parameter files")

    monkeypatch.setattr(render_module, "default_param_store_path", unexpected)
    monkeypatch.setattr(render_module, "read_param_store", unexpected)
    monkeypatch.setattr(render_module, "recover_param_store_session", unexpected)

    with RenderSession(_constant_draw()) as session:
        frame = session.render(0.0)

    assert frame.metadata.parameter_source == "code"
    assert frame.metadata.parameter_store_path is None


@pytest.mark.parametrize(
    ("parameter_source", "expected_loader", "expected_source"),
    [
        ("saved", "saved", "saved"),
        ("recovery", "recovery", "recovery"),
        (Path("specific.json"), "saved", "path"),
    ],
)
def test_parameter_source_selects_one_explicit_load_path(
    parameter_source: str | Path,
    expected_loader: str,
    expected_source: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    render_module = importlib.import_module("grafix.api.render")

    default_path = tmp_path / "default.json"
    calls: list[tuple[str, Path]] = []

    monkeypatch.setattr(
        render_module,
        "default_param_store_path",
        lambda *_args, **_kwargs: default_path,
    )

    def read_saved(path: Path) -> ParamStoreReadResult:
        calls.append(("saved", Path(path)))
        return ParamStoreReadResult(ParamStore(), "loaded")

    def load_recovery(path: Path) -> ParamStore:
        calls.append(("recovery", Path(path)))
        return ParamStore()

    monkeypatch.setattr(render_module, "read_param_store", read_saved)
    monkeypatch.setattr(render_module, "recover_param_store_session", load_recovery)

    source: str | Path
    if isinstance(parameter_source, Path):
        source = tmp_path / parameter_source
    else:
        source = parameter_source
    with RenderSession(_constant_draw(), parameter_source=source) as session:
        metadata = session.metadata

    assert calls == [
        (
            expected_loader,
            (tmp_path / "specific.json").resolve() if expected_source == "path" else default_path,
        )
    ]
    assert metadata.parameter_source == (
        (tmp_path / "specific.json").resolve() if expected_source == "path" else expected_source
    )


@pytest.mark.parametrize("parameter_source", ["saved", "path"])
def test_saved_and_path_parameter_sources_do_not_mutate_broken_file(
    parameter_source: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    render_module = importlib.import_module("grafix.api.render")
    path = tmp_path / "broken.json"
    original_payload = "{broken-json"
    path.write_text(original_payload, encoding="utf-8")
    monkeypatch.setattr(
        render_module,
        "default_param_store_path",
        lambda *_args, **_kwargs: path,
    )
    source: str | Path = "saved" if parameter_source == "saved" else path

    with RenderSession(lambda _t: (), parameter_source=source) as session:
        assert session.param_store.load_diagnostics[0].code == "load_error"

    assert path.read_text(encoding="utf-8") == original_payload
    assert list(tmp_path.glob("broken.json.corrupt-*")) == []
    assert not (tmp_path / "broken.session.json").exists()


def test_render_session_rejects_unknown_parameter_source() -> None:
    with pytest.raises(ValueError, match="parameter_source"):
        RenderSession(_constant_draw(), parameter_source="implicit")  # type: ignore[arg-type]


def test_render_session_metadata_keeps_effective_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """version: 1
paths:
  output_dir: artifacts
""",
        encoding="utf-8",
    )

    with RenderSession(_constant_draw(), config_path=config_path) as session:
        frame = session.render(0.0)
        metadata = session.metadata

    assert metadata.config_path == config_path.resolve()
    assert metadata.effective_config is frame.metadata.effective_config
    assert metadata.effective_config.output_dir == (tmp_path / "artifacts").resolve()


def test_render_session_keeps_explicit_config_identity_and_rejects_path_pair(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths:\n  output_dir: artifacts\n", encoding="utf-8")
    config = load_runtime_config(config_path)

    with RenderSession(_constant_draw(), config=config) as session:
        assert session.config is config

    with pytest.raises(ValueError, match="同時"):
        RenderSession(
            _constant_draw(),
            config=config,
            config_path=config_path,
        )


def test_render_session_freezes_config_without_mutating_default_discovery(
    tmp_path: Path,
) -> None:
    session_config_path = tmp_path / "session.yaml"
    session_config_path.write_text(
        "version: 1\npaths:\n  output_dir: session-artifacts\n",
        encoding="utf-8",
    )
    observed_configs = []

    def draw(t: float):
        observed_configs.append(current_runtime_config())
        return _constant_draw()(t)

    default_before = runtime_config()
    session = RenderSession(draw, config_path=session_config_path)
    session_config = session.config

    # Session 作成後に設定ファイルが変化しても、評価は開始時 snapshot を使う。
    session_config_path.write_text(
        "version: 1\npaths:\n  output_dir: changed-artifacts\n",
        encoding="utf-8",
    )
    session.render(0.0)
    session.close()

    assert observed_configs == [session_config]
    assert session_config.output_dir == (tmp_path / "session-artifacts").resolve()
    assert runtime_config() == default_before


def test_render_session_config_load_failure_does_not_change_existing_session(
    tmp_path: Path,
) -> None:
    valid_config_path = tmp_path / "valid.yaml"
    valid_config_path.write_text(
        "version: 1\npaths:\n  output_dir: valid-artifacts\n",
        encoding="utf-8",
    )
    invalid_config_path = tmp_path / "invalid.yaml"
    invalid_config_path.write_text("paths:\n  outpt_dir: broken\n", encoding="utf-8")

    existing = RenderSession(_constant_draw(), config_path=valid_config_path)

    with pytest.raises(RuntimeError, match="paths.outpt_dir"):
        RenderSession(_constant_draw(), config_path=invalid_config_path)

    frame = existing.render(0.0)
    existing.close()
    assert frame.metadata.effective_config.output_dir == (tmp_path / "valid-artifacts").resolve()


@pytest.mark.parametrize("first_to_close", ["a", "b"])
def test_render_sessions_are_isolated_for_any_close_order(
    tmp_path: Path,
    first_to_close: Literal["a", "b"],
) -> None:
    config_a = tmp_path / "a.yaml"
    config_b = tmp_path / "b.yaml"
    config_a.write_text("paths:\n  output_dir: a-output\n", encoding="utf-8")
    config_b.write_text("paths:\n  output_dir: b-output\n", encoding="utf-8")

    observed_a = []
    observed_b = []

    def draw_a(_t: float):
        observed_a.append(current_runtime_config())
        return ()

    def draw_b(_t: float):
        observed_b.append(current_runtime_config())
        return ()

    session_a = RenderSession(draw_a, config_path=config_a)
    session_b = RenderSession(draw_b, config_path=config_b)
    session_a.render(0.0)
    session_b.render(0.0)
    session_a.render(0.5)
    session_b.render(0.5)

    # A/B を交互に評価した後、どちらを先に close しても、
    # 残った session の config は変わらない。
    if first_to_close == "a":
        session_a.close()
        session_b.render(1.0)
        session_b.close()
    else:
        session_b.close()
        session_a.render(1.0)
        session_a.close()

    assert all(config is session_a.config for config in observed_a)
    assert all(config is session_b.config for config in observed_b)
    assert len(observed_a) == (2 if first_to_close == "a" else 3)
    assert len(observed_b) == (3 if first_to_close == "a" else 2)
    assert session_a.config.output_dir == (tmp_path / "a-output").resolve()
    assert session_b.config.output_dir == (tmp_path / "b-output").resolve()


def test_render_session_runtime_config_binding_is_thread_local(tmp_path: Path) -> None:
    config_a = tmp_path / "thread-a.yaml"
    config_b = tmp_path / "thread-b.yaml"
    config_a.write_text("paths:\n  output_dir: thread-a\n", encoding="utf-8")
    config_b.write_text("paths:\n  output_dir: thread-b\n", encoding="utf-8")
    barrier = Barrier(2)
    observed: dict[str, Path] = {}

    def make_draw(name: str):
        def draw(_t: float):
            barrier.wait(timeout=5.0)
            observed[name] = current_runtime_config().output_dir
            return ()

        return draw

    session_a = RenderSession(make_draw("a"), config_path=config_a)
    session_b = RenderSession(make_draw("b"), config_path=config_b)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(session_a.render, 0.0)
        future_b = executor.submit(session_b.render, 0.0)
        future_a.result(timeout=10.0)
        future_b.result(timeout=10.0)
    session_a.close()
    session_b.close()

    assert observed == {
        "a": (tmp_path / "thread-a").resolve(),
        "b": (tmp_path / "thread-b").resolve(),
    }


@pytest.mark.parametrize("parameter_source", ["saved", "recovery"])
def test_render_session_parameter_path_uses_session_config(
    tmp_path: Path,
    parameter_source: Literal["saved", "recovery"],
) -> None:
    config_path = tmp_path / f"{parameter_source}.yaml"
    config_path.write_text(
        f"paths:\n  output_dir: {parameter_source}-output\n",
        encoding="utf-8",
    )

    with RenderSession(
        _constant_draw(),
        config_path=config_path,
        parameter_source=parameter_source,
    ) as session:
        store_path = session.metadata.parameter_store_path

    assert store_path is not None
    assert store_path.is_relative_to((tmp_path / f"{parameter_source}-output").resolve())


def test_render_session_preset_autoload_uses_session_config(tmp_path: Path) -> None:
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir()
    (preset_dir / "session_config.py").write_text(
        "\n".join(
            (
                "from grafix import G, preset",
                "",
                "@preset(meta={})",
                "def phase1_session_config_preset():",
                "    return G.line(length=1.0)",
                "",
            )
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "preset.yaml"
    config_path.write_text(
        f"paths:\n  preset_module_dirs:\n    - {preset_dir.as_posix()}\n",
        encoding="utf-8",
    )

    def draw(_t: float):
        return P.phase1_session_config_preset()

    with RenderSession(draw, config_path=config_path) as session:
        frame = session.render(0.0)

    assert len(frame.layers) == 1


def test_render_session_text_resolution_uses_session_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_resolve = FontResources.resolve
    observed_font_dirs: list[tuple[Path, ...]] = []
    observed_configs: list[object] = []

    def resolve_with_observation(self, font, face_index, *, config):
        observed_configs.append(config)
        observed_font_dirs.append(config.font_dirs)
        return original_resolve(self, font, face_index, config=config)

    monkeypatch.setattr(FontResources, "resolve", resolve_with_observation)
    font_dir = tmp_path / "fonts"
    config_path = tmp_path / "font.yaml"
    config_path.write_text(
        f"paths:\n  font_dirs:\n    - {font_dir.as_posix()}\n",
        encoding="utf-8",
    )

    def draw(_t: float):
        return G.text(text="A", font="GoogleSans-Regular.ttf")

    with RenderSession(draw, config_path=config_path) as session:
        fixed_config = session.config
        session.render(0.0)

    assert observed_font_dirs == [((font_dir).resolve(),)]
    assert observed_configs == [EvaluationConfig(font_dirs=fixed_config.font_dirs)]
    assert type(observed_configs[0]) is EvaluationConfig
