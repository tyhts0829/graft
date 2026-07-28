from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from typing import Any, cast

import pyglet
import pytest

from grafix.core.parameters import ParamStore
from grafix.interactive.gl import draw_renderer as draw_renderer_module
from grafix.interactive.gl.draw_renderer import DrawRenderer, _aspect_fit_viewport
from grafix.interactive.parameter_gui import gui as gui_module
from grafix.interactive.parameter_gui import pyglet_backend
from grafix.interactive.parameter_gui.midi_learn import MidiLearnState
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.table_view import (
    parameter_table_view_for_store,
)
from grafix.api.render import RenderOptions


class _FakeWindow:
    def __init__(self, *, width: int, height: int, scale: float = 2.0) -> None:
        self.width = int(width)
        self.height = int(height)
        self.scale = float(scale)
        self.set_size_calls: list[tuple[int, int]] = []
        self.clear_calls = 0

    def get_framebuffer_size(self) -> tuple[int, int]:
        return int(self.width * self.scale), int(self.height * self.scale)

    def set_size(self, width: int, height: int) -> None:
        self.set_size_calls.append((int(width), int(height)))
        self.width = int(width)
        self.height = int(height)

    def clear(self) -> None:
        self.clear_calls += 1


class _FakeImGui:
    WINDOW_NO_RESIZE = 1
    WINDOW_NO_COLLAPSE = 2
    WINDOW_NO_TITLE_BAR = 4

    def __init__(self) -> None:
        self.io = SimpleNamespace(mouse_wheel=0.0)
        self.next_window_sizes: list[tuple[int, int]] = []

    def get_io(self) -> Any:
        return self.io

    def get_style(self) -> Any:
        return SimpleNamespace(item_spacing=(0.0, 0.0))

    def set_current_context(self, _context: object) -> None:
        pass

    def new_frame(self) -> None:
        pass

    def set_next_window_position(self, _x: int, _y: int) -> None:
        pass

    def set_next_window_size(self, width: int, height: int) -> None:
        self.next_window_sizes.append((int(width), int(height)))

    def begin(self, _title: str, *, flags: int) -> None:
        assert flags == 7

    def get_content_region_available_width(self) -> float:
        return 768.0

    def button(self, _label: str) -> bool:
        return False

    def same_line(self) -> None:
        pass

    def checkbox(self, _label: str, value: bool) -> tuple[bool, bool]:
        return False, bool(value)

    def text_disabled(self, _text: str) -> None:
        pass

    def separator(self) -> None:
        pass

    def begin_child(
        self,
        _name: str,
        _width: int,
        _height: int,
        *,
        border: bool,
    ) -> None:
        assert border is False

    def end_child(self) -> None:
        pass

    def end(self) -> None:
        pass

    def is_any_item_active(self) -> bool:
        return False

    def render(self) -> None:
        pass

    def get_draw_data(self) -> object:
        return object()


class _FakeImGuiRenderer:
    def __init__(self) -> None:
        self.render_calls = 0

    def render(self, _draw_data: object) -> None:
        self.render_calls += 1


def test_parameter_gui_draw_keeps_requested_logical_width_on_retina(
    monkeypatch: pytest.MonkeyPatch,
    initialized_parameter_gui: gui_module.ParameterGUI,
) -> None:
    window = _FakeWindow(width=800, height=1000, scale=2.0)
    imgui = _FakeImGui()
    renderer = _FakeImGuiRenderer()
    gui = cast(Any, initialized_parameter_gui)
    gui._closed = False
    gui._prev_time = time.monotonic()
    gui._imgui = cast(Any, imgui)
    gui._custom_font_path = None
    gui._window = window
    gui._backend = SimpleNamespace(
        activate_context=lambda: None,
        begin_frame=lambda _dt: imgui.new_frame(),
        render=lambda: (
            imgui.render(),
            window.clear(),
            renderer.render(imgui.get_draw_data()),
        ),
    )
    gui._monitor = None
    gui._transport = None
    gui._history = None
    gui._snapshot_slots = None
    gui._midi_session = None
    gui._store = ParamStore()
    gui._session.filter_state = ParameterFilterState()
    gui._session.error_keys = frozenset()
    gui._session.favorite_keys = frozenset()
    gui._session.table_view = None
    gui._session.midi_learn = MidiLearnState()
    gui._session.show_inactive_parameters = False
    gui._title = "Parameters"

    monkeypatch.setattr(
        gui_module,
        "render_store_parameter_table",
        lambda *_a, **_k: SimpleNamespace(
            changed=False,
            midi_learn_state=gui._session.midi_learn,
        ),
    )
    monkeypatch.setattr(gui, "_render_toolbar_area", lambda **_kwargs: False)
    monkeypatch.setattr(gui, "_render_midi_clear_notice", lambda: False)

    def render_parameter_table_toolbar() -> bool:
        gui._session.table_view = parameter_table_view_for_store(
            gui._store,
            cache=gui._session.table_cache,
            show_inactive_params=False,
        )
        return False

    monkeypatch.setattr(
        gui,
        "_render_parameter_table_toolbar",
        render_parameter_table_toolbar,
    )
    monkeypatch.setattr(gui, "_render_reconcile_orphan_control", lambda: False)
    monkeypatch.setattr(gui, "_maybe_preview_range_edit_by_midi", lambda: None)
    monkeypatch.setattr(gui, "_render_range_edit_mode", lambda: False)
    monkeypatch.setattr(gui, "_render_bottom_drawer", lambda **_kwargs: None)
    fake_pyglet = SimpleNamespace(
        gl=SimpleNamespace(glClearColor=lambda *_args: None),
    )
    monkeypatch.setitem(sys.modules, "pyglet", fake_pyglet)

    gui.draw_frame()

    assert window.get_framebuffer_size() == (1600, 2000)
    assert window.set_size_calls == []
    assert imgui.next_window_sizes == [(800, 1000)]
    assert renderer.render_calls == 1
    assert window.clear_calls == 1


