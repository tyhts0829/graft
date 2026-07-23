from __future__ import annotations

from typing import Any, cast

from grafix.core.parameters.frame_params import FrameParamRecord
from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.merge_ops import merge_frame_params
from grafix.core.parameters.meta import ParamMeta
from grafix.core.parameters.reconcile import ReconcileOrphan
from grafix.core.parameters.reconcile_ops import list_reconcile_orphans
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.ui_ops import update_state_from_ui
from grafix.interactive.parameter_gui.gui import ParameterGUI
from grafix.interactive.parameter_gui.reconcile_panel import (
    ReconcileMigrationRequest,
    reconcile_orphan_panel_model,
    reconcile_reason_text,
    render_reconcile_orphan_popup,
)
from tests.param_store_test_support import mutate_runtime


def _orphan(
    *,
    new_site: str = "new",
    old_sites: tuple[str, ...] = ("old-a", "old-b"),
    reason: str = "tie",
) -> ReconcileOrphan:
    return ReconcileOrphan(
        new_group=("circle", new_site),
        candidate_old_groups=tuple(("circle", site) for site in old_sites),
        score=45,
        reason=reason,  # type: ignore[arg-type]
    )


def test_orphan_panel_model_is_stable_and_explains_reason() -> None:
    model = reconcile_orphan_panel_model(
        (
            _orphan(new_site="new-b", old_sites=("old-b", "old-a")),
            _orphan(new_site="new-a", old_sites=("old-c",), reason="claimed"),
        )
    )

    assert [view.new_group for view in model.orphans] == [
        ("circle", "new-a"),
        ("circle", "new-b"),
    ]
    assert model.orphans[1].candidate_old_groups == (
        ("circle", "old-a"),
        ("circle", "old-b"),
    )
    assert model.orphan_count == 2
    assert model.candidate_count == 3
    assert "same saved group" in model.orphans[0].reason_text
    assert reconcile_reason_text("future-reason") == "A manual 1:1 choice is required."


class _RenderImgui:
    def __init__(self, *, clicked_id: str | None = None) -> None:
        self.clicked_id = clicked_id
        self.texts: list[str] = []
        self.disabled_texts: list[str] = []

    def text(self, value: str) -> None:
        self.texts.append(str(value))

    def text_disabled(self, value: str) -> None:
        self.disabled_texts.append(str(value))

    def separator(self) -> None:
        return None

    def same_line(self) -> None:
        return None

    def button(self, label: str) -> bool:
        return str(label).rpartition("##")[2] == self.clicked_id

    def small_button(self, label: str) -> bool:
        return str(label).rpartition("##")[2] == self.clicked_id


def test_popup_never_selects_a_candidate_without_explicit_click() -> None:
    imgui = _RenderImgui()
    model = reconcile_orphan_panel_model((_orphan(),))

    request = render_reconcile_orphan_popup(imgui, model)

    assert request is None
    assert any("Current group: circle  ·  new" in text for text in imgui.texts)
    assert any("Saved old group: circle  ·  old-a" in text for text in imgui.texts)
    assert any("Multiple saved groups" in text for text in imgui.disabled_texts)


def test_popup_returns_only_the_clicked_one_to_one_migration() -> None:
    imgui = _RenderImgui(clicked_id="reconcile_0_1")
    model = reconcile_orphan_panel_model((_orphan(),))

    request = render_reconcile_orphan_popup(imgui, model)

    assert request == ReconcileMigrationRequest(
        old_group=("circle", "old-b"),
        new_group=("circle", "new"),
    )


def test_popup_has_empty_fallback() -> None:
    imgui = _RenderImgui()
    model = reconcile_orphan_panel_model(())

    assert render_reconcile_orphan_popup(imgui, model) is None
    assert model.empty_message in imgui.disabled_texts


class _Popup:
    def __init__(self, opened: bool) -> None:
        self.opened = bool(opened)

    def __enter__(self) -> _Popup:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _ControlImgui(_RenderImgui):
    def __init__(self, *, clicked_candidate: str | None) -> None:
        super().__init__(clicked_id=clicked_candidate)
        self.popup_open = False

    def button(self, label: str) -> bool:
        widget_id = str(label).rpartition("##")[2]
        if widget_id == "reconcile_orphan_review":
            return True
        return widget_id == self.clicked_id

    def open_popup(self, _label: str) -> None:
        self.popup_open = True

    def begin_popup(self, _label: str) -> _Popup:
        return _Popup(self.popup_open)

    def close_current_popup(self) -> None:
        self.popup_open = False


def _ambiguous_store() -> tuple[ParamStore, ParameterKey]:
    store = ParamStore()
    meta = ParamMeta(kind="float", ui_min=0.0, ui_max=10.0)
    records = [
        FrameParamRecord(
            key=ParameterKey("circle", site, "radius"),
            base=base,
            meta=meta,
            effective=base,
            source="code",
            explicit=False,
        )
        for site, base in (("old-a", 1.0), ("old-b", 8.0), ("new", 2.0))
    ]
    merge_frame_params(store, records)
    old_b = ParameterKey("circle", "old-b", "radius")
    update_state_from_ui(store, old_b, 8.5, meta=meta, override=True)
    orphan = _orphan()
    mutate_runtime(
        store,
        lambda runtime: runtime.reconcile_orphans.__setitem__(
            orphan.new_group,
            orphan,
        ),
    )
    return store, ParameterKey("circle", "new", "radius")


def _gui_for_store(
    gui: ParameterGUI,
    store: ParamStore,
    *,
    clicked_candidate: str | None,
) -> tuple[Any, _ControlImgui, ParamStoreHistory]:
    history = ParamStoreHistory(store)
    imgui = _ControlImgui(clicked_candidate=clicked_candidate)
    gui_state = cast(Any, gui)
    gui_state._store = store
    gui_state._history = history
    gui_state._imgui = imgui
    gui_state._session.reconcile_error = None
    gui_state._session.table_view = None
    gui_state._session.favorite_keys = frozenset()
    return gui_state, imgui, history


def test_gui_requires_click_then_refreshes_list_and_records_undo(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    store, new_key = _ambiguous_store()
    gui, imgui, history = _gui_for_store(
        initialized_parameter_gui,
        store,
        clicked_candidate="reconcile_0_1",
    )

    assert gui._render_reconcile_orphan_control() is True

    migrated = store.get_state(new_key)
    assert migrated is not None and migrated.ui_value == 8.5
    assert list_reconcile_orphans(store) == ()
    assert gui._session.reconcile_model.orphan_count == 0
    assert imgui.popup_open is False
    assert history.undo_depth == 1
    assert history.undo() is True
    restored = store.get_state(new_key)
    assert restored is not None and restored.ui_value == 2.0


def test_gui_review_without_candidate_click_does_not_migrate(
    initialized_parameter_gui: ParameterGUI,
) -> None:
    store, new_key = _ambiguous_store()
    gui, _imgui, history = _gui_for_store(
        initialized_parameter_gui,
        store,
        clicked_candidate=None,
    )

    assert gui._render_reconcile_orphan_control() is False

    current = store.get_state(new_key)
    assert current is not None and current.ui_value == 2.0
    assert len(list_reconcile_orphans(store)) == 1
    assert history.undo_depth == 0
