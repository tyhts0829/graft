"""Workspace window の配置、visibility、永続化を所有する controller。"""

from __future__ import annotations

import logging
from math import isfinite
from pathlib import Path
from sys import platform as _PLATFORM
from typing import Any

import pyglet
from pyglet.window import key

from grafix.interactive.draw_window import (
    MINIMUM_DRAW_WINDOW_HEIGHT,
    MINIMUM_DRAW_WINDOW_WIDTH,
)
from grafix.interactive.diagnostics import DiagnosticEvent
from grafix.interactive.runtime.window_layout import (
    DEFAULT_SCREEN_MARGIN,
    WindowRect,
    layout_window_pair,
)
from grafix.interactive.runtime.workspace_state import (
    WorkspaceState,
    clamp_window_rect,
    clamp_workspace_state,
    load_workspace_state,
    save_workspace_state,
)

_logger = logging.getLogger(__name__)


def _window_content_size(window: Any) -> tuple[int, int] | None:
    """window から現在の logical content size を得る。"""

    # pyglet の requested size は set_size() でしか更新されず、ユーザーの
    # drag resize を反映しない。現在の framebuffer を取得し、set_size() が
    # logical 単位を使う backend だけ backing scale で割って同じ単位へ戻す。
    try:
        framebuffer_width, framebuffer_height = window.get_framebuffer_size()
        dpi_scaling = pyglet.options["dpi_scaling"]
        uses_scaled_size = (_PLATFORM == "darwin" and dpi_scaling != "real") or (
            _PLATFORM == "win32" and dpi_scaling in ("scaled", "stretch")
        )
        scale = float(window.scale) if uses_scaled_size else 1.0
        if not isfinite(scale) or scale <= 0.0:
            raise ValueError("window scale must be positive and finite")
        width = int(round(float(framebuffer_width) / scale))
        height = int(round(float(framebuffer_height) / scale))
    except (AttributeError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        # test double や未完成の platform backend では現在値を取得できない場合がある。
        # その場合だけ requested size へ退避する。
        try:
            requested_width, requested_height = window.get_requested_size()
            width = int(requested_width)
            height = int(requested_height)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _full_screen_bounds(screen: Any) -> WindowRect | None:
    """pyglet Screen を top-left 原点の矩形へ変換する。"""

    try:
        bounds = WindowRect(
            x=int(screen.x),
            y=int(screen.y),
            width=int(screen.width),
            height=int(screen.height),
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if bounds.width < 2 or bounds.height < 2:
        return None
    return bounds


def _screen_for_position(window: Any, position: tuple[int, int]) -> Any | None:
    """preferred point を含む screen を選び、無ければ window.screen を返す。"""

    screens = list(window.display.get_screens())

    point_x, point_y = int(position[0]), int(position[1])
    for screen in screens:
        bounds = _full_screen_bounds(screen)
        if bounds is None:
            continue
        if bounds.x <= point_x < bounds.right and bounds.y <= point_y < bounds.bottom:
            return screen

    return window.screen


def _macos_visible_screen_bounds(screen: Any, full: WindowRect) -> WindowRect | None:
    """Cocoa NSScreen.visibleFrame を pyglet と同じ top-left 座標へ変換する。"""

    try:
        ns_screen = screen._ns_screen
        frame = ns_screen.frame()
        visible = ns_screen.visibleFrame()
        frame_x = float(frame.origin.x)
        frame_y = float(frame.origin.y)
        frame_w = float(frame.size.width)
        frame_h = float(frame.size.height)
        visible_x = float(visible.origin.x)
        visible_y = float(visible.origin.y)
        visible_w = float(visible.size.width)
        visible_h = float(visible.size.height)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    if frame_w <= 0.0 or frame_h <= 0.0 or visible_w <= 0.0 or visible_h <= 0.0:
        return None

    # Cocoa は bottom-left 原点、pyglet の set_location は top-left 原点。
    # Screen / NSScreen の単位が異なる環境にも比率で合わせる。
    scale_x = float(full.width) / frame_w
    scale_y = float(full.height) / frame_h
    candidate = WindowRect(
        x=int(round(float(full.x) + (visible_x - frame_x) * scale_x)),
        y=int(round(float(full.y) + (frame_y + frame_h - (visible_y + visible_h)) * scale_y)),
        width=int(round(visible_w * scale_x)),
        height=int(round(visible_h * scale_y)),
    )

    # private Cocoa bridge の値が不整合でも full screen 外へは出さない。
    left = max(full.x, candidate.x)
    top = max(full.y, candidate.y)
    right = min(full.right, candidate.right)
    bottom = min(full.bottom, candidate.bottom)
    if right - left < 2 or bottom - top < 2:
        return None
    return WindowRect(left, top, right - left, bottom - top)


def _usable_screen_bounds(window: Any, position: tuple[int, int]) -> WindowRect | None:
    """menu bar / Dock を除く usable bounds を返し、非Cocoaでは full bounds を返す。"""

    screen = _screen_for_position(window, position)
    if screen is None:
        return None
    full = _full_screen_bounds(screen)
    if full is None:
        return None
    return _macos_visible_screen_bounds(screen, full) or full


def _available_screen_bounds(window: Any) -> tuple[WindowRect, ...]:
    """window の display から現在使える screen bounds を返す。"""

    screens = list(window.display.get_screens())
    if not screens:
        screens = [window.screen]

    bounds: list[WindowRect] = []
    for screen in screens:
        full = _full_screen_bounds(screen)
        if full is None:
            continue
        usable = _macos_visible_screen_bounds(screen, full) or full
        safe = _safe_explicit_layout_bounds(usable)
        candidate = usable if safe is None else safe
        if candidate not in bounds:
            bounds.append(candidate)
    return tuple(bounds)


def _rect_is_inside(rect: WindowRect, bounds: WindowRect) -> bool:
    return bool(
        rect.x >= bounds.x
        and rect.y >= bounds.y
        and rect.right <= bounds.right
        and rect.bottom <= bounds.bottom
    )


def _rects_overlap(a: WindowRect, b: WindowRect) -> bool:
    return not (a.right <= b.x or b.right <= a.x or a.bottom <= b.y or b.bottom <= a.y)


def _safe_explicit_layout_bounds(bounds: WindowRect) -> WindowRect | None:
    """native frame分のmarginを除いた、明示content rectの安全領域を返す。"""

    margin = int(DEFAULT_SCREEN_MARGIN)
    width = int(bounds.width - 2 * margin)
    height = int(bounds.height - 2 * margin)
    if width < 2 or height < 2:
        return None
    return WindowRect(
        int(bounds.x + margin),
        int(bounds.y + margin),
        width,
        height,
    )


def _window_rect(window: Any) -> WindowRect | None:
    """window から logical content rect を取得する。"""

    size = _window_content_size(window)
    if size is None:
        return None
    try:
        x, y = window.get_location()
        return WindowRect(int(x), int(y), int(size[0]), int(size[1]))
    except (TypeError, ValueError, OverflowError):
        return None


def _apply_window_rect(window: Any, rect: WindowRect) -> None:
    size = _window_content_size(window)
    assert size is not None
    target_size = (int(rect.width), int(rect.height))
    if target_size != size:
        window.set_size(*target_size)
    window.set_location(int(rect.x), int(rect.y))


def _apply_preview_rect(window: Any, rect: WindowRect) -> None:
    """画面内へ収めた preview size と native minimum を整合させる。"""

    try:
        window.set_minimum_size(
            min(MINIMUM_DRAW_WINDOW_WIDTH, int(rect.width)),
            min(MINIMUM_DRAW_WINDOW_HEIGHT, int(rect.height)),
        )
    except AttributeError:
        # layout の unit test double は minimum-size API を持たない。
        pass
    _apply_window_rect(window, rect)


def _clamp_rect_preserving_aspect(
    rect: WindowRect,
    *,
    screen_bounds: tuple[WindowRect, ...],
) -> WindowRect:
    """rect を最適な screen へ、aspect ratio を保って収める。"""

    independently_clamped = clamp_window_rect(rect, screen_bounds)
    target = next(
        (bounds for bounds in screen_bounds if _rect_is_inside(independently_clamped, bounds)),
        screen_bounds[0],
    )
    scale = min(
        1.0,
        float(target.width) / float(rect.width),
        float(target.height) / float(rect.height),
    )
    width = max(1, min(target.width, int(round(float(rect.width) * scale))))
    height = max(1, min(target.height, int(round(float(rect.height) * scale))))
    return clamp_window_rect(
        WindowRect(rect.x, rect.y, width, height),
        (target,),
    )


def _apply_workspace_layout(
    *,
    preview_window: Any,
    inspector_window: Any | None,
    state: WorkspaceState,
    restore_preview_size: bool = True,
) -> bool:
    """保存 layout を現在 screen へ clamp して window へ適用する。

    ``restore_preview_size=False`` では保存済みの位置だけを使い、
    preview window が保持している現在の natural size を優先する。
    """

    bounds = _available_screen_bounds(preview_window)
    if not bounds:
        return False
    preview_size = _window_content_size(preview_window)
    if preview_size is None:
        return False
    if inspector_window is not None:
        if state.inspector_rect is None:
            return False
        if _window_content_size(inspector_window) is None:
            return False

    effective_state = state
    if not restore_preview_size:
        effective_state = WorkspaceState(
            preview_rect=WindowRect(
                state.preview_rect.x,
                state.preview_rect.y,
                preview_size[0],
                preview_size[1],
            ),
            inspector_rect=state.inspector_rect,
            inspector_visible=state.inspector_visible,
            ui_scale=state.ui_scale,
        )
    clamped = clamp_workspace_state(effective_state, screen_bounds=bounds)
    if not restore_preview_size:
        clamped = WorkspaceState(
            preview_rect=_clamp_rect_preserving_aspect(
                effective_state.preview_rect,
                screen_bounds=bounds,
            ),
            inspector_rect=clamped.inspector_rect,
            inspector_visible=clamped.inspector_visible,
            ui_scale=clamped.ui_scale,
        )

    _apply_preview_rect(preview_window, clamped.preview_rect)
    if inspector_window is not None:
        assert clamped.inspector_rect is not None
        _apply_window_rect(inspector_window, clamped.inspector_rect)
        inspector_window.set_visible(bool(clamped.inspector_visible))
    return True


def _apply_initial_preview_layout(
    *,
    preview_window: Any,
    preferred_preview_position: tuple[int, int],
) -> bool:
    """single preview の natural size を保ち、必要時だけ縮小する。"""

    preview_size = _window_content_size(preview_window)
    if preview_size is None:
        return False
    usable = _usable_screen_bounds(preview_window, preferred_preview_position)
    if usable is None:
        return False
    safe = _safe_explicit_layout_bounds(usable) or usable
    target = _clamp_rect_preserving_aspect(
        WindowRect(
            int(preferred_preview_position[0]),
            int(preferred_preview_position[1]),
            preview_size[0],
            preview_size[1],
        ),
        screen_bounds=(safe,),
    )
    _apply_preview_rect(preview_window, target)
    return True


def _workspace_state_from_windows(
    *,
    preview_window: Any,
    inspector_window: Any | None,
    previous: WorkspaceState,
) -> WorkspaceState | None:
    """終了時 window から保存用 state を作り、取得不能なら None を返す。"""

    preview_rect = _window_rect(preview_window)
    if preview_rect is None:
        return None
    inspector_rect = previous.inspector_rect
    inspector_visible = previous.inspector_visible
    if inspector_window is not None:
        current_inspector_rect = _window_rect(inspector_window)
        if current_inspector_rect is not None:
            inspector_rect = current_inspector_rect
        inspector_visible = bool(inspector_window.visible)
    return WorkspaceState(
        preview_rect=preview_rect,
        inspector_rect=inspector_rect,
        inspector_visible=inspector_visible,
        ui_scale=previous.ui_scale,
    )


def _persist_workspace_state_on_shutdown(
    *,
    path: Path,
    preview_window: Any | None,
    inspector_window: Any | None,
    previous: WorkspaceState,
) -> None:
    """window が構築済みの場合だけ workspace を atomic 保存する。"""

    if preview_window is None:
        return
    state = _workspace_state_from_windows(
        preview_window=preview_window,
        inspector_window=inspector_window,
        previous=previous,
    )
    if state is None:
        _logger.debug("WorkspaceState save skipped: window rect unavailable")
        return
    save_workspace_state(state, path)


def _apply_initial_window_layout(
    *,
    preview_window: Any,
    parameter_gui_window: Any,
    preferred_preview_position: tuple[int, int],
    preferred_parameter_gui_position: tuple[int, int],
) -> bool:
    """実windowへ安全な初期layoutを適用し、計算不能なら False を返す。"""

    preview_size = _window_content_size(preview_window)
    gui_size = _window_content_size(parameter_gui_window)
    if preview_size is None or gui_size is None:
        return False
    usable_bounds = _usable_screen_bounds(
        preview_window,
        preferred_preview_position,
    )
    if usable_bounds is None:
        return False

    preview_preferred_rect = WindowRect(
        int(preferred_preview_position[0]),
        int(preferred_preview_position[1]),
        int(preview_size[0]),
        int(preview_size[1]),
    )
    gui_preferred_rect = WindowRect(
        int(preferred_parameter_gui_position[0]),
        int(preferred_parameter_gui_position[1]),
        int(gui_size[0]),
        int(gui_size[1]),
    )
    gui_usable_bounds = _usable_screen_bounds(
        parameter_gui_window,
        preferred_parameter_gui_position,
    )
    preview_safe_bounds = _safe_explicit_layout_bounds(usable_bounds)
    gui_safe_bounds = (
        None if gui_usable_bounds is None else _safe_explicit_layout_bounds(gui_usable_bounds)
    )

    # 明示 config が既に安全なら、single / dual monitor を問わずユーザーの配置と
    # natural size をそのまま尊重する。現在の既定値のように overlap / overflow
    # している場合だけ、以下の single-screen responsive layout へ進む。
    if (
        preview_safe_bounds is not None
        and gui_safe_bounds is not None
        and _rect_is_inside(preview_preferred_rect, preview_safe_bounds)
        and _rect_is_inside(gui_preferred_rect, gui_safe_bounds)
        and not _rects_overlap(preview_preferred_rect, gui_preferred_rect)
    ):
        preview_window.set_location(*preferred_preview_position)
        parameter_gui_window.set_location(*preferred_parameter_gui_position)
        return True

    try:
        layout = layout_window_pair(
            preview_size=preview_size,
            parameter_gui_size=gui_size,
            usable_bounds=usable_bounds,
            preferred_preview_position=preferred_preview_position,
            preferred_parameter_gui_position=preferred_parameter_gui_position,
        )
    except (TypeError, ValueError, OverflowError):
        _logger.debug("Initial window layout could not be calculated", exc_info=True)
        return False

    gui_target_size = (layout.parameter_gui.width, layout.parameter_gui.height)

    _apply_preview_rect(preview_window, layout.preview)
    if gui_target_size != gui_size:
        parameter_gui_window.set_size(*gui_target_size)
    parameter_gui_window.set_location(
        layout.parameter_gui.x,
        layout.parameter_gui.y,
    )
    _logger.debug(
        "Applied %s initial window layout: preview=%s gui=%s bounds=%s",
        layout.orientation,
        layout.preview,
        layout.parameter_gui,
        usable_bounds,
    )
    return True


def _activate_initial_windows(preview_window: Any, parameter_gui_window: Any | None) -> None:
    """preview を前面化した後に GUI を最後に activate する。"""

    try:
        preview_window.activate()
    except Exception:
        pass
    try:
        if parameter_gui_window is not None and bool(parameter_gui_window.visible):
            parameter_gui_window.activate()
    except Exception:
        pass


def _set_inspector_visible(
    *,
    preview_window: Any,
    inspector_window: Any,
    visible: bool,
) -> None:
    """Inspector の表示を切り替え、操作先 window を前面化する。"""

    inspector_window.set_visible(bool(visible))
    if visible:
        inspector_window.activate()
    else:
        preview_window.activate()


def _install_inspector_visibility_shortcut(
    *,
    preview_window: Any,
    inspector_window: Any,
) -> None:
    """preview/Inspector の Cmd/Ctrl+I に Inspector toggle を配線する。"""

    shortcut_modifier_mask = int(key.MOD_CTRL) | int(key.MOD_COMMAND)

    def toggle_inspector(symbol: int | None, modifiers: int) -> object | None:
        if symbol is None or int(symbol) != int(key.I):
            return None
        if not int(modifiers) & shortcut_modifier_mask:
            return None

        _set_inspector_visible(
            preview_window=preview_window,
            inspector_window=inspector_window,
            visible=not bool(inspector_window.visible),
        )
        return pyglet.event.EVENT_HANDLED

    # Inspector が hide 中は preview、表示中はどちらに focus があっても
    # 同じ shortcut で戻せる。preview 自体の command surface は増やさない。
    preview_window.push_handlers(on_key_press=toggle_inspector)
    inspector_window.push_handlers(on_key_press=toggle_inspector)


class WorkspaceWindowController:
    """一 session の preview/Inspector window state と配置 policy を所有する。"""

    def __init__(
        self,
        *,
        path: Path,
        state: WorkspaceState,
        restored: bool,
        restore_preview_size: bool,
        preferred_preview_position: tuple[int, int],
        preferred_inspector_position: tuple[int, int],
    ) -> None:
        self._path = path
        self._state = state
        self._restored = bool(restored)
        if not isinstance(restore_preview_size, bool):
            raise TypeError("restore_preview_size は bool である必要があります")
        self._restore_preview_size = restore_preview_size
        self._preferred_preview_position = preferred_preview_position
        self._preferred_inspector_position = preferred_inspector_position
        self._preview_window: Any | None = None
        self._inspector_window: Any | None = None
        self._diagnostic: DiagnosticEvent | None = None

    @classmethod
    def load(
        cls,
        *,
        path: Path,
        preview_size: tuple[int, int],
        inspector_size: tuple[int, int],
        restore_preview_size: bool,
        preferred_preview_position: tuple[int, int],
        preferred_inspector_position: tuple[int, int],
    ) -> WorkspaceWindowController:
        """session config の fallback と保存済み workspace を一度だけ合成する。"""

        fallback = WorkspaceState(
            preview_rect=WindowRect(
                int(preferred_preview_position[0]),
                int(preferred_preview_position[1]),
                int(preview_size[0]),
                int(preview_size[1]),
            ),
            inspector_rect=WindowRect(
                int(preferred_inspector_position[0]),
                int(preferred_inspector_position[1]),
                int(inspector_size[0]),
                int(inspector_size[1]),
            ),
            inspector_visible=True,
            ui_scale=1.0,
        )
        result = load_workspace_state(path, fallback=fallback)
        controller = cls(
            path=path,
            state=result.state,
            restored=result.restored,
            restore_preview_size=restore_preview_size,
            preferred_preview_position=preferred_preview_position,
            preferred_inspector_position=preferred_inspector_position,
        )
        controller._diagnostic = result.diagnostic
        return controller

    @property
    def diagnostic(self) -> DiagnosticEvent | None:
        """workspace load 時の diagnostic を返す。"""

        return self._diagnostic

    @property
    def restored(self) -> bool:
        """保存済み workspace が採用されたか返す。"""

        return self._restored

    @property
    def ui_scale(self) -> float:
        """Inspector 初期化に使う保存済み UI scale を返す。"""

        return float(self._state.ui_scale)

    def attach_preview(self, window: Any) -> None:
        """preview window の所有境界を登録し、config の初期位置を適用する。"""

        self._preview_window = window
        window.set_location(*self._preferred_preview_position)

    def attach_inspector(self, window: Any) -> None:
        """Inspector window を登録する。"""

        self._inspector_window = window

    def apply_layout(self) -> bool:
        """restore または responsive fallback を現在 window 群へ適用する。"""

        preview = self._preview_window
        if preview is None:
            return False
        inspector = self._inspector_window
        applied = False
        if self._restored:
            applied = _apply_workspace_layout(
                preview_window=preview,
                inspector_window=inspector,
                state=self._state,
                restore_preview_size=self._restore_preview_size,
            )
        if not applied:
            if inspector is None:
                applied = _apply_initial_preview_layout(
                    preview_window=preview,
                    preferred_preview_position=self._preferred_preview_position,
                )
            else:
                applied = _apply_initial_window_layout(
                    preview_window=preview,
                    parameter_gui_window=inspector,
                    preferred_preview_position=self._preferred_preview_position,
                    preferred_parameter_gui_position=self._preferred_inspector_position,
                )
                if not applied:
                    inspector.set_location(*self._preferred_inspector_position)
        return applied

    def set_inspector_visible(self, visible: bool) -> None:
        """Inspector visibility と focus を一緒に更新する。"""

        preview = self._preview_window
        inspector = self._inspector_window
        if preview is None or inspector is None:
            return
        _set_inspector_visible(
            preview_window=preview,
            inspector_window=inspector,
            visible=visible,
        )

    def hide_inspector(self) -> None:
        """window close event を session 終了ではなく非表示へ変換する。"""

        self.set_inspector_visible(False)

    def install_visibility_shortcut(self) -> None:
        """preview/Inspector 双方へ visibility shortcut を配線する。"""

        preview = self._preview_window
        inspector = self._inspector_window
        if preview is None or inspector is None:
            return
        _install_inspector_visibility_shortcut(
            preview_window=preview,
            inspector_window=inspector,
        )

    def activate(self) -> None:
        """preview の後に可視 Inspector を前面化する。"""

        preview = self._preview_window
        if preview is None:
            return
        _activate_initial_windows(preview, self._inspector_window)

    def persist(self) -> None:
        """構築済み window の現在 state を atomic 保存する。"""

        _persist_workspace_state_on_shutdown(
            path=self._path,
            preview_window=self._preview_window,
            inspector_window=self._inspector_window,
            previous=self._state,
        )


__all__ = ["WorkspaceWindowController"]
