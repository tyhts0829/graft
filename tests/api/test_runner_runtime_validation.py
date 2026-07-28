from __future__ import annotations

import inspect
from typing import Any, get_type_hints

import pytest
import pyglet

pyglet.options["shadow_window"] = False
import grafix.api._runner_application as application_module  # noqa: E402
import grafix.api.runner as runner_module  # noqa: E402
from grafix.core.runtime_limits import (  # noqa: E402
    DEFAULT_RUNTIME_LIMIT_PROFILES,
    RuntimeLimitProfiles,
)
from grafix.runtime_config_loader import runtime_config  # noqa: E402


def _draw(_t: float) -> None:
    return None


class _StringSubclass(str):
    pass


class _RuntimeLimitProfilesSubclass(RuntimeLimitProfiles):
    pass


_SUBCLASS_PROFILES = _RuntimeLimitProfilesSubclass(
    preview=DEFAULT_RUNTIME_LIMIT_PROFILES.preview,
    final=DEFAULT_RUNTIME_LIMIT_PROFILES.final,
)


def _assert_rejected_before_side_effect(
    monkeypatch: pytest.MonkeyPatch,
    *,
    kwargs: dict[str, Any],
    error_type: type[Exception],
    match: str,
) -> None:
    config_load_calls: list[object] = []
    monkeypatch.setattr(
        application_module,
        "runtime_config_with_fallback",
        config_load_calls.append,
    )

    with pytest.raises(error_type, match=match):
        runner_module.run(_draw, **kwargs)

    assert config_load_calls == []


@pytest.mark.parametrize("n_worker", [True, 1.0, "1"])
def test_run_rejects_implicitly_convertible_worker_count(
    n_worker: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={"n_worker": n_worker},
        error_type=TypeError,
        match="n_worker.*int",
    )


@pytest.mark.parametrize(
    "timeout",
    [True, "1", 0.0, -1.0, float("inf"), float("nan")],
)
def test_run_rejects_invalid_evaluation_timeout(timeout: object) -> None:
    expected_error = TypeError if isinstance(timeout, (bool, str)) else ValueError
    with pytest.raises(expected_error, match="evaluation_timeout"):
        runner_module.run(_draw, evaluation_timeout=timeout)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["parameter_gui", "parameter_persistence"])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_run_requires_exact_boolean_flags_before_side_effect(
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={field: value},
        error_type=TypeError,
        match=f"{field}.*bool",
    )


@pytest.mark.parametrize("mode", [7, b"7bit", _StringSubclass("7bit")])
def test_run_requires_exact_string_midi_mode_before_side_effect(
    mode: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={"midi_mode": mode},
        error_type=TypeError,
        match="midi_mode.*str",
    )


def test_run_rejects_unknown_midi_mode_before_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={"midi_mode": "16bit"},
        error_type=ValueError,
        match="midi_mode",
    )


@pytest.mark.parametrize(
    ("fps", "error_type"),
    [
        (True, TypeError),
        ("60", TypeError),
        (float("inf"), ValueError),
        (float("-inf"), ValueError),
        (float("nan"), ValueError),
    ],
)
def test_run_rejects_invalid_fps_before_side_effect(
    fps: object,
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={"fps": fps},
        error_type=error_type,
        match="fps",
    )


@pytest.mark.parametrize(
    ("render_scale", "error_type"),
    [
        (True, TypeError),
        ("1", TypeError),
        (0.0, ValueError),
        (-1.0, ValueError),
        (float("inf"), ValueError),
        (float("nan"), ValueError),
    ],
)
def test_run_rejects_invalid_render_scale_before_side_effect(
    render_scale: object,
    error_type: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={"render_scale": render_scale},
        error_type=error_type,
        match="render_scale",
    )


def test_public_run_defaults_render_scale_to_workspace_managed() -> None:
    parameter = inspect.signature(runner_module.run).parameters["render_scale"]

    assert parameter.default is None
    assert get_type_hints(runner_module.run)["render_scale"] == float | None


def test_run_accepts_workspace_managed_render_scale_before_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConfigPathReached(RuntimeError):
        pass

    def stop_after_validation(_path: object) -> None:
        raise ConfigPathReached

    monkeypatch.setattr(
        application_module,
        "runtime_config_with_fallback",
        stop_after_validation,
    )

    with pytest.raises(ConfigPathReached):
        runner_module.run(_draw, render_scale=None)


@pytest.mark.parametrize("seed", [True, 1.0, "1"])
def test_run_requires_exact_integer_seed_before_side_effect(
    seed: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={"seed": seed},
        error_type=TypeError,
        match="seed.*int",
    )


@pytest.mark.parametrize("profiles", [object(), _SUBCLASS_PROFILES])
def test_run_requires_exact_runtime_limit_profiles_type_before_side_effect(
    profiles: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_side_effect(
        monkeypatch,
        kwargs={"runtime_limit_profiles": profiles},
        error_type=TypeError,
        match="runtime_limit_profiles",
    )


@pytest.mark.parametrize("fps", [0, -1, 0.0, -1.0])
def test_run_preserves_nonpositive_fps_contract(
    fps: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ConfigPathReached(RuntimeError):
        pass

    def stop_after_validation(_path: object) -> None:
        raise ConfigPathReached

    monkeypatch.setattr(
        application_module,
        "runtime_config_with_fallback",
        stop_after_validation,
    )
    with pytest.raises(ConfigPathReached):
        runner_module.run(_draw, fps=fps)  # type: ignore[arg-type]


def test_run_rejects_config_and_config_path_before_config_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(application_module, "runtime_config_with_fallback", calls.append)

    with pytest.raises(ValueError, match="同時"):
        runner_module.run(
            _draw,
            config=runtime_config(),
            config_path="config.yaml",
        )

    assert calls == []


def test_public_run_does_not_expose_internal_config_fallback() -> None:
    assert "config_fallback" not in inspect.signature(runner_module.run).parameters
    with pytest.raises(TypeError, match="config_fallback"):
        runner_module.run(_draw, config_fallback=object())  # type: ignore[call-arg]
