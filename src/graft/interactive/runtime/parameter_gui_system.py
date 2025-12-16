# どこで: `src/graft/interactive/runtime/parameter_gui_system.py`。
# 何を: Parameter GUI を「1フレーム描画できるサブシステム」として提供する。
# なぜ: `src/graft/api/run.py` の `run()` から GUI 初期化/描画/後始末を分離し、肥大化を防ぐため。

from __future__ import annotations

from graft.interactive.parameter_gui import ParameterGUI, create_parameter_gui_window
from graft.core.parameters import ParamStore


class ParameterGUIWindowSystem:
    """Parameter GUI（別ウィンドウ）のサブシステム。"""

    def __init__(self, *, store: ParamStore) -> None:
        """GUI 用の window と ParameterGUI を初期化する。"""

        self.window = create_parameter_gui_window()
        self._gui = ParameterGUI(self.window, store=store)

    def draw_frame(self) -> None:
        """1 フレーム分の GUI を描画する（`flip()` は呼ばない）。"""

        self._gui.draw_frame()

    def close(self) -> None:
        """GUI を終了し、ウィンドウを破棄する。"""

        self._gui.close()
