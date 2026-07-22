from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
import importlib
from typing import Any, Protocol, cast

import pytest

from grafix.core.parameters import ParameterKey
from grafix.core.operation_catalog import OperationCatalog
from grafix.core.parameters.style import STYLE_OP, STYLE_SITE_ID
from grafix.core.preset_catalog import PresetCatalog
from grafix.core.runtime_config import RuntimeConfig
from grafix.interactive.parameter_gui.catalog import ParameterGuiCatalog
from grafix.interactive.runtime import parameter_gui_system as gui_system_module
from grafix.interactive.runtime.parameter_gui_system import ParameterGUIWindowSystem


class _Gui:
    def __init__(self, *, active: bool) -> None:
        self.parameter_edit_active = bool(active)
        self.draw_count = 0
        self.catalogs: list[ParameterGuiCatalog] = []

    def replace_catalog(self, catalog: ParameterGuiCatalog) -> None:
        self.catalogs.append(catalog)

    def draw_frame(self) -> None:
        self.draw_count += 1

    def close(self) -> None:
        pass


class _Autosave:
    path = "autosave.json"
    status = "dirty"
    last_error = None

    def __init__(self) -> None:
        self.suspended_values: list[bool] = []

    def tick(self, *, suspended: bool) -> bool:
        self.suspended_values.append(bool(suspended))
        return False


class _Store:
    def __init__(self) -> None:
        self.revision = 4
        self.value_revision = 2
        self.changed_keys = frozenset(
            {ParameterKey("circle", "site", "radius")}
        )

    def value_changes_since(
        self,
        _revision: int,
    ) -> frozenset[ParameterKey]:
        return self.changed_keys


class _GuiSystemFactory(Protocol):
    def __call__(
        self,
        *,
        gui: _Gui,
        store: _Store | None = None,
        autosave: _Autosave | None = None,
        catalog_provider: Callable[[], ParameterGuiCatalog] | None = None,
        on_parameter_revision_created: Callable[[int, int, str], None] | None = None,
    ) -> ParameterGUIWindowSystem: ...


@pytest.fixture
def gui_system_factory(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> Iterator[_GuiSystemFactory]:
    """実リソースだけを差し替え、production constructor で system を作る。"""

    systems: list[ParameterGUIWindowSystem] = []
    effective_config = replace(
        effective_runtime_config,
        parameter_gui_window_size=(480, 720),
    )
    monkeypatch.setattr(
        gui_system_module,
        "create_parameter_gui_window",
        lambda **_kwargs: object(),
    )

    def create(
        *,
        gui: _Gui,
        store: _Store | None = None,
        autosave: _Autosave | None = None,
        catalog_provider: Callable[[], ParameterGuiCatalog] | None = None,
        on_parameter_revision_created: Callable[[int, int, str], None] | None = None,
    ) -> ParameterGUIWindowSystem:
        selected_store = _Store() if store is None else store
        monkeypatch.setattr(
            gui_system_module,
            "ParameterGUI",
            lambda *_args, **_kwargs: gui,
        )
        system = ParameterGUIWindowSystem(
            effective_config=effective_config,
            store=cast(Any, selected_store),
            autosave=cast(Any, autosave),
            catalog_provider=catalog_provider,
            on_parameter_revision_created=on_parameter_revision_created,
        )
        systems.append(system)
        return system

    yield create

    for system in reversed(systems):
        system.close()


def test_gui_system_keeps_injected_config_after_ambient_config_changes(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> None:
    """window と ParameterGUI は runner が固定した同一configだけを使う。"""

    fixed_config = replace(
        effective_runtime_config,
        parameter_gui_window_size=(321, 654),
        parameter_gui_font_size_base_px=17.0,
    )
    ambient_config = replace(
        fixed_config,
        parameter_gui_window_size=(999, 888),
        parameter_gui_font_size_base_px=99.0,
    )
    runtime_config_module = importlib.import_module("grafix.runtime_config_loader")
    monkeypatch.setattr(
        runtime_config_module,
        "runtime_config",
        lambda: ambient_config,
    )

    window_calls: list[dict[str, object]] = []
    gui_calls: list[dict[str, object]] = []
    gui = _Gui(active=False)
    monkeypatch.setattr(
        gui_system_module,
        "create_parameter_gui_window",
        lambda **kwargs: window_calls.append(dict(kwargs)) or object(),
    )
    monkeypatch.setattr(
        gui_system_module,
        "ParameterGUI",
        lambda *_args, **kwargs: gui_calls.append(dict(kwargs)) or gui,
    )

    system = ParameterGUIWindowSystem(
        effective_config=fixed_config,
        store=cast(Any, _Store()),
    )
    try:
        assert window_calls == [
            {"width": 321, "height": 654, "vsync": False}
        ]
        assert gui_calls[0]["effective_config"] is fixed_config
    finally:
        system.close()


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"transport_fps": "60"}, TypeError),
        ({"transport_fps": 0.0}, ValueError),
        ({"ui_scale": True}, TypeError),
        ({"ui_scale": float("inf")}, ValueError),
    ],
)
def test_gui_system_rejects_invalid_scale_before_window_creation(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
    kwargs: dict[str, object],
    error: type[Exception],
) -> None:
    window_calls: list[object] = []
    monkeypatch.setattr(
        gui_system_module,
        "create_parameter_gui_window",
        lambda **values: window_calls.append(values),
    )

    with pytest.raises(error):
        ParameterGUIWindowSystem(
            effective_config=effective_runtime_config,
            store=cast(Any, _Store()),
            **kwargs,  # type: ignore[arg-type]
        )
    assert window_calls == []


