from __future__ import annotations

from typing import Any, cast

import pytest

from grafix.core.parameters import ParamStore
from grafix.core.operation_catalog import OperationCatalogBuilder
from grafix.core.preset_catalog import PresetCatalogBuilder
from grafix.core.runtime_config import RuntimeConfig
from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog
from grafix.interactive.parameter_gui import gui as gui_module
from grafix.interactive.parameter_gui.gui import ParameterGUI
from grafix.interactive.parameter_gui.pyglet_backend import PygletImguiBackend


class _FailingWindow:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def switch_to(self) -> None:
        self._calls.append("switch_to")
        raise RuntimeError("switch failed")

    def close(self) -> None:
        self._calls.append("window.close")
        raise RuntimeError("window close failed")


class _FailingRenderer:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def shutdown(self) -> None:
        self._calls.append("renderer.shutdown")
        raise RuntimeError("renderer shutdown failed")


class _FailingImGui:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def destroy_context(self, context: object) -> None:
        assert context == "owned-context"
        self._calls.append("imgui.destroy_context")
        raise RuntimeError("context destroy failed")


class _FailingBackend:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def close(self) -> None:
        self._calls.append("backend.close")
        raise RuntimeError("backend close failed")


class _FakeWindow:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def switch_to(self) -> None:
        self._calls.append("switch_to")

    def close(self) -> None:
        self._calls.append("window.close")


class _FakeBackend:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def close(self) -> None:
        self._calls.append("backend.close")


def test_parameter_gui_owns_injected_catalog_without_global_lookup(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> None:
    catalog = ParameterGuiCatalog.capture(
        OperationCatalogBuilder().freeze(),
        PresetCatalogBuilder().freeze(),
    )

    def fail_global_lookup() -> None:
        raise AssertionError("explicit session catalog must be used")

    def initialize(self: ParameterGUI, *_args: object, **_kwargs: object) -> None:
        assert self._catalog is catalog
        self._closed = True

    monkeypatch.setattr(gui_module, "current_parameter_gui_catalog", fail_global_lookup)
    monkeypatch.setattr(ParameterGUI, "_initialize", initialize)

    instance = ParameterGUI(
        _FailingWindow([]),
        effective_config=effective_runtime_config,
        store=ParamStore(),
        catalog=catalog,
    )

    assert instance._catalog is catalog


def test_backend_close_switches_to_owned_context_and_attempts_every_cleanup_step(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    backend = cast(Any, object.__new__(PygletImguiBackend))
    backend._closed = False
    backend._window = _FailingWindow(calls)
    backend._renderer = _FailingRenderer(calls)
    backend._imgui = _FailingImGui(calls)
    backend._context = "owned-context"

    with pytest.raises(RuntimeError, match="switch failed"):
        backend.close()

    assert calls == [
        "switch_to",
        "imgui.destroy_context",
    ]

    # 最初の close が例外を返しても、二重解放はしない。
    backend.close()
    assert len(calls) == 2
    assert caplog.records == []


def test_partial_initialization_cleans_resources_and_preserves_root_error(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> None:
    calls: list[str] = []
    window = _FailingWindow(calls)

    def fail_initialize(self: ParameterGUI, *_args: object, **_kwargs: object) -> None:
        gui_state = cast(Any, self)
        gui_state._backend = _FailingBackend(calls)
        raise ValueError("font initialization failed")

    monkeypatch.setattr(ParameterGUI, "_initialize", fail_initialize)

    with pytest.raises(ValueError, match="font initialization failed"):
        ParameterGUI(
            window,
            effective_config=effective_runtime_config,
            store=ParamStore(),
        )

    assert calls == [
        "backend.close",
        "switch_to",
        "window.close",
    ]


def test_parameter_gui_close_attempts_backend_and_window_cleanup(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    calls: list[str] = []
    gui = cast(Any, initialized_parameter_gui)
    gui._closed = False
    gui._window = _FailingWindow(calls)
    gui._backend = _FailingBackend(calls)

    with pytest.raises(RuntimeError, match="backend close failed"):
        gui.close()

    assert calls == ["backend.close", "switch_to", "window.close"]


def test_widget_state_is_released_across_fake_gui_close_and_reopen(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> None:
    calls: list[str] = []

    def initialize(
        self: ParameterGUI,
        window: _FakeWindow,
        *,
        store: ParamStore,
        **_kwargs: object,
    ) -> None:
        self._window = window
        self._store = store
        self._session = gui_module.ParameterGuiSessionState.for_store(
            store,
            catalog=self._catalog,
        )
        self._backend = _FakeBackend(calls)
        self._closed = False

    monkeypatch.setattr(ParameterGUI, "_initialize", initialize)
    key = ("text", "same-site", "font")

    first = ParameterGUI(
        _FakeWindow(calls),
        effective_config=effective_runtime_config,
        store=ParamStore(),
    )
    first._session.widgets.font_filter_by_key[key] = "old filter"
    first._session.widgets.font_choices = (
        ("Old", "Nested/Old.ttf", False, "old"),
    )
    first._session.widgets.snippet_popup_text = "old snippet"
    first.close()

    assert first._session.widgets.font_filter_by_key == {}
    assert first._session.widgets.font_choices is None
    assert first._session.widgets.snippet_popup_text == ""

    reopened = ParameterGUI(
        _FakeWindow(calls),
        effective_config=effective_runtime_config,
        store=ParamStore(),
    )
    assert reopened._session.widgets.font_filter_by_key == {}
    assert reopened._session.widgets.font_choices is None
    assert reopened._session.widgets.snippet_popup_text == ""
    reopened.close()
    assert calls == [
        "backend.close",
        "switch_to",
        "window.close",
        "backend.close",
        "switch_to",
        "window.close",
    ]


def test_catalog_lookup_failure_closes_owned_window_and_preserves_root_error(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> None:
    calls: list[str] = []
    window = _FailingWindow(calls)

    def fail_global_lookup() -> ParameterGuiCatalog:
        raise LookupError("catalog lookup failed")

    monkeypatch.setattr(gui_module, "current_parameter_gui_catalog", fail_global_lookup)

    with pytest.raises(LookupError, match="catalog lookup failed"):
        ParameterGUI(
            window,
            effective_config=effective_runtime_config,
            store=ParamStore(),
        )

    assert calls == ["switch_to", "window.close"]


@pytest.mark.parametrize(
    ("kwargs", "error", "match"),
    [
        ({"transport_fps": "60"}, TypeError, "transport_fps"),
        ({"transport_fps": 0.0}, ValueError, "transport_fps"),
        ({"ui_scale": True}, TypeError, "ui_scale"),
        ({"ui_scale": float("nan")}, ValueError, "ui_scale"),
        ({"title": 1}, TypeError, "title"),
        ({"title": ""}, ValueError, "title"),
    ],
)
def test_parameter_gui_rejects_noncanonical_scalar_configuration_before_init(
    effective_runtime_config: RuntimeConfig,
    kwargs: dict[str, object],
    error: type[Exception],
    match: str,
) -> None:
    calls: list[str] = []
    window = _FailingWindow(calls)
    with pytest.raises(error, match=match):
        ParameterGUI(
            window,
            effective_config=effective_runtime_config,
            store=ParamStore(),
            **kwargs,  # type: ignore[arg-type]
        )

    assert calls == ["switch_to", "window.close"]
