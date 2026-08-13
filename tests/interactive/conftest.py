"""
Purpose:
    interactive GUIテストへ独立したRuntimeConfig、catalog cache、headless ParameterGUIを供給する。
Use when:
    GL windowを開かずにParameterGUIのconstructor後の挙動とcleanupを検証するとき。
Constraints:
    - 各fixtureでmutable store/cacheを共有せず、test間stateを隔離する。
    - GUIは通常constructorを通し、renderer/fontの外部resourceだけをheadless fakeへ差し替える。
    - teardownではtest中のfake差し替えを外し、constructorが所有した元resourceをproduction closeで解放する。
Side effects:
    pytest monkeypatchでpyglet optionとGUI resource factoryを一時的に差し替える。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
import pyglet

from grafix.core.parameters import ParamStore
from grafix.core.runtime_config import RuntimeConfig
from grafix.runtime_config_loader import runtime_config
from grafix.interactive.parameter_gui import gui as gui_module
from grafix.interactive.parameter_gui.catalog import current_parameter_gui_catalog
from grafix.interactive.parameter_gui.gui import ParameterGUI
from grafix.interactive.parameter_gui.table_view import ParameterTableViewCache


class _ParameterGuiWindow:
    """ParameterGUI の constructor/cleanup 契約を満たす headless window。"""

    scale = 1.0

    def push_handlers(self, **_handlers: Any) -> None:
        pass

    def switch_to(self) -> None:
        pass

    def close(self) -> None:
        pass


class _ParameterGuiRenderer:
    """初期化テスト以外で GL resource を作らない renderer。"""

    def shutdown(self) -> None:
        pass


@pytest.fixture
def effective_runtime_config() -> RuntimeConfig:
    """interactive test が明示注入する完全な実行時設定を返す。"""

    return runtime_config()


@pytest.fixture
def parameter_table_cache() -> ParameterTableViewCache:
    """各 test が明示注入する独立 table cache を返す。"""

    return ParameterTableViewCache(current_parameter_gui_catalog())


@pytest.fixture
def initialized_parameter_gui(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> Iterator[ParameterGUI]:
    """通常の constructor を通した、headless な ParameterGUI を返す。"""

    monkeypatch.setitem(cast(Any, pyglet.options), "shadow_window", False)

    renderer = _ParameterGuiRenderer()
    with monkeypatch.context() as initialization_patch:
        initialization_patch.setattr(
            gui_module.PygletImguiBackend,
            "_create_renderer",
            staticmethod(lambda _window: renderer),
        )
        initialization_patch.setattr(
            ParameterGUI,
            "_sync_font_for_window",
            lambda _self: None,
        )
        gui = ParameterGUI(
            _ParameterGuiWindow(),
            effective_config=effective_runtime_config,
            store=ParamStore(),
        )
    io = gui._imgui.get_io()
    io.config_flags = int(io.config_flags) & ~int(
        gui._imgui.CONFIG_NAV_ENABLE_KEYBOARD
    )
    initialized_state = gui.__dict__.copy()
    try:
        yield gui
    finally:
        # test が差し替えた fake ではなく、constructor が所有した resource を
        # production の close 経路で解放する。
        gui.__dict__.clear()
        gui.__dict__.update(initialized_state)
        gui.close()
