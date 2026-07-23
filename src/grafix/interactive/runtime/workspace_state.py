"""sketch/run ごとの window workspace 状態を versioned JSON で保存する。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from grafix.core.runtime_config import RuntimeConfig
from grafix.file_io import atomic_write_text
from grafix.export.output_paths import output_path_for_draw
from grafix.core.value_validation import finite_real
from grafix.interactive.diagnostics import DiagnosticEvent
from grafix.interactive.runtime.window_layout import WindowRect

WORKSPACE_STATE_SCHEMA_VERSION = 1
WorkspaceLoadStatus = Literal["loaded", "missing", "corrupt", "old", "future"]


@dataclass(frozen=True, slots=True)
class WorkspaceState:
    """preview / Inspector の workspace 状態。"""

    preview_rect: WindowRect
    inspector_rect: WindowRect | None
    inspector_visible: bool
    ui_scale: float = 1.0

    def __post_init__(self) -> None:
        _validate_rect(self.preview_rect, key="preview_rect")
        if self.inspector_rect is not None:
            _validate_rect(self.inspector_rect, key="inspector_rect")
        if not isinstance(self.inspector_visible, bool):
            raise TypeError("inspector_visible は bool である必要がある")
        ui_scale = finite_real(
            self.ui_scale,
            name="ui_scale",
            minimum=0.0,
            minimum_inclusive=False,
        )
        object.__setattr__(self, "ui_scale", ui_scale)


@dataclass(frozen=True, slots=True)
class WorkspaceStateLoadResult:
    """load 結果と、呼び出し側が publish できる診断。"""

    state: WorkspaceState
    status: WorkspaceLoadStatus
    source: Path
    diagnostic: DiagnosticEvent | None = None

    @property
    def restored(self) -> bool:
        """JSON から現行 schema の状態を復元したか。"""

        return self.status == "loaded"


def _validate_rect(rect: WindowRect, *, key: str) -> None:
    if not isinstance(rect, WindowRect):
        raise TypeError(f"{key} は WindowRect である必要がある")
    if int(rect.width) <= 0 or int(rect.height) <= 0:
        raise ValueError(f"{key} の width/height は正である必要がある")


def default_workspace_state_path(
    draw: Callable[[float], object],
    *,
    config: RuntimeConfig,
    run_id: str | None = None,
) -> Path:
    """draw の sketch/run identity に対応する保存先を返す。"""

    return output_path_for_draw(
        kind="workspace",
        ext="json",
        draw=draw,
        run_id=run_id,
        config=config,
    )


def _rect_to_payload(rect: WindowRect | None) -> dict[str, int] | None:
    if rect is None:
        return None
    return {
        "x": int(rect.x),
        "y": int(rect.y),
        "width": int(rect.width),
        "height": int(rect.height),
    }


def dumps_workspace_state(state: WorkspaceState) -> str:
    """WorkspaceState を安定した JSON 文字列にする。"""

    payload = {
        "schema_version": WORKSPACE_STATE_SCHEMA_VERSION,
        "preview_rect": _rect_to_payload(state.preview_rect),
        "inspector_rect": _rect_to_payload(state.inspector_rect),
        "inspector_visible": state.inspector_visible,
        "ui_scale": state.ui_scale,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def save_workspace_state(state: WorkspaceState, path: str | Path) -> Path:
    """WorkspaceState を atomic に保存する。"""

    target = Path(path)
    atomic_write_text(target, dumps_workspace_state(state), encoding="utf-8")
    return target


def _as_mapping(value: Any, *, key: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{key} は mapping である必要がある")
    return value


def _rect_from_payload(value: Any, *, key: str) -> WindowRect:
    mapping = _as_mapping(value, key=key)
    expected = {"x", "y", "width", "height"}
    if set(mapping) != expected:
        raise ValueError(f"{key} は x/y/width/height だけを含む必要がある")
    values: dict[str, int] = {}
    for field in ("x", "y", "width", "height"):
        raw = mapping[field]
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError(f"{key}.{field} は整数である必要がある")
        values[field] = int(raw)
    rect = WindowRect(**values)
    _validate_rect(rect, key=key)
    return rect


def _state_from_payload(payload: Mapping[str, Any]) -> WorkspaceState:
    expected = {
        "schema_version",
        "preview_rect",
        "inspector_rect",
        "inspector_visible",
        "ui_scale",
    }
    if set(payload) != expected:
        raise ValueError("WorkspaceState の key set が現行 schema と一致しない")
    inspector_raw = payload["inspector_rect"]
    inspector_rect = (
        None
        if inspector_raw is None
        else _rect_from_payload(inspector_raw, key="inspector_rect")
    )
    visible = payload["inspector_visible"]
    if not isinstance(visible, bool):
        raise ValueError("inspector_visible は bool である必要がある")
    ui_scale = payload["ui_scale"]
    if isinstance(ui_scale, bool) or not isinstance(ui_scale, (int, float)):
        raise ValueError("ui_scale は数値である必要がある")
    return WorkspaceState(
        preview_rect=_rect_from_payload(payload["preview_rect"], key="preview_rect"),
        inspector_rect=inspector_rect,
        inspector_visible=visible,
        ui_scale=float(ui_scale),
    )


def _fallback_diagnostic(
    *,
    status: Literal["corrupt", "old", "future"],
    path: Path,
    details: str,
) -> DiagnosticEvent:
    summaries = {
        "corrupt": "WorkspaceState が破損しているため初期 layout を使用します",
        "old": "古い WorkspaceState のため初期 layout を使用します",
        "future": "未対応の WorkspaceState のため初期 layout を使用します",
    }
    return DiagnosticEvent(
        category="workspace",
        severity="warning",
        summary=summaries[status],
        details=details,
        source=str(path),
        dedupe_key=f"workspace-{status}:{path}",
    )


def load_workspace_state(
    path: str | Path,
    *,
    fallback: WorkspaceState,
) -> WorkspaceStateLoadResult:
    """WorkspaceState を読み、読めなければ fallback と診断を返す。"""

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return WorkspaceStateLoadResult(fallback, "missing", source)
    except (OSError, UnicodeError) as exc:
        return WorkspaceStateLoadResult(
            fallback,
            "corrupt",
            source,
            _fallback_diagnostic(
                status="corrupt",
                path=source,
                details=str(exc),
            ),
        )

    try:
        decoded = json.loads(text)
        payload = _as_mapping(decoded, key="WorkspaceState")
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return WorkspaceStateLoadResult(
            fallback,
            "corrupt",
            source,
            _fallback_diagnostic(status="corrupt", path=source, details=str(exc)),
        )

    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        status: Literal["old", "future"] = "old"
        details = "schema_version がありません"
    elif int(version) < WORKSPACE_STATE_SCHEMA_VERSION:
        status = "old"
        details = f"schema_version={version}"
    elif int(version) > WORKSPACE_STATE_SCHEMA_VERSION:
        status = "future"
        details = f"schema_version={version}"
    else:
        try:
            state = _state_from_payload(payload)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return WorkspaceStateLoadResult(
                fallback,
                "corrupt",
                source,
                _fallback_diagnostic(status="corrupt", path=source, details=str(exc)),
            )
        return WorkspaceStateLoadResult(state, "loaded", source)

    return WorkspaceStateLoadResult(
        fallback,
        status,
        source,
        _fallback_diagnostic(status=status, path=source, details=details),
    )


def _intersection_area(a: WindowRect, b: WindowRect) -> int:
    width = max(0, min(a.right, b.right) - max(a.x, b.x))
    height = max(0, min(a.bottom, b.bottom) - max(a.y, b.y))
    return int(width * height)


def _distance_squared_to_center(rect: WindowRect, bounds: WindowRect) -> float:
    rect_x = float(rect.x) + float(rect.width) / 2.0
    rect_y = float(rect.y) + float(rect.height) / 2.0
    bounds_x = float(bounds.x) + float(bounds.width) / 2.0
    bounds_y = float(bounds.y) + float(bounds.height) / 2.0
    return (rect_x - bounds_x) ** 2 + (rect_y - bounds_y) ** 2


def clamp_window_rect(rect: WindowRect, screen_bounds: Sequence[WindowRect]) -> WindowRect:
    """rect を最も近い現在 screen 内へ clamp する。"""

    bounds = tuple(screen_bounds)
    if not bounds:
        raise ValueError("screen_bounds は 1 件以上必要である")
    for index, candidate in enumerate(bounds):
        _validate_rect(candidate, key=f"screen_bounds[{index}]")

    target = max(
        bounds,
        key=lambda candidate: (
            _intersection_area(rect, candidate),
            -_distance_squared_to_center(rect, candidate),
        ),
    )
    width = min(int(rect.width), int(target.width))
    height = min(int(rect.height), int(target.height))
    x = max(int(target.x), min(int(rect.x), int(target.right - width)))
    y = max(int(target.y), min(int(rect.y), int(target.bottom - height)))
    return WindowRect(x=x, y=y, width=width, height=height)


def clamp_workspace_state(
    state: WorkspaceState,
    *,
    screen_bounds: Sequence[WindowRect],
) -> WorkspaceState:
    """WorkspaceState の各 rect を現在の screen bounds 内に収める。"""

    return WorkspaceState(
        preview_rect=clamp_window_rect(state.preview_rect, screen_bounds),
        inspector_rect=(
            None
            if state.inspector_rect is None
            else clamp_window_rect(state.inspector_rect, screen_bounds)
        ),
        inspector_visible=state.inspector_visible,
        ui_scale=state.ui_scale,
    )


__all__ = [
    "WORKSPACE_STATE_SCHEMA_VERSION",
    "WorkspaceLoadStatus",
    "WorkspaceState",
    "WorkspaceStateLoadResult",
    "clamp_window_rect",
    "clamp_workspace_state",
    "default_workspace_state_path",
    "dumps_workspace_state",
    "load_workspace_state",
    "save_workspace_state",
]