def test_gui_system_rejects_invalid_catalog_provider_before_window_creation(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> None:
    window_calls: list[object] = []
    monkeypatch.setattr(
        gui_system_module,
        "create_parameter_gui_window",
        lambda **values: window_calls.append(values),
    )

    with pytest.raises(TypeError, match="catalog_provider"):
        ParameterGUIWindowSystem(
            effective_config=effective_runtime_config,
            store=cast(Any, _Store()),
            catalog_provider=cast(Any, object()),
        )

    assert window_calls == []


def test_gui_system_transfers_window_ownership_once_to_parameter_gui(
    monkeypatch: pytest.MonkeyPatch,
    effective_runtime_config: RuntimeConfig,
) -> None:
    calls: list[str] = []

    class Window:
        def switch_to(self) -> None:
            calls.append("switch")

        def close(self) -> None:
            calls.append("close")

    window = Window()
    monkeypatch.setattr(
        gui_system_module,
        "create_parameter_gui_window",
        lambda **_kwargs: window,
    )

    def fail_initialize(self: object, *_args: object, **_kwargs: object) -> None:
        raise LookupError("GUI construction failed")

    monkeypatch.setattr(gui_system_module.ParameterGUI, "_initialize", fail_initialize)
    catalog = ParameterGuiCatalog.capture(OperationCatalog({}), PresetCatalog({}))

    with pytest.raises(LookupError, match="GUI construction failed"):
        ParameterGUIWindowSystem(
            effective_config=effective_runtime_config,
            store=cast(Any, _Store()),
            catalog=catalog,
        )

    assert calls == ["switch", "close"]


def test_gui_system_suspends_autosave_while_an_item_is_active(
    gui_system_factory: _GuiSystemFactory,
) -> None:
    gui = _Gui(active=True)
    autosave = _Autosave()
    system = gui_system_factory(gui=gui, autosave=autosave)

    system.draw_frame()

    assert gui.draw_count == 1
    assert autosave.suspended_values == [True]


def test_gui_system_adopts_catalog_generation_before_drawing(
    gui_system_factory: _GuiSystemFactory,
) -> None:
    gui = _Gui(active=False)
    catalog = ParameterGuiCatalog.capture(
        OperationCatalog({}),
        PresetCatalog({}),
    )
    system = gui_system_factory(
        gui=gui,
        catalog_provider=lambda: catalog,
    )

    system.draw_frame()

    assert gui.catalogs == [catalog]
    assert gui.draw_count == 1


def test_gui_system_reports_revision_from_start_of_edit_frame(
    gui_system_factory: _GuiSystemFactory,
) -> None:
    store = _Store()
    events: list[tuple[int, int, str]] = []

    class EditingGui(_Gui):
        def draw_frame(self) -> None:
            super().draw_frame()
            store.revision += 1
            store.value_revision += 1

    system = gui_system_factory(
        gui=EditingGui(active=True),
        store=store,
        on_parameter_revision_created=lambda revision, timestamp_ns, domain: events.append(
            (revision, timestamp_ns, domain)
        ),
    )

    system.draw_frame()

    assert len(events) == 1
    assert events[0][0] == 5
    assert events[0][1] > 0
    assert events[0][2] == "geometry"


def test_gui_system_ignores_structure_only_revision_for_input_latency(
    gui_system_factory: _GuiSystemFactory,
) -> None:
    store = _Store()
    events: list[tuple[int, int, str]] = []

    class StructureGui(_Gui):
        def draw_frame(self) -> None:
            super().draw_frame()
            store.revision += 1

    system = gui_system_factory(
        gui=StructureGui(active=False),
        store=store,
        on_parameter_revision_created=lambda revision, timestamp_ns, domain: events.append(
            (revision, timestamp_ns, domain)
        ),
    )

    system.draw_frame()

    assert events == []


def test_gui_system_classifies_style_revision_for_present_latency(
    gui_system_factory: _GuiSystemFactory,
) -> None:
    store = _Store()
    store.changed_keys = frozenset(
        {ParameterKey(STYLE_OP, STYLE_SITE_ID, "global_thickness")}
    )
    events: list[tuple[int, int, str]] = []

    class EditingGui(_Gui):
        def draw_frame(self) -> None:
            super().draw_frame()
            store.revision += 1
            store.value_revision += 1

    system = gui_system_factory(
        gui=EditingGui(active=True),
        store=store,
        on_parameter_revision_created=lambda revision, timestamp_ns, domain: events.append(
            (revision, timestamp_ns, domain)
        ),
    )

    system.draw_frame()

    assert len(events) == 1
    assert events[0][0] == 5
    assert events[0][2] == "style"
