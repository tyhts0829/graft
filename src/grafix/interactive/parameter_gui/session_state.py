# どこで: `src/grafix/interactive/parameter_gui/session_state.py`。
# 何を: Parameter GUI の frame 間 UI state を一つの lifetime owner にまとめる。
# なぜ: ParameterGUI 本体を描画順序と controller 配線へ集中させるため。

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from grafix.core.parameters.favorites import favorite_parameter_key_set
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.reconcile_ops import list_reconcile_orphans
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.view import ParameterRow

from .catalog import ParameterGuiCatalog
from .midi_learn import MidiLearnState
from .parameter_filter import ParameterFilterState
from .reconcile_panel import ReconcileOrphanPanelModel, reconcile_orphan_panel_model

if TYPE_CHECKING:
    from .table_view import ParameterTableView, ParameterTableViewCache


@dataclass(frozen=True, slots=True)
class MidiClearNotice:
    """MIDI mapping 一括解除後の Undo 導線。"""

    message: str
    history_token: tuple[int, int] | None


@dataclass(slots=True)
class WidgetSessionState:
    """widget と snippet popup の GUI instance 固有状態。

    ``font_choices`` は font combo を初めて開いた時に構築し、この
    GUI session の全 font row で共有する。filesystem の変化は
    font picker の明示的な refresh でのみ反映する。
    """

    font_filter_by_key: dict[tuple[str, str, str], str] = field(default_factory=dict)
    font_choices: tuple[tuple[str, str, bool, str], ...] | None = None
    choice_filter_by_key: dict[tuple[str, str, str], str] = field(default_factory=dict)
    snippet_popup_text: str = ""
    snippet_popup_focus_next: bool = False

    def clear(self) -> None:
        """GUI close 時に widget 固有の一時状態をまとめて解放する。"""

        self.font_filter_by_key.clear()
        self.font_choices = None
        self.choice_filter_by_key.clear()
        self.snippet_popup_text = ""
        self.snippet_popup_focus_next = False


@dataclass(slots=True)
class ParameterGuiSessionState:
    """Parameter GUI instance と同じ寿命を持つ frame 間 state。"""

    table_cache: ParameterTableViewCache
    show_inactive_parameters: bool = False
    filter_state: ParameterFilterState = field(default_factory=ParameterFilterState)
    table_view: ParameterTableView | None = None
    favorite_keys: frozenset[ParameterKey] = frozenset()
    error_keys: frozenset[ParameterKey] = frozenset()
    help_row: ParameterRow | None = None
    parameter_edit_active: bool = False
    reconcile_model: ReconcileOrphanPanelModel = field(
        default_factory=lambda: reconcile_orphan_panel_model(())
    )
    reconcile_error: str | None = None
    midi_clear_notice: MidiClearNotice | None = None
    midi_learn: MidiLearnState = field(default_factory=MidiLearnState)
    widgets: WidgetSessionState = field(default_factory=WidgetSessionState)

    @classmethod
    def for_store(
        cls,
        store: ParamStore,
        *,
        catalog: ParameterGuiCatalog,
    ) -> ParameterGuiSessionState:
        """store 由来の初期 state を構築する。"""

        from .table_view import ParameterTableViewCache

        return cls(
            table_cache=ParameterTableViewCache(catalog),
            favorite_keys=favorite_parameter_key_set(store),
            reconcile_model=reconcile_orphan_panel_model(list_reconcile_orphans(store)),
        )

    def invalidate_table(self) -> None:
        """次の描画で immutable table view を再構築させる。"""

        self.table_view = None

    def replace_catalog(self, catalog: ParameterGuiCatalog) -> None:
        """この session の table cache だけを新 catalog へ交換する。"""

        if type(catalog) is not ParameterGuiCatalog:
            raise TypeError("catalog は exact ParameterGuiCatalog である必要があります")
        if catalog is self.table_cache.catalog:
            return
        from .table_view import ParameterTableViewCache

        self.table_cache.clear()
        self.table_cache = ParameterTableViewCache(catalog)
        self.table_view = None

    def close(self) -> None:
        """table/widget state と session-owned cache をまとめて破棄する。"""

        self.table_view = None
        self.table_cache.clear()
        self.widgets.clear()


__all__ = ["MidiClearNotice", "ParameterGuiSessionState", "WidgetSessionState"]
