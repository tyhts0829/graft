"""versioned WorkspaceState の保存・fallback・screen clamp を検証する。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import grafix.interactive.runtime.workspace_state as workspace_module
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.runtime.window_layout import WindowRect
from grafix.interactive.runtime.workspace_state import (
    WORKSPACE_STATE_SCHEMA_VERSION,
    WorkspaceState,
    clamp_workspace_state,
    default_workspace_state_path,
    load_workspace_state,
    save_workspace_state,
)


def _state() -> WorkspaceState:
    return WorkspaceState(
        preview_rect=WindowRect(100, 120, 800, 700),
        inspector_rect=WindowRect(920, 120, 520, 760),
        inspector_visible=False,
        ui_scale=1.25,
    )


@pytest.mark.parametrize(
    ("ui_scale", "error"),
    (
        (True, TypeError),
        ("1.25", TypeError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (0.0, ValueError),
        (-1.0, ValueError),
    ),
)
def test_workspace_state_validates_ui_scale(
    ui_scale: object,
    error: type[Exception],
) -> None:
    with pytest.raises(error, match="ui_scale"):
        WorkspaceState(
            preview_rect=WindowRect(100, 120, 800, 700),
            inspector_rect=None,
            inspector_visible=False,
            ui_scale=ui_scale,  # type: ignore[arg-type]
        )


def test_workspace_state_roundtrip_uses_versioned_json(tmp_path: Path) -> None:
    path = tmp_path / "workspace.json"
    expected = _state()

    assert save_workspace_state(expected, path) == path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == WORKSPACE_STATE_SCHEMA_VERSION

    result = load_workspace_state(path, fallback=WorkspaceState(
        WindowRect(0, 0, 1, 1), None, True
    ))
    assert result.status == "loaded"
    assert result.restored is True
    assert result.state == expected
    assert result.diagnostic is None


def test_missing_workspace_uses_fallback_without_diagnostic(tmp_path: Path) -> None:
    fallback = _state()
    result = load_workspace_state(tmp_path / "missing.json", fallback=fallback)

    assert result.status == "missing"
    assert result.restored is False
    assert result.state is fallback
    assert result.diagnostic is None


def test_corrupt_workspace_uses_fallback_with_publishable_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "workspace.json"
    path.write_text("{broken", encoding="utf-8")
    fallback = _state()

    result = load_workspace_state(path, fallback=fallback)

    assert result.status == "corrupt"
    assert result.state is fallback
    assert result.diagnostic is not None
    assert result.diagnostic.category == "workspace"
    assert result.diagnostic.severity == "warning"
    assert result.diagnostic.source == str(path)


@pytest.mark.parametrize("payload", ({}, {"schema_version": 0}))
def test_old_workspace_uses_fallback_with_diagnostic(
    payload: dict[str, object],
    tmp_path: Path,
) -> None:
    path = tmp_path / "workspace.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    fallback = _state()

    result = load_workspace_state(path, fallback=fallback)

    assert result.status == "old"
    assert result.state is fallback
    assert result.diagnostic is not None
    assert "古い WorkspaceState" in result.diagnostic.summary


def test_workspace_rects_are_clamped_after_screen_configuration_change() -> None:
    saved = WorkspaceState(
        preview_rect=WindowRect(2050, 100, 900, 900),
        inspector_rect=WindowRect(3000, -200, 1000, 1200),
        inspector_visible=True,
        ui_scale=1.5,
    )
    current_screen = WindowRect(0, 0, 1440, 900)

    clamped = clamp_workspace_state(saved, screen_bounds=(current_screen,))

    assert clamped.preview_rect == WindowRect(540, 0, 900, 900)
    assert clamped.inspector_rect == WindowRect(440, 0, 1000, 900)
    assert clamped.inspector_visible is True
    assert clamped.ui_scale == 1.5


def test_default_workspace_path_keeps_sketch_and_run_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    config = runtime_config()

    def fake_output_path_for_draw(**kwargs: object) -> Path:
        calls.append(dict(kwargs))
        return Path("out/workspace/sketch_v2.json")

    monkeypatch.setattr(workspace_module, "output_path_for_draw", fake_output_path_for_draw)

    def draw(_t: float) -> object:
        return None

    assert default_workspace_state_path(draw, run_id="v2", config=config) == Path(
        "out/workspace/sketch_v2.json"
    )
    assert calls == [
        {
            "kind": "workspace",
            "ext": "json",
            "draw": draw,
            "run_id": "v2",
            "config": config,
        }
    ]


def test_default_workspace_path_requires_explicit_config() -> None:
    with pytest.raises(TypeError, match="config"):
        default_workspace_state_path(lambda _t: None)  # type: ignore[call-arg]
