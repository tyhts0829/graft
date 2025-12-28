# どこで: `src/grafix/interactive/runtime/parameter_gui_system.py`。
# 何を: Parameter GUI を「1フレーム描画できるサブシステム」として提供する。
# なぜ: `src/grafix/api/runner.py` の `run()` から GUI 初期化/描画/後始末を分離し、肥大化を防ぐため。

from __future__ import annotations

from typing import TYPE_CHECKING

from grafix.interactive.parameter_gui import ParameterGUI, create_parameter_gui_window
from grafix.core.parameters import ParamStore
from grafix.core.runtime_config import runtime_config
from grafix.interactive.midi import MidiController

if TYPE_CHECKING:
    from grafix.interactive.runtime.monitor import RuntimeMonitor


class ParameterGUIWindowSystem:
    """Parameter GUI（別ウィンドウ）のサブシステム。"""

    def __init__(
        self,
        *,
        store: ParamStore,
        midi_controller: MidiController | None = None,
        monitor: RuntimeMonitor | None = None,
    ) -> None:
        """GUI 用の window と ParameterGUI を初期化する。"""

        cfg = runtime_config()
        w, h = cfg.parameter_gui_window_size
        self.window = create_parameter_gui_window(width=w, height=h, vsync=False)
        self._gui = ParameterGUI(
            self.window,
            store=store,
            midi_controller=midi_controller,
            monitor=monitor,
        )

    def draw_frame(self) -> None:
        """1 フレーム分の GUI を描画する（`flip()` は呼ばない）。"""

        self._gui.draw_frame()

    def close(self) -> None:
        """GUI を終了し、ウィンドウを破棄する。"""

        self._gui.close()
