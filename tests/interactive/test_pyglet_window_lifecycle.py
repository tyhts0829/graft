from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from grafix.interactive import pyglet_window_lifecycle as lifecycle

pytestmark = pytest.mark.integration


class _Resource:
    def __init__(
        self,
        name: str,
        trace: list[str],
        *,
        error: BaseException | None = None,
    ) -> None:
        self.id: int | None = 1
        self._name = name
        self._trace = trace
        self._error = error

    def delete(self) -> None:
        self._trace.append(self._name)
        self.id = None
        if self._error is not None:
            raise self._error


class _TextView:
    def __init__(self, trace: list[str]) -> None:
        object.__setattr__(self, "_trace", trace)
        object.__setattr__(self, "_window", object())

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_window" and value is None:
            self._trace.append("detach")
        object.__setattr__(self, name, value)


class _Window:
    def __init__(
        self,
        trace: list[str],
        *,
        switch_error: BaseException | None = None,
        buffer_error: BaseException | None = None,
        program_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._trace = trace
        self._switch_error = switch_error
        self._close_error = close_error
        self.ubo = SimpleNamespace(
            buffer=_Resource("buffer", trace, error=buffer_error)
        )
        self._default_program = _Resource(
            "program",
            trace,
            error=program_error,
        )
        self._nsview = SimpleNamespace(_textview=_TextView(trace))

    def switch_to(self) -> None:
        self._trace.append("switch")
        if self._switch_error is not None:
            raise self._switch_error

    def close(self) -> None:
        self._trace.append("close")
        if self._close_error is not None:
            raise self._close_error


def test_close_releases_default_resources_and_cocoa_back_reference_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    window = _Window(trace)
    text_view = window._nsview._textview
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")

    lifecycle.close_pyglet_window(window)

    assert trace == ["switch", "buffer", "program", "detach", "close"]
    assert window.ubo is None
    assert window._default_program is None
    assert text_view._window is None


def test_close_does_not_touch_cocoa_private_state_off_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    window = _Window(trace)
    text_view = window._nsview._textview
    original_window = text_view._window
    monkeypatch.setattr(lifecycle.sys, "platform", "linux")

    lifecycle.close_pyglet_window(window)

    assert trace == ["switch", "buffer", "program", "close"]
    assert text_view._window is original_window


def test_switch_failure_skips_raw_gl_delete_but_still_detaches_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    root_error = RuntimeError("switch failed")
    window = _Window(trace, switch_error=root_error)
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")

    with pytest.raises(RuntimeError, match="switch failed") as captured:
        lifecycle.close_pyglet_window(window)

    assert captured.value is root_error
    assert trace == ["switch", "detach", "close"]
    assert window.ubo is None
    assert window._default_program is None


def test_closed_context_noop_switch_never_deletes_gl_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    window = _Window(trace)
    window.context = None
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")

    lifecycle.close_pyglet_window(window)

    assert trace == ["detach", "close"]
    assert window.ubo is None
    assert window._default_program is None


def test_delete_failure_preserves_first_error_and_runs_remaining_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    root_error = RuntimeError("buffer failed")
    window = _Window(
        trace,
        buffer_error=root_error,
        program_error=RuntimeError("program failed"),
        close_error=RuntimeError("close failed"),
    )
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")

    with pytest.raises(RuntimeError, match="buffer failed") as captured:
        lifecycle.close_pyglet_window(window)

    assert captured.value is root_error
    assert trace == ["switch", "buffer", "program", "detach", "close"]
    assert window.ubo is None
    assert window._default_program is None


def test_missing_pyglet_private_resources_still_closes() -> None:
    trace: list[str] = []
    window = SimpleNamespace(
        switch_to=lambda: trace.append("switch"),
        close=lambda: trace.append("close"),
    )

    lifecycle.close_pyglet_window(window)

    assert trace == ["switch", "close"]


def test_already_deleted_resources_are_not_deleted_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[str] = []
    window = _Window(trace)
    window.ubo.buffer.id = None
    window._default_program.id = None
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")

    lifecycle.close_pyglet_window(window)

    assert trace == ["switch", "detach", "close"]


def test_close_removes_only_the_owned_dynamic_ubo_pointer_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View(ctypes.Structure):
        _fields_ = [("projection", ctypes.c_float * 16)]

    trace: list[str] = []
    view = View()
    view_pointer = ctypes.pointer(view)
    pointer_type = type(view_pointer)
    window = _Window(trace)
    window.ubo.view = view
    window.ubo._view_ptr = view_pointer
    pointer_cache = lifecycle.ctypes._pointer_type_cache
    assert pointer_cache.get(View) is pointer_type
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")

    lifecycle.close_pyglet_window(window)

    assert View not in pointer_cache


def test_closed_context_also_drops_dynamic_ubo_pointer_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class View(ctypes.Structure):
        _fields_ = [("projection", ctypes.c_float * 16)]

    trace: list[str] = []
    view = View()
    view_pointer = ctypes.pointer(view)
    window = _Window(trace)
    window.context = None
    window.ubo.view = view
    window.ubo._view_ptr = view_pointer
    pointer_cache = lifecycle.ctypes._pointer_type_cache
    assert View in pointer_cache
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")

    lifecycle.close_pyglet_window(window)

    assert View not in pointer_cache