def test_parameter_gui_window_is_resizable_and_keeps_requested_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CreatedWindow:
        def set_minimum_size(self, width: int, height: int) -> None:
            captured["minimum_size"] = (int(width), int(height))

    sentinel = CreatedWindow()

    def config(**kwargs: object) -> object:
        captured["config"] = dict(kwargs)
        return "gl-config"

    def window(**kwargs: object) -> object:
        captured["window"] = dict(kwargs)
        return sentinel

    fake_pyglet = SimpleNamespace(
        gl=SimpleNamespace(Config=config),
        window=SimpleNamespace(Window=window),
    )
    monkeypatch.setitem(sys.modules, "pyglet", fake_pyglet)

    result = pyglet_backend.create_parameter_gui_window(width=840, height=720)

    assert result is sentinel
    assert captured["window"] == {
        "width": 840,
        "height": 720,
        "caption": "Grafix Inspector",
        "resizable": True,
        "vsync": False,
        "config": "gl-config",
    }
    assert captured["minimum_size"] == (760, 480)


def test_parameter_gui_window_factory_closes_window_when_minimum_size_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[object] = []

    class CreatedWindow:
        def set_minimum_size(self, _width: int, _height: int) -> None:
            raise RuntimeError("minimum size failed")

    sentinel = CreatedWindow()
    fake_pyglet = SimpleNamespace(
        gl=SimpleNamespace(Config=lambda **_kwargs: object()),
        window=SimpleNamespace(Window=lambda **_kwargs: sentinel),
    )
    monkeypatch.setitem(sys.modules, "pyglet", fake_pyglet)
    monkeypatch.setattr(
        pyglet_backend,
        "close_pyglet_window",
        lambda window: closed.append(window),
    )

    with pytest.raises(RuntimeError, match="minimum size failed"):
        pyglet_backend.create_parameter_gui_window(width=840, height=720)

    assert closed == [sentinel]


def test_draw_window_is_resizable(monkeypatch: pytest.MonkeyPatch) -> None:
    # pyglet.gl import 時の shadow context を headless test で作らない。
    pyglet.options["shadow_window"] = False
    from grafix.interactive import draw_window as draw_window_module

    captured: dict[str, object] = {}

    class CreatedWindow:
        def set_minimum_size(self, width: int, height: int) -> None:
            captured["minimum_size"] = (int(width), int(height))

    sentinel = CreatedWindow()

    def config(**kwargs: object) -> object:
        captured["config"] = dict(kwargs)
        return "gl-config"

    def window(**kwargs: object) -> object:
        captured["window"] = dict(kwargs)
        return sentinel

    monkeypatch.setattr(draw_window_module, "Config", config)
    monkeypatch.setattr(
        draw_window_module,
        "pyglet",
        SimpleNamespace(window=SimpleNamespace(Window=window)),
    )

    result = draw_window_module.create_draw_window(
        RenderOptions(canvas_size=(320, 240)),
        render_scale=1.5,
    )

    assert result is sentinel
    assert captured["window"] == {
        "width": 480,
        "height": 360,
        "resizable": True,
        "caption": "Grafix",
        "config": "gl-config",
    }
    assert captured["minimum_size"] == (320, 320)


