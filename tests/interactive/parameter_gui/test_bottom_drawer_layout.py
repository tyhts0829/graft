from __future__ import annotations

import sys
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from grafix.core.parameters import ParamStore
from grafix.interactive.parameter_gui import gui as gui_module
from grafix.interactive.parameter_gui.midi_learn import MidiLearnState
from grafix.interactive.parameter_gui.parameter_filter import ParameterFilterState
from grafix.interactive.parameter_gui.table_view import (
    parameter_table_view_for_store,
)
from grafix.interactive.parameter_gui.theme import apply_parameter_gui_theme


def test_dynamic_drawer_content_does_not_move_or_narrow_parameter_table(
    monkeypatch: pytest.MonkeyPatch,
    initialized_parameter_gui: gui_module.ParameterGUI,
) -> None:
    """Help/telemetry の行数が変わっても root scrollbar と table rect は不変。"""

    imgui = pytest.importorskip("imgui")
    parameter_gui = cast(Any, initialized_parameter_gui)
    backend = parameter_gui._backend
    context = backend._context
    owned_window = backend._window
    owned_renderer = backend._renderer
    imgui.set_current_context(context)
    try:
        io = imgui.get_io()
        io.display_size = (1100.0, 1000.0)
        io.fonts.get_tex_data_as_rgba32()
        apply_parameter_gui_theme(imgui)

        window = SimpleNamespace(
            width=1100,
            height=1000,
            scale=1.0,
            get_framebuffer_size=lambda: (1100, 1000),
            clear=lambda: None,
        )
        renderer = SimpleNamespace(render=lambda _draw_data: None)
        quiet = SimpleNamespace(profiler=None, diagnostics=(), alert_count=0)
        noisy = SimpleNamespace(
            profiler=object(),
            diagnostics=tuple(range(40)),
            alert_count=20,
        )
        monitor = SimpleNamespace(
            current=quiet,
            diagnostic_center=object(),
        )
        monitor.snapshot = lambda: monitor.current

        parameter_gui._closed = False
        parameter_gui._prev_time = time.monotonic()
        parameter_gui._imgui = imgui
        parameter_gui._custom_font_path = None
        parameter_gui._window = window
        backend._window = window
        backend._renderer = renderer
        parameter_gui._monitor = monitor
        parameter_gui._transport = None
        parameter_gui._history = None
        parameter_gui._snapshot_slots = None
        parameter_gui._midi_session = None
        parameter_gui._store = ParamStore()
        parameter_gui._session.filter_state = ParameterFilterState()
        parameter_gui._session.error_keys = frozenset()
        parameter_gui._session.favorite_keys = frozenset()
        parameter_gui._session.table_view = None
        parameter_gui._session.help_row = None
        parameter_gui._session.midi_learn = MidiLearnState()
        parameter_gui._session.show_inactive_parameters = False
        parameter_gui._title = "Parameters"
        parameter_gui._ui_scale = 1.0

        root_content_widths: list[float] = []
        table_rects: list[tuple[float, float, float, float]] = []
        root_scroll_maxima: list[float] = []

        def render_toolbar_area(
            *,
            content_width: float,
            monitor_snapshot: object,
        ) -> bool:
            _ = monitor_snapshot
            root_content_widths.append(float(content_width))
            return False

        parameter_gui._render_toolbar_area = render_toolbar_area
        parameter_gui._render_midi_clear_notice = lambda: False
        def render_parameter_table_toolbar() -> bool:
            parameter_gui._session.table_view = parameter_table_view_for_store(
                parameter_gui._store,
                cache=parameter_gui._session.table_cache,
                show_inactive_params=False,
            )
            return False

        parameter_gui._render_parameter_table_toolbar = render_parameter_table_toolbar
        parameter_gui._render_reconcile_orphan_control = lambda: False
        parameter_gui._maybe_preview_range_edit_by_midi = lambda: None
        parameter_gui._render_range_edit_mode = lambda: False

        def render_table(*_args: object, **_kwargs: object) -> object:
            position = imgui.get_window_position()
            table_rects.append(
                (
                    float(position.x),
                    float(position.y),
                    float(imgui.get_window_width()),
                    float(imgui.get_window_height()),
                )
            )
            return SimpleNamespace(
                changed=False,
                midi_learn_state=parameter_gui._session.midi_learn,
            )

        monkeypatch.setattr(gui_module, "render_store_parameter_table", render_table)
        monkeypatch.setattr(
            gui_module,
            "monitor_alert_lines",
            lambda snapshot: tuple(
                f"alert {index}" for index in range(int(snapshot.alert_count))
            ),
        )

        def render_alerts(target: Any, snapshot: Any) -> None:
            for index in range(int(snapshot.alert_count)):
                target.text(f"alert {index}")

        def render_profiler(target: Any, profiler: object | None) -> None:
            if profiler is not None:
                for index in range(50):
                    target.text(f"profile {index}")

        def render_diagnostics(
            target: Any,
            diagnostics: tuple[int, ...],
            *,
            center: object,
        ) -> None:
            _ = center
            for index in diagnostics:
                target.text(f"diagnostic {index}")

        def render_help(target: Any, row: object | None) -> None:
            target.text_disabled("HELP")
            if row is not None:
                for index in range(50):
                    target.text_wrapped(f"long description {index}")

        monkeypatch.setattr(gui_module, "render_monitor_alerts", render_alerts)
        monkeypatch.setattr(gui_module, "render_profiler_panel", render_profiler)
        monkeypatch.setattr(gui_module, "render_diagnostics_panel", render_diagnostics)
        monkeypatch.setattr(gui_module, "render_parameter_help_pane", render_help)

        original_drawer = parameter_gui._render_bottom_drawer

        def render_observed_drawer(**kwargs: object) -> None:
            original_drawer(**kwargs)
            root_scroll_maxima.append(float(imgui.get_scroll_max_y()))

        parameter_gui._render_bottom_drawer = render_observed_drawer
        monkeypatch.setitem(
            sys.modules,
            "pyglet",
            SimpleNamespace(
                gl=SimpleNamespace(glClearColor=lambda *_args: None),
            ),
        )

        for snapshot, help_row in (
            (quiet, None),
            (noisy, object()),
            (quiet, None),
        ):
            monitor.current = snapshot
            parameter_gui._session.help_row = help_row
            parameter_gui.draw_frame()

        assert root_content_widths == pytest.approx(
            [root_content_widths[0]] * len(root_content_widths)
        )
        assert table_rects[0] == pytest.approx(table_rects[1])
        assert table_rects[0] == pytest.approx(table_rects[2])
        assert root_scroll_maxima == pytest.approx([0.0, 0.0, 0.0])
    finally:
        imgui.set_current_context(context)
        backend._window = owned_window
        backend._renderer = owned_renderer