def test_draw_window_minimum_does_not_exceed_initial_natural_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyglet.options["shadow_window"] = False
    from grafix.interactive import draw_window as draw_window_module

    captured: dict[str, object] = {}

    class CreatedWindow:
        def set_minimum_size(self, width: int, height: int) -> None:
            captured["minimum_size"] = (int(width), int(height))

    sentinel = CreatedWindow()
    config = object()

    def window(**kwargs: object) -> CreatedWindow:
        captured["window"] = dict(kwargs)
        return sentinel

    monkeypatch.setattr(draw_window_module, "Config", lambda **_kwargs: config)
    monkeypatch.setattr(
        draw_window_module,
        "pyglet",
        SimpleNamespace(window=SimpleNamespace(Window=window)),
    )

    result = draw_window_module.create_draw_window(
        RenderOptions(canvas_size=(100, 1000)),
        render_scale=1.0,
    )

    assert result is sentinel
    assert captured["window"] == {
        "width": 100,
        "height": 1000,
        "resizable": True,
        "caption": "Grafix",
        "config": config,
    }
    assert captured["minimum_size"] == (100, 320)


def test_draw_window_factory_closes_window_when_minimum_size_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pyglet.gl import 時の shadow context を headless test で作らない。
    pyglet.options["shadow_window"] = False
    from grafix.interactive import draw_window as draw_window_module

    closed: list[object] = []

    class CreatedWindow:
        def set_minimum_size(self, _width: int, _height: int) -> None:
            raise RuntimeError("minimum size failed")

    sentinel = CreatedWindow()
    monkeypatch.setattr(draw_window_module, "Config", lambda **_kwargs: object())
    monkeypatch.setattr(
        draw_window_module,
        "pyglet",
        SimpleNamespace(window=SimpleNamespace(Window=lambda **_kwargs: sentinel)),
    )
    monkeypatch.setattr(
        draw_window_module,
        "close_pyglet_window",
        lambda window: closed.append(window),
    )

    with pytest.raises(RuntimeError, match="minimum size failed"):
        draw_window_module.create_draw_window(
            RenderOptions(canvas_size=(320, 240)),
            render_scale=1.5,
        )

    assert closed == [sentinel]


@pytest.mark.parametrize(
    ("framebuffer_size", "canvas_size", "expected"),
    [
        ((1200, 800), (800, 800), (200, 0, 800, 800)),
        ((800, 800), (800, 400), (0, 200, 800, 400)),
        ((1600, 1200), (800, 600), (0, 0, 1600, 1200)),
        ((0, 0), (800, 600), (0, 0, 1, 1)),
    ],
)
def test_aspect_fit_viewport_centers_canvas_without_distortion(
    framebuffer_size: tuple[int, int],
    canvas_size: tuple[int, int],
    expected: tuple[int, int, int, int],
) -> None:
    assert _aspect_fit_viewport(framebuffer_size, canvas_size) == expected


def test_draw_renderer_uses_aspect_fit_size_for_viewport_and_line_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Context:
        def __init__(self) -> None:
            self.viewport: tuple[int, int, int, int] | None = None
            self.clear_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def clear(self, *args: object, **kwargs: object) -> None:
            self.clear_calls.append((args, dict(kwargs)))

    class Mesh:
        def __init__(self, _ctx: object, _program: object) -> None:
            return None

    class Window:
        def switch_to(self) -> None:
            return None

    uniform = SimpleNamespace(value=(1.0, 1.0))
    program = {
        "viewport_size": uniform,
        "line_width_px": SimpleNamespace(value=0.0),
        "color": SimpleNamespace(value=(0.0, 0.0, 0.0, 1.0)),
        "projection": SimpleNamespace(write=lambda _value: None),
    }
    context = Context()
    monkeypatch.setattr(
        draw_renderer_module.moderngl,
        "create_context",
        lambda **_kwargs: context,
    )
    monkeypatch.setattr(
        draw_renderer_module.Shader,
        "create_shader",
        lambda _context: program,
    )
    monkeypatch.setattr(draw_renderer_module, "LineMesh", Mesh)
    renderer = DrawRenderer(
        cast(Any, Window()),
        RenderOptions(canvas_size=(800, 800)),
    )

    renderer.viewport(1200, 800)

    assert context.viewport == (200, 0, 800, 800)
    assert renderer._framebuffer_size == (1200, 800)
    assert renderer._viewport_size == (800, 800)
    assert uniform.value == (800.0, 800.0)

    renderer.clear((0.25, 0.5, 0.75))
    assert context.clear_calls == [
        (
            (0.25, 0.5, 0.75, 1.0),
            {"viewport": (0, 0, 1200, 800)},
        )
    ]
    assert context.viewport == (200, 0, 800, 800)
