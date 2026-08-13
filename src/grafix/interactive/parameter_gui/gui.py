"""
Purpose:
    Parameter GUI backend、session state、controller、panelを一frameのInspectorへ合成する。
Use when:
    GUI frame順、toolbar/panel配線、catalog交換、またはInspector resource lifetimeを変更する場合。
Constraints:
    - store mutationとdomain transactionを各controller/table commitへ委譲する。
    - catalog/cache/widget stateをGUI instanceに閉じ、close時にGL contextが生存中の順で解放する。
Side effects:
    ImGui/pyglet windowを描画し、user intentに応じてcontrollerを呼び出す。
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from grafix.core.evaluation_config import EvaluationConfig, bind_evaluation_config
from grafix.core.font_resolver import default_font_path, resolve_font_path
from grafix.core.lifecycle import CleanupErrors
from grafix.core.parameters.favorites import favorite_parameter_key_set
from grafix.core.parameters.history import (
    ParamSnapshotSlots,
    ParamStoreHistory,
)
from grafix.core.parameters.reconcile_ops import list_reconcile_orphans
from grafix.core.parameters.snapshot_ops import store_snapshot
from grafix.core.parameters.store import ParamStore
from grafix.core.parameters.view import ParameterRow
from grafix.core.runtime_config import RuntimeConfig, bind_runtime_config
from grafix.core.value_validation import exact_string, finite_real
from grafix.interactive.midi import MidiSession
from grafix.interactive.pyglet_window_lifecycle import close_pyglet_window
from grafix.interactive.transport import TransportClock
from grafix.interactive.telemetry import MonitorSnapshot, TelemetrySource

from .catalog import ParameterGuiCatalog, current_parameter_gui_catalog
from .midi_learn import MidiLearnState
from .monitor_bar import monitor_alert_lines, render_monitor_alerts, render_monitor_status
from .diagnostics_panel import render_diagnostics_panel
from .help_pane import render_parameter_help_pane
from .profiler_panel import render_profiler_panel
from .parameter_filter import ParameterActivityFilter
from .pyglet_backend import (
    PygletImguiBackend,
    content_region_available_width,
)
from .range_edit import RangeEditMode
from .range_edit_controller import RangeEditController
from .reconcile_panel import (
    apply_reconcile_migration,
    reconcile_orphan_panel_model,
    render_reconcile_orphan_popup,
)
from .session_state import MidiClearNotice, ParameterGuiSessionState
from .shortcuts import resolve_shortcut_keys, shortcut_help_lines
from .table_commit import (
    clear_all_midi_assignments,
    render_store_parameter_table,
    set_all_parameter_groups_collapsed,
)
from .table_view import parameter_table_view_for_store
from .theme import PARAMETER_GUI_PALETTE, apply_parameter_gui_theme
from .variation_controller import VariationController
from .variation_panel import (
    VariationThumbnailCapture,
    VariationThumbnailPreview,
)


_VARIATION_DELETE_POPUP_ID = "Delete variation##variation_delete_confirmation"
_TOOLBAR_LABEL_WIDTH_PX = 64.0
_BOTTOM_DRAWER_HEIGHT_PX = 176.0
_BOTTOM_DRAWER_GAP_PX = 10.0
_BOTTOM_DRAWER_HELP_RATIO = 0.58


def _positive_coordinate_scale(value: float) -> float:
    """正の coordinate scale を返し、不正値だけを 1.0 へ戻す。"""

    scale = float(value)
    return scale if scale > 0.0 else 1.0


@dataclass(frozen=True, slots=True)
class ToolbarLayout:
    """上部 Controls / Status surface の純粋な幅計算結果。"""

    stacked: bool
    controls_width: float
    status_width: float
    gap: float
    surface_height: float
    coordinate_scale: float


@dataclass(frozen=True, slots=True)
class TransportToolbarGeometry:
    """TIME行の幅契約。reserved はcaption/buttons/spacingの合計。"""

    available_width: float
    reserved_width: float
    timeline_width: float

    @property
    def required_width(self) -> float:
        return self.reserved_width + self.timeline_width

    @property
    def fits(self) -> bool:
        return self.required_width <= self.available_width + 0.01


@dataclass(frozen=True, slots=True)
class BottomDrawerGeometry:
    """固定 bottom drawer の高さと左右 pane 幅。"""

    height: float
    gap: float
    help_width: float
    runtime_width: float


def compute_bottom_drawer_geometry(
    content_width: float,
    *,
    coordinate_scale: float = 1.0,
) -> BottomDrawerGeometry:
    """幅変更から独立した drawer 高さと、Help / Runtime の幅を返す。"""

    width = max(0.0, float(content_width))
    scale = _positive_coordinate_scale(coordinate_scale)
    gap = min(width, _BOTTOM_DRAWER_GAP_PX * scale)
    panes_width = max(0.0, width - gap)
    help_width = panes_width * _BOTTOM_DRAWER_HELP_RATIO
    return BottomDrawerGeometry(
        height=_BOTTOM_DRAWER_HEIGHT_PX * scale,
        gap=gap,
        help_width=help_width,
        runtime_width=max(0.0, panes_width - help_width),
    )


def compute_transport_toolbar_geometry(
    controls_width: float,
    *,
    coordinate_scale: float = 1.0,
) -> TransportToolbarGeometry:
    """標準Inspectorでtimelineを160px以上確保する純粋なgeometry計算。"""

    width = max(0.0, float(controls_width))
    scale = _positive_coordinate_scale(coordinate_scale)
    # TIME caption、明示幅button 6個、speed label、item spacing 8個。
    reserved_width = 358.0 * scale
    timeline_width = max(160.0 * scale, min(220.0 * scale, width - reserved_width))
    return TransportToolbarGeometry(
        available_width=width,
        reserved_width=reserved_width,
        timeline_width=timeline_width,
    )


def compute_toolbar_layout(
    content_width: float,
    *,
    coordinate_scale: float = 1.0,
) -> ToolbarLayout:
    """通常幅は約 65:35、760px 未満は compact status を下へ積む。"""

    width = max(0.0, float(content_width))
    scale = _positive_coordinate_scale(coordinate_scale)
    if width >= 760.0 * scale:
        gap = 12.0 * scale
        # 固定 50:50 にせず、制作操作と160px以上のtimelineを優先する。
        # 標準768px contentではControls 68.4% / Status 30%（残りはgap）。
        status_width = min(300.0 * scale, max(228.0 * scale, width * 0.30))
        controls_width = max(0.0, width - gap - status_width)
        return ToolbarLayout(
            stacked=False,
            controls_width=controls_width,
            status_width=status_width,
            gap=gap,
            surface_height=66.0 * scale,
            coordinate_scale=scale,
        )
    return ToolbarLayout(
        stacked=True,
        controls_width=width,
        status_width=width,
        gap=6.0 * scale,
        # Compact status is rendered separately below, so two control rows do
        # not need the extra height reserved for the three-line desktop status.
        surface_height=56.0 * scale,
        coordinate_scale=scale,
    )


def _window_ui_coordinate_scale(window: Any, *, ui_scale: float = 1.0) -> float:
    """ウィンドウ幅と独立した ImGui 寸法倍率を返す。"""

    return _compute_window_backing_scale(window) * _positive_coordinate_scale(ui_scale)


def _same_line_with_spacing(imgui: Any, spacing: float) -> None:
    """明示的な group gap を作る。"""

    imgui.same_line(spacing=float(spacing))


def _same_line_at(imgui: Any, position: float) -> None:
    """同一 surface 内の固定 x へ次 item を揃える。"""

    imgui.same_line(position=float(position))


def _vertical_item_spacing(imgui: Any) -> float:
    """現在の style が child 間へ自動挿入する縦 spacing を返す。"""

    return max(0.0, float(imgui.get_style().item_spacing[1]))


def _button_with_width(imgui: Any, label: str, width: float) -> bool:
    """明示幅の button を描画する。"""

    return bool(imgui.button(str(label), float(width), 0.0))


def _item_tooltip(imgui: Any, text: str) -> None:
    """hoverまたはkeyboard focus中のitemへtooltipを表示する。"""

    if imgui.is_item_hovered() or imgui.is_item_focused():
        imgui.set_tooltip(str(text))


def _enable_keyboard_navigation(imgui: Any) -> bool:
    """ImGuiのTab/Enter navigation flagを有効化できたらTrueを返す。"""

    keyboard_nav = int(imgui.CONFIG_NAV_ENABLE_KEYBOARD)
    io = imgui.get_io()
    io.config_flags = int(io.config_flags) | keyboard_nav
    return True


def _begin_toolbar_surface(imgui: Any, label: str, width: float, height: float) -> None:
    """弱い surface 色を適用して child を開始する。"""

    imgui.push_style_color(
        imgui.COLOR_CHILD_BACKGROUND,
        *PARAMETER_GUI_PALETTE["surface"],
    )
    imgui.begin_child(str(label), float(width), float(height), border=False)


def _end_toolbar_surface(imgui: Any) -> None:
    imgui.end_child()
    imgui.pop_style_color()


def _midi_assignment_count(store: ParamStore) -> int:
    """visible/inactive を問わず、割り当て済み CC component 数を返す。"""

    count = 0
    for _key, (_meta, state, _ordinal, _label) in store_snapshot(store).items():
        cc_key = state.cc_key
        if isinstance(cc_key, int):
            count += 1
        elif cc_key is not None:
            count += sum(1 for cc in cc_key if cc is not None)
    return count


def _section_separator(imgui: Any) -> None:
    """上部 toolbar と parameter table の境界を描く。"""

    imgui.separator()


def _default_gui_font_path() -> Path | None:
    try:
        return default_font_path()
    except OSError:
        return None


def _gui_fallback_font_path_for_japanese(
    effective_config: RuntimeConfig,
) -> Path | None:
    """parameter_gui の日本語表示用フォールバックフォントを解決して返す。"""

    specified = effective_config.parameter_gui_fallback_font_japanese
    if specified:
        try:
            return resolve_font_path(str(specified))
        except OSError:
            return None

    for font in ("Hiragino Sans GB.ttc", "NotoSansJP-VariableFont_wght.ttf"):
        try:
            return resolve_font_path(str(font))
        except OSError:
            continue
    return None


def _favorite_glyph_font_path() -> Path | None:
    """favorite の星 glyph を必ず持つ同梱フォントを返す。"""

    try:
        return resolve_font_path("NotoSansJP-Regular.ttf")
    except OSError:
        return None


def _compute_window_backing_scale(gui_window: Any) -> float:
    """ウィンドウの backing scale（DPI 倍率）を返す。"""

    return float(max(float(gui_window.scale), 1.0))


class ParameterGUI:
    """pyimgui で ParamStore を編集するための最小 GUI。

    `draw_frame()` を呼ぶことで 1 フレーム分の UI を描画する。
    """

    def __init__(
        self,
        gui_window: Any,
        *,
        effective_config: RuntimeConfig,
        store: ParamStore,
        midi_session: MidiSession | None = None,
        monitor: TelemetrySource | None = None,
        transport: TransportClock | None = None,
        transport_fps: float = 60.0,
        history: ParamStoreHistory | None = None,
        snapshot_slots: ParamSnapshotSlots | None = None,
        is_recording: Callable[[], bool] | None = None,
        variation_thumbnail_capture: VariationThumbnailCapture | None = None,
        variation_thumbnail_preview: VariationThumbnailPreview | None = None,
        ui_scale: float = 1.0,
        title: str = "Parameters",
        catalog: ParameterGuiCatalog | None = None,
    ) -> None:
        """GUIをtransactionalに初期化し、途中失敗時も取得済みresourceを解放する。"""

        # `_initialize` が import/context/renderer/font の途中で失敗しても、
        # constructor は部分 object を caller へ返さない。close() は getattr
        # ベースなので、作成済みのものだけを安全に逆順 cleanup できる。
        self._window = gui_window
        self._closed = False
        try:
            frame_rate = finite_real(
                transport_fps,
                name="transport_fps",
                minimum=0.0,
                minimum_inclusive=False,
            )
            scale = finite_real(
                ui_scale,
                name="ui_scale",
                minimum=0.0,
                minimum_inclusive=False,
            )
            window_title = exact_string(title, name="title")
            if not window_title:
                raise ValueError("title は空にできません")
            self._catalog = (
                current_parameter_gui_catalog() if catalog is None else catalog
            )
            if type(self._catalog) is not ParameterGuiCatalog:
                raise TypeError("catalog は exact ParameterGuiCatalog である必要があります")
            evaluation_config = EvaluationConfig(font_dirs=effective_config.font_dirs)
            with (
                bind_runtime_config(effective_config),
                bind_evaluation_config(evaluation_config),
            ):
                self._initialize(
                    gui_window,
                    effective_config=effective_config,
                    store=store,
                    midi_session=midi_session,
                    monitor=monitor,
                    transport=transport,
                    transport_fps=frame_rate,
                    history=history,
                    snapshot_slots=snapshot_slots,
                    is_recording=is_recording,
                    variation_thumbnail_capture=variation_thumbnail_capture,
                    variation_thumbnail_preview=variation_thumbnail_preview,
                    ui_scale=scale,
                    title=window_title,
                )
        except BaseException:
            try:
                self.close()
            except BaseException:
                # 初期化の根本例外を優先する。close() は全stepを既に試している。
                pass
            raise

    @property
    def catalog(self) -> ParameterGuiCatalog:
        """現在の immutable parameter schema catalog を返す。"""

        return self._catalog

    def replace_catalog(self, catalog: ParameterGuiCatalog) -> None:
        """成功した authoring generation の GUI projection へ切り替える。

        catalog identity が同じ場合は何もしない。異なる generation では table の
        derived view だけを破棄し、ParamStore の値・履歴・選択状態は維持する。
        """

        if type(catalog) is not ParameterGuiCatalog:
            raise TypeError("catalog は exact ParameterGuiCatalog である必要があります")
        if catalog is self._catalog:
            return
        self._catalog = catalog
        self._session.replace_catalog(catalog)

    def _initialize(
        self,
        gui_window: Any,
        *,
        effective_config: RuntimeConfig,
        store: ParamStore,
        midi_session: MidiSession | None = None,
        monitor: TelemetrySource | None = None,
        transport: TransportClock | None = None,
        transport_fps: float = 60.0,
        history: ParamStoreHistory | None = None,
        snapshot_slots: ParamSnapshotSlots | None = None,
        is_recording: Callable[[], bool] | None = None,
        variation_thumbnail_capture: VariationThumbnailCapture | None = None,
        variation_thumbnail_preview: VariationThumbnailPreview | None = None,
        ui_scale: float = 1.0,
        title: str = "Parameters",
    ) -> None:
        """GUI の初期化本体（ImGui コンテキスト / renderer 作成）。"""

        # GUI の描画対象となるウィンドウと、編集対象の ParamStore を保持する。
        self._window = gui_window
        self._effective_config = effective_config
        self._store = store
        self._midi_session = midi_session
        self._monitor = monitor
        self._transport = transport
        self._transport_fps = transport_fps
        self._history = history
        self._snapshot_slots = snapshot_slots
        self._is_recording = is_recording
        self._variation_controller = VariationController(
            store,
            history=history,
            transport=transport,
            thumbnail_capture=variation_thumbnail_capture,
            thumbnail_preview=variation_thumbnail_preview,
        )
        self._range_edit_controller = RangeEditController(store, history=history)
        self._range_edit_key_r = 0
        self._range_edit_key_e = 0
        self._range_edit_key_t = 0
        self._range_edit_key_escape = 0
        self._transport_key_space = 0
        self._transport_key_home = 0
        self._transport_key_left = 0
        self._transport_key_right = 0
        self._transport_key_slower = 0
        self._transport_key_faster = 0
        self._history_key_z = 0
        self._history_key_y = 0
        self._shortcut_modifier_mask = 0
        self._shortcut_shift_mask = 0
        self._session = ParameterGuiSessionState.for_store(
            store,
            catalog=self._catalog,
        )
        self._title = title
        self._ui_scale = ui_scale
        self._font_size_base_px = (
            float(effective_config.parameter_gui_font_size_base_px) * self._ui_scale
        )
        self._shortcut_bindings = effective_config.parameter_gui_shortcuts

        # context/renderer/frame lifecycle は backend が一括所有する。
        self._backend = PygletImguiBackend(gui_window)
        imgui = self._backend.imgui
        self._imgui = imgui
        imgui.style_colors_dark()
        apply_parameter_gui_theme(imgui, ui_scale=self._ui_scale)

        # pyglet は環境によって「座標系が backing pixel」になり得る。
        # その場合、Retina では物理サイズが小さく見えるため、フォント生成 px を DPI で補正する。
        io = imgui.get_io()
        io.font_global_scale = 1.0
        _enable_keyboard_navigation(imgui)

        from pyglet.window import key as pyglet_key

        shortcut_keys = resolve_shortcut_keys(
            self._shortcut_bindings,
            key_namespace=pyglet_key,
        )
        self._range_edit_key_r = shortcut_keys["range_shift"]
        self._range_edit_key_e = shortcut_keys["range_min"]
        self._range_edit_key_t = shortcut_keys["range_max"]
        self._range_edit_key_escape = shortcut_keys["cancel"]
        self._transport_key_space = shortcut_keys["play_pause"]
        self._transport_key_home = shortcut_keys["reset_time"]
        self._transport_key_left = shortcut_keys["step_backward"]
        self._transport_key_right = shortcut_keys["step_forward"]
        self._transport_key_slower = shortcut_keys["slower"]
        self._transport_key_faster = shortcut_keys["faster"]
        self._history_key_z = shortcut_keys["undo"]
        self._history_key_y = shortcut_keys["redo"]
        self._shortcut_modifier_mask = int(pyglet_key.MOD_CTRL) | int(pyglet_key.MOD_COMMAND)
        self._shortcut_shift_mask = int(pyglet_key.MOD_SHIFT)
        self._window.push_handlers(
            on_key_press=self._on_key_press,
            on_deactivate=self._on_deactivate,
        )

        self._custom_font_path = _default_gui_font_path()
        self._font_backing_scale: float | None = None
        self._font_fallback_path_for_japanese: Path | None = None
        self._font_sync_key: tuple[object, ...] | None = None
        self._sync_font_for_window()

        import time

        # ImGui に渡す delta_time 用の前回時刻。
        self._prev_time = time.monotonic()
        self._closed = False

    def _render_transport_toolbar(
        self,
        *,
        timeline_width: float | None = None,
        coordinate_scale: float = 1.0,
    ) -> None:
        """時間を止めて同じ frame を調整する transport 操作を描画する。"""

        transport = self._transport
        if transport is None:
            return

        imgui = self._imgui
        scale = max(1.0, float(coordinate_scale))
        snapshot = transport.snapshot()
        is_recording = self._is_recording
        if is_recording is not None and is_recording():
            imgui.text_disabled(f"Transport locked  ·  {snapshot.t:.3f}s  ·  fixed 1x")
            return

        play_label = "Pause##transport_play" if snapshot.is_playing else "Play##transport_play"
        if _button_with_width(imgui, play_label, 54.0 * scale):
            transport.toggle()
        imgui.same_line()
        if _button_with_width(imgui, "Reset##transport_reset", 54.0 * scale):
            transport.reset()
        imgui.same_line()
        if _button_with_width(imgui, "-1f##transport_back", 39.0 * scale):
            transport.step_frame(fps=self._transport_fps, frames=-1)
        imgui.same_line()
        if _button_with_width(imgui, "+1f##transport_forward", 39.0 * scale):
            transport.step_frame(fps=self._transport_fps, frames=1)

        imgui.same_line()
        if _button_with_width(imgui, "-##transport_slower", 26.0 * scale):
            transport.set_speed(max(0.125, transport.speed / 2.0))
        _item_tooltip(imgui, "Slower · halve playback speed")
        imgui.same_line()
        imgui.text_disabled(f"{transport.speed:g}x")
        imgui.same_line()
        if _button_with_width(imgui, "+##transport_faster", 26.0 * scale):
            transport.set_speed(min(8.0, transport.speed * 2.0))
        _item_tooltip(imgui, "Faster · double playback speed")

        imgui.same_line()
        timeline_width_px = 160.0 * scale if timeline_width is None else float(timeline_width)
        imgui.set_next_item_width(float(timeline_width_px))
        changed_t, next_t = imgui.drag_float(
            "##transport_time",
            float(snapshot.t),
            0.01,
            0.0,
            0.0,
            "%.3f s",
        )
        if changed_t:
            transport.pause()
            transport.seek(float(next_t))

    def _render_variation_delete_confirmation(self) -> bool:
        """削除対象名と不可逆性を示す modal を描画する。"""

        imgui = self._imgui
        controller = self._variation_controller
        state = controller.state
        changed = False
        with imgui.begin_popup_modal(_VARIATION_DELETE_POPUP_ID) as popup:
            if not popup.opened:
                return False
            name = state.pending_delete_name
            if name is None:
                imgui.text_disabled("The selected variation is no longer available.")
                if imgui.button("Close##variation_delete_missing"):
                    imgui.close_current_popup()
                return False

            imgui.text_wrapped(f'Delete variation "{name}"?')
            imgui.text_disabled("This cannot be undone.")
            if imgui.button("Cancel##variation_delete_cancel"):
                controller.cancel_delete()
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("Delete permanently##variation_delete_confirm"):
                changed = controller.confirm_delete_pending()
                imgui.close_current_popup()
        return changed

    def _render_variation_combo(
        self,
        label: str,
        names: tuple[str, ...],
        selected: str | None,
    ) -> str | None:
        imgui = self._imgui
        selection = self._variation_controller.normalized_selection(names, selected)
        with imgui.begin_combo(label, selection or "(none)") as combo:
            if combo.opened:
                for name in names:
                    clicked, _selected = imgui.selectable(
                        f"{name}##{label}_{name}",
                        name == selection,
                    )
                    if clicked:
                        selection = name
                    if name == selection:
                        imgui.set_item_default_focus()
        return selection

    def _render_variation_popup(self) -> bool:
        """named variation と探索操作を一つの popup に描画する。"""

        imgui = self._imgui
        controller = self._variation_controller
        state = controller.state
        model = controller.synchronize_panel()
        names = model.names

        changed = False
        imgui.text("Save current variation")
        _name_changed, state.new_name = imgui.input_text(
            "Name##variation_new_name",
            state.new_name,
        )
        _note_changed, state.new_note = imgui.input_text(
            "Note##variation_new_note",
            state.new_note,
        )
        _seed_enabled_changed, state.include_seed = imgui.checkbox(
            "Store seed##variation_include_seed",
            state.include_seed,
        )
        imgui.same_line()
        _seed_changed, state.random_seed = imgui.input_int(
            "Seed##variation_seed",
            int(state.random_seed),
        )
        if imgui.button("Save current##variation_save"):
            command_changed = controller.save()
            changed = command_changed or changed

        imgui.separator()
        imgui.text("Explore scope")
        if imgui.button(
            ("[Filtered]" if state.scope == "filtered" else "Filtered")
            + "##variation_scope_filtered"
        ):
            state.scope = "filtered"
        imgui.same_line()
        if imgui.button(
            ("[Favorites]" if state.scope == "favorites" else "Favorites")
            + "##variation_scope_favorites"
        ):
            state.scope = "favorites"
        scope = controller.scope_summary(
            parameter_table_view_for_store(
                self._store,
                cache=self._session.table_cache,
                show_inactive_params=bool(self._session.show_inactive_parameters),
                filter_state=self._session.filter_state,
                error_keys=self._session.error_keys,
                favorite_keys=favorite_parameter_key_set(self._store),
            )
        )
        imgui.same_line()
        imgui.text_disabled(f"{scope.parameter_count} parameters  ·  {scope.locked_count} locked")
        _random_seed_changed, state.random_seed = imgui.input_int(
            "Random seed##variation_random_seed",
            int(state.random_seed),
        )
        if imgui.button("Randomize##variation_randomize"):
            command_changed = controller.randomize(scope)
            if command_changed:
                self._session.invalidate_table()
            changed = command_changed or changed
        imgui.same_line()
        if imgui.button("Lock scope##variation_lock_scope"):
            command_changed = controller.set_scope_locked(scope, locked=True)
            if command_changed:
                self._session.invalidate_table()
            changed = command_changed or changed
        imgui.same_line()
        if imgui.button("Unlock scope##variation_unlock_scope"):
            command_changed = controller.set_scope_locked(scope, locked=False)
            if command_changed:
                self._session.invalidate_table()
            changed = command_changed or changed

        imgui.separator()
        imgui.text("Morph named variations")
        state.morph_a = self._render_variation_combo(
            "A##variation_morph_a",
            names,
            state.morph_a,
        )
        imgui.same_line()
        state.morph_b = self._render_variation_combo(
            "B##variation_morph_b",
            names,
            state.morph_b,
        )
        _amount_changed, state.morph_amount = imgui.slider_float(
            "Amount##variation_morph_amount",
            float(state.morph_amount),
            0.0,
            1.0,
        )
        if imgui.button("Apply morph##variation_morph_apply"):
            command_changed = controller.morph(scope)
            if command_changed:
                self._session.invalidate_table()
            changed = command_changed or changed

        imgui.separator()
        imgui.text(f"Saved variations ({model.count})")
        if not model.items:
            imgui.text_disabled(model.empty_message)
        for index, item in enumerate(model.items):
            selected = item.name == state.selected_name
            if imgui.button(f"{'>' if selected else ' '} {item.name}##variation_select_{index}"):
                controller.select(item.name)
            imgui.same_line()
            seed_label = "none" if item.seed is None else str(item.seed)
            imgui.text_disabled(
                f"{item.timestamp}  ·  seed {seed_label}  ·  {item.diff_count} diffs"
            )
            if item.note:
                imgui.text_wrapped(item.note)
            if item.thumbnail_path is not None:
                preview_message = controller.preview_thumbnail(
                    imgui,
                    item.thumbnail_path,
                )
                if preview_message is not None:
                    imgui.text_disabled(preview_message)

        if state.selected_name is not None:
            imgui.separator()
            imgui.text(f"Selected: {state.selected_name}")
            _target_changed, state.target_name = imgui.input_text(
                "Rename to##variation_target_name",
                state.target_name,
            )
            _duplicate_changed, state.duplicate_name = imgui.input_text(
                "Duplicate as##variation_duplicate_name",
                state.duplicate_name,
            )
            if imgui.button("Load##variation_load"):
                command_changed = controller.load(state.selected_name)
                if command_changed:
                    self._session.invalidate_table()
                changed = command_changed or changed
            imgui.same_line()
            if imgui.button("Rename##variation_rename"):
                command_changed = controller.rename_selected()
                changed = command_changed or changed
            imgui.same_line()
            if imgui.button("Duplicate##variation_duplicate"):
                command_changed = controller.duplicate_selected()
                changed = command_changed or changed
            imgui.same_line()
            if imgui.button("Delete##variation_delete"):
                if controller.request_delete_selected():
                    imgui.open_popup(_VARIATION_DELETE_POPUP_ID)

        changed = self._render_variation_delete_confirmation() or changed

        if state.notice:
            imgui.separator()
            imgui.text_disabled(state.notice)
        return changed

    def _render_history_toolbar(self) -> bool:
        """Undo/Redo と named variation popup を描画する。"""

        history = self._history
        if history is None:
            return False

        imgui = self._imgui
        changed = False
        if imgui.button("Undo##param_undo"):
            changed = history.undo() or changed
        imgui.same_line()
        if imgui.button("Redo##param_redo"):
            changed = history.redo() or changed
        imgui.same_line()
        imgui.text_disabled(f"{history.undo_depth} / {history.redo_depth}")
        imgui.same_line()

        variation_count = self._variation_controller.count
        if imgui.button(f"Variations {variation_count}##variation_popup_button"):
            imgui.open_popup("Named variations##variation_popup")
        with imgui.begin_popup("Named variations##variation_popup") as popup:
            if popup.opened:
                changed = self._render_variation_popup() or changed
        return changed

    def _render_controls_surface(
        self,
        *,
        controls_width: float,
        coordinate_scale: float,
    ) -> bool:
        """制作操作を TIME と HISTORY の2行へ意味的にまとめる。"""

        imgui = self._imgui
        changed = False
        label_width = _TOOLBAR_LABEL_WIDTH_PX * float(coordinate_scale)
        if self._transport is not None:
            imgui.align_text_to_frame_padding()
            imgui.text_disabled("TIME")
            _same_line_at(imgui, label_width)
            geometry = compute_transport_toolbar_geometry(
                float(controls_width),
                coordinate_scale=float(coordinate_scale),
            )
            self._render_transport_toolbar(
                timeline_width=geometry.timeline_width,
                coordinate_scale=float(coordinate_scale),
            )
        if self._history is not None:
            imgui.align_text_to_frame_padding()
            imgui.text_disabled("HISTORY")
            _same_line_at(imgui, label_width)
            changed = self._render_history_toolbar() or changed
        return changed

    def _render_toolbar_area(
        self,
        *,
        content_width: float,
        monitor_snapshot: MonitorSnapshot | None,
    ) -> bool:
        """通常幅は Controls / Status 2列、狭幅は compact status を下へ積む。"""

        imgui = self._imgui
        coordinate_scale = _window_ui_coordinate_scale(
            self._window,
            ui_scale=float(self._ui_scale),
        )
        layout = compute_toolbar_layout(
            float(content_width),
            coordinate_scale=coordinate_scale,
        )
        changed = False

        _begin_toolbar_surface(
            imgui,
            "##toolbar_controls_surface",
            layout.controls_width,
            layout.surface_height,
        )
        try:
            changed = (
                self._render_controls_surface(
                    controls_width=layout.controls_width,
                    coordinate_scale=layout.coordinate_scale,
                )
                or changed
            )
        finally:
            _end_toolbar_surface(imgui)

        if not layout.stacked:
            _same_line_with_spacing(imgui, layout.gap)

        status_height = 30.0 * layout.coordinate_scale if layout.stacked else layout.surface_height
        _begin_toolbar_surface(
            imgui,
            "##toolbar_status_surface",
            layout.status_width,
            status_height,
        )
        try:
            imgui.text_disabled("STATUS")
            if layout.stacked:
                imgui.same_line()
            if monitor_snapshot is not None:
                midi = self._midi_session
                render_monitor_status(
                    imgui,
                    monitor_snapshot,
                    midi_status=None if midi is None else midi.status_label,
                    compact=layout.stacked,
                )
            else:
                imgui.text_disabled("No telemetry")
        finally:
            _end_toolbar_surface(imgui)
        return changed

    def _render_shortcut_help(self) -> None:
        """configから読んだParameter GUI shortcut一覧をpopup表示する。"""

        imgui = self._imgui
        if not imgui.button("Shortcuts##shortcut_help"):
            pass
        else:
            imgui.open_popup("Parameter GUI shortcuts##shortcut_help_popup")
        with imgui.begin_popup("Parameter GUI shortcuts##shortcut_help_popup") as popup:
            if not popup.opened:
                return
            for line in shortcut_help_lines(self._shortcut_bindings):
                imgui.text(str(line))

    def _render_midi_clear_notice(self) -> bool:
        """全mapping解除後に、明示的な Undo 導線を全幅で表示する。"""

        notice = self._session.midi_clear_notice
        if notice is None:
            return False

        imgui = self._imgui
        history = self._history
        notice_token = notice.history_token
        if history is not None and notice_token is not None:
            current_token = (int(history.undo_depth), int(self._store.revision))
            if current_token != notice_token:
                # Clear後に別編集が入った場合、そのUndoをClearのUndoと偽装しない。
                self._session.midi_clear_notice = None
                return False

        imgui.push_style_color(imgui.COLOR_TEXT, *PARAMETER_GUI_PALETTE["warning"])
        try:
            imgui.text(notice.message)
        finally:
            imgui.pop_style_color()

        if history is None or not history.can_undo:
            return False
        imgui.same_line()
        if not imgui.button("Undo##midi_clear_notice_undo"):
            return False
        changed = history.undo()
        self._session.midi_clear_notice = None
        return bool(changed)

    def _render_midi_mapping_menu(self) -> bool:
        """MIDI assignment menu を描画する。"""

        imgui = self._imgui
        if imgui.button("MIDI##midi_menu"):
            imgui.open_popup("MIDI mappings##midi_menu_popup")

        changed = False
        with imgui.begin_popup("MIDI mappings##midi_menu_popup") as popup:
            if not popup.opened:
                return False

            assignment_count = _midi_assignment_count(self._store)
            session = self._midi_session
            status = "MIDI OFF" if session is None else session.status_label
            imgui.text_disabled(f"{status}  ·  {assignment_count} mappings")
            imgui.separator()
            reconnect_clicked, _selected = imgui.menu_item(
                "Reconnect##midi_reconnect",
                enabled=session is not None and session.can_reconnect,
            )
            if reconnect_clicked and session is not None:
                session.reconnect()
            clear_frozen_clicked, _selected = imgui.menu_item(
                "Clear frozen snapshot##midi_clear_frozen",
                enabled=session is not None and session.state == "frozen",
            )
            if clear_frozen_clicked and session is not None:
                session.clear_frozen_snapshot()
            clear_clicked, _selected = imgui.menu_item(
                "Clear all mappings##clear_midi_assigns",
                enabled=assignment_count > 0,
            )
            if clear_clicked and assignment_count > 0:
                self._session.midi_learn = MidiLearnState()
                history = self._history
                changed = bool(
                    clear_all_midi_assignments(
                        self._store,
                        history=history,
                    )
                )
                if changed:
                    self._session.midi_clear_notice = MidiClearNotice(
                        message="MIDI mappings cleared",
                        history_token=(
                            None
                            if history is None
                            else (int(history.undo_depth), int(self._store.revision))
                        ),
                    )
        return changed

    def _render_parameter_table_toolbar(self) -> bool:
        """Table固有のfilterとMIDI global commandをtable直上へ配置する。"""

        imgui = self._imgui
        self._session.favorite_keys = favorite_parameter_key_set(self._store)
        state = self._session.filter_state
        imgui.align_text_to_frame_padding()
        imgui.text_disabled("PARAMETERS")

        imgui.same_line()
        coordinate_scale = _window_ui_coordinate_scale(
            self._window,
            ui_scale=float(self._ui_scale),
        )
        imgui.set_next_item_width(260.0 * coordinate_scale)
        query_changed, query = imgui.input_text_with_hint(
            "##parameter_search",
            "Search label, op, arg, source, MIDI CC",
            str(state.query),
        )
        if query_changed:
            state = replace(state, query=str(query))

        imgui.same_line()
        _clicked, self._session.show_inactive_parameters = imgui.checkbox(
            "Show inactive##show_inactive_params",
            bool(self._session.show_inactive_parameters),
        )

        # 詳細 filter は popup にまとめ、主操作行をコンパクトに保つ。
        imgui.same_line()
        enabled_filter_count = sum(
            (
                state.activity != "all",
                bool(state.ui_override_only),
                bool(state.midi_mapped_only),
                bool(state.error_only),
                bool(state.favorite_only),
            )
        )
        filter_button = (
            "Filters" if enabled_filter_count == 0 else f"Filters {enabled_filter_count}"
        )
        if imgui.button(f"{filter_button}##parameter_filter_menu"):
            imgui.open_popup("Parameter filters##parameter_filter_popup")

        def menu_clicked(
            label: str,
            *,
            selected: bool,
            enabled: bool = True,
        ) -> bool:
            clicked, _selected = imgui.menu_item(
                label,
                None,
                bool(selected),
                bool(enabled),
            )
            return bool(clicked)

        with imgui.begin_popup("Parameter filters##parameter_filter_popup") as popup:
            if popup.opened:
                activity_options: tuple[tuple[str, ParameterActivityFilter], ...] = (
                    ("All activity##filter_activity_all", "all"),
                    ("Active only##filter_activity_active", "active"),
                    ("Inactive only##filter_activity_inactive", "inactive"),
                )
                for label, activity in activity_options:
                    if menu_clicked(
                        label,
                        selected=state.activity == activity,
                    ):
                        state = replace(state, activity=activity)
                        if activity == "inactive":
                            # 選択直後に空結果に見えないよう、既存 visibility gate も開く。
                            self._session.show_inactive_parameters = True
                imgui.separator()
                boolean_filters = (
                    ("UI override##filter_ui_override", "ui_override_only"),
                    ("MIDI mapped##filter_midi_mapped", "midi_mapped_only"),
                    ("Error##filter_error", "error_only"),
                    ("Favorite##filter_favorite", "favorite_only"),
                )
                for label, field in boolean_filters:
                    if field == "ui_override_only":
                        selected = state.ui_override_only
                    elif field == "midi_mapped_only":
                        selected = state.midi_mapped_only
                    elif field == "error_only":
                        selected = state.error_only
                    else:
                        selected = state.favorite_only
                    if menu_clicked(label, selected=selected):
                        if field == "ui_override_only":
                            state = replace(state, ui_override_only=not selected)
                        elif field == "midi_mapped_only":
                            state = replace(state, midi_mapped_only=not selected)
                        elif field == "error_only":
                            state = replace(state, error_only=not selected)
                        else:
                            state = replace(state, favorite_only=not selected)

        self._session.filter_state = state
        # MIDI は status へ混ぜず、Filters と同じ主操作群へ置く。
        imgui.same_line()
        changed = self._render_midi_mapping_menu()

        view = parameter_table_view_for_store(
            self._store,
            cache=self._session.table_cache,
            show_inactive_params=bool(self._session.show_inactive_parameters),
            filter_state=state,
            error_keys=self._session.error_keys,
            favorite_keys=self._session.favorite_keys,
        )
        self._session.table_view = view
        imgui.text_disabled(f"{view.filtered_count} / {view.total_count} parameters")
        imgui.same_line()
        imgui.text_disabled(f"{view.hidden_count} hidden")
        imgui.same_line()
        if imgui.button("Expand all##parameter_groups_expand_all"):
            history = self._history
            transaction = (
                history.transaction(source="expand_all_parameter_groups")
                if history is not None
                else nullcontext()
            )
            with transaction:
                changed = (
                    set_all_parameter_groups_collapsed(
                        self._store,
                        view,
                        collapsed=False,
                    )
                    or changed
                )
        imgui.same_line()
        if imgui.button("Collapse all##parameter_groups_collapse_all"):
            history = self._history
            transaction = (
                history.transaction(source="collapse_all_parameter_groups")
                if history is not None
                else nullcontext()
            )
            with transaction:
                changed = (
                    set_all_parameter_groups_collapsed(
                        self._store,
                        view,
                        collapsed=True,
                    )
                    or changed
                )

        imgui.same_line()
        self._render_shortcut_help()
        return changed

    def _remember_parameter_help_row(
        self,
        row: ParameterRow,
        _selected: bool,
    ) -> None:
        """row の hover/focus/select を次 frame の Help pane へ保持する。"""

        self._session.help_row = row

    def _render_reconcile_orphan_control(self) -> bool:
        """曖昧な parameter group の明示 1:1 relink 導線を描画する。"""

        imgui = self._imgui
        model = reconcile_orphan_panel_model(list_reconcile_orphans(self._store))
        self._session.reconcile_model = model

        if model.orphan_count == 0:
            return False

        imgui.text_disabled("RELINK")
        imgui.same_line()
        imgui.text_disabled(
            f"{model.orphan_count} ambiguous groups  ·  {model.candidate_count} saved candidates"
        )
        imgui.same_line()
        if imgui.button("Review##reconcile_orphan_review"):
            imgui.open_popup("Parameter relink##reconcile_orphan_popup")

        request = None
        with imgui.begin_popup("Parameter relink##reconcile_orphan_popup") as popup:
            if popup.opened:
                request = render_reconcile_orphan_popup(
                    imgui,
                    model,
                    error_message=self._session.reconcile_error,
                )
        if request is None:
            return False

        result = apply_reconcile_migration(
            self._store,
            request,
            history=self._history,
        )
        self._session.reconcile_model = result.model
        self._session.reconcile_error = result.error
        if not result.changed:
            return False

        self._session.favorite_keys = favorite_parameter_key_set(self._store)
        self._session.invalidate_table()
        imgui.close_current_popup()
        return True

    def _on_key_press(self, symbol: int | None, modifiers: int) -> None:
        if symbol is None:
            return
        symbol_i = int(symbol)

        io = self._imgui.get_io()
        if bool(io.want_text_input) or bool(io.want_capture_keyboard):
            return

        # R/E/T は押下中だけ直接commitせず、明示preview modeを開始する。
        # Applyまでstoreは変えず、Esc/Cancelはpreviewを捨てるだけにする。
        mode: RangeEditMode | None = None
        if symbol_i == int(self._range_edit_key_r):
            mode = "shift"
        elif symbol_i == int(self._range_edit_key_e):
            mode = "min"
        elif symbol_i == int(self._range_edit_key_t):
            mode = "max"
        elif symbol_i == int(self._range_edit_key_escape):
            self._range_edit_controller.cancel()
            return
        if mode is not None:
            self._range_edit_controller.begin(mode)
            return

        modifier_i = int(modifiers)
        shortcut_mask = int(self._shortcut_modifier_mask)
        if shortcut_mask and modifier_i & shortcut_mask:
            history = self._history
            if history is not None:
                if symbol_i == int(self._history_key_z):
                    if modifier_i & int(self._shortcut_shift_mask):
                        history.redo()
                    else:
                        history.undo()
                elif symbol_i == int(self._history_key_y):
                    history.redo()
            # Cmd/Ctrl を伴う OS/editor shortcut を transport として解釈しない。
            return

        transport = self._transport
        if transport is None:
            return
        is_recording = self._is_recording
        if is_recording is not None and is_recording():
            return
        if symbol_i == int(self._transport_key_space):
            transport.toggle()
        elif symbol_i == int(self._transport_key_home):
            transport.reset()
        elif symbol_i == int(self._transport_key_left):
            transport.step_frame(fps=self._transport_fps, frames=-1)
        elif symbol_i == int(self._transport_key_right):
            transport.step_frame(fps=self._transport_fps, frames=1)
        elif symbol_i == int(self._transport_key_slower):
            transport.set_speed(max(0.125, transport.speed / 2.0))
        elif symbol_i == int(self._transport_key_faster):
            transport.set_speed(min(8.0, transport.speed * 2.0))

    def _on_deactivate(self) -> None:
        self._range_edit_controller.cancel()

    @property
    def parameter_edit_active(self) -> bool:
        """直前の GUI frame で ImGui item が操作中なら True。"""

        return bool(self._session.parameter_edit_active)

    def _maybe_preview_range_edit_by_midi(self) -> bool:
        """新しいCC差分をstore非破壊のRange Edit previewへ反映する。"""

        session = self._midi_session
        if session is None:
            return False

        last = session.last_cc_change
        if last is None:
            return False

        sequence, cc = last
        return self._range_edit_controller.preview_midi_change(
            sequence=sequence,
            cc=cc,
            value=session.value_for_cc(cc),
            blocked=self._session.midi_learn.active_target is not None,
        )

    def _render_range_edit_mode(self) -> bool:
        """明示Range Edit modeの対象、preview、Apply/Cancelを描画する。"""

        controller = self._range_edit_controller
        mode = controller.mode
        if mode is None:
            return False
        imgui = self._imgui
        mode_label = {"shift": "SHIFT", "min": "MIN", "max": "MAX"}[mode]
        imgui.text(f"RANGE EDIT · {mode_label}")
        edit = controller.session
        if edit is None:
            imgui.text_disabled("Move a mapped MIDI control to choose linked parameters.")
        else:
            imgui.text_disabled(
                f"CC {edit.cc} · {len(edit.targets)} linked parameter"
                + ("s" if len(edit.targets) != 1 else "")
            )
            for target in edit.targets[:4]:
                lo, hi = target.pending_range
                imgui.text_disabled(f"{target.label}: {lo:g} .. {hi:g}")
            if len(edit.targets) > 4:
                imgui.text_disabled(f"+ {len(edit.targets) - 4} more")

        applied = False
        if imgui.button("Apply##range_edit_apply") and edit is not None:
            applied = bool(controller.commit())
        imgui.same_line()
        if imgui.button("Cancel##range_edit_cancel"):
            controller.cancel()
        _section_separator(imgui)
        return applied

    def _render_bottom_drawer(
        self,
        *,
        geometry: BottomDrawerGeometry,
        monitor_snapshot: MonitorSnapshot | None,
        monitor: TelemetrySource | None,
    ) -> None:
        """Help と可変長 runtime details を固定高 pane 内に描画する。"""

        imgui = self._imgui
        _begin_toolbar_surface(
            imgui,
            "##parameter_help_drawer",
            geometry.help_width,
            geometry.height,
        )
        try:
            render_parameter_help_pane(
                imgui,
                self._session.help_row,
            )
        finally:
            _end_toolbar_surface(imgui)

        _same_line_with_spacing(imgui, geometry.gap)
        _begin_toolbar_surface(
            imgui,
            "##runtime_details_drawer",
            geometry.runtime_width,
            geometry.height,
        )
        try:
            imgui.text_disabled("RUNTIME")
            if monitor_snapshot is None:
                imgui.text_disabled("No runtime details")
                return

            alert_lines = monitor_alert_lines(monitor_snapshot)
            render_monitor_alerts(imgui, monitor_snapshot)
            render_profiler_panel(imgui, monitor_snapshot.profiler)
            render_diagnostics_panel(
                imgui,
                monitor_snapshot.diagnostics,
                center=None if monitor is None else monitor.diagnostic_center,
            )
            if (
                not alert_lines
                and monitor_snapshot.profiler is None
                and not monitor_snapshot.diagnostics
            ):
                imgui.text_disabled("No alerts or diagnostics")
        finally:
            _end_toolbar_surface(imgui)

    def _sync_font_for_window(self) -> None:
        """ウィンドウの backing scale に合わせてフォントを同期する。"""

        if self._custom_font_path is None:
            return

        backing_scale = _compute_window_backing_scale(self._window)
        effective_config = self._effective_config
        config_key: tuple[object, ...] = (
            effective_config.config_path,
            effective_config.parameter_gui_fallback_font_japanese,
            effective_config.font_dirs,
        )
        sync_key: tuple[object, ...] = (
            float(backing_scale),
            self._custom_font_path,
            float(self._font_size_base_px),
            config_key,
        )
        # フォント探索/resolve はディスクアクセスを含み得る。安定フレームでは行わず、
        # backing scale または runtime config が変わったときだけ再同期する。
        if self._font_sync_key == sync_key:
            return
        fallback = _gui_fallback_font_path_for_japanese(effective_config)
        favorite_font = _favorite_glyph_font_path()

        io = self._imgui.get_io()
        io.fonts.clear()
        font_px = float(self._font_size_base_px * backing_scale)
        io.fonts.add_font_from_file_ttf(
            str(self._custom_font_path),
            float(font_px),
            glyph_ranges=io.fonts.get_glyph_ranges_default(),
        )

        if fallback is not None and fallback.is_file():
            if fallback.resolve() != self._custom_font_path.resolve():
                font_config = self._imgui.core.FontConfig(merge_mode=True)
                io.fonts.add_font_from_file_ttf(
                    str(fallback),
                    float(font_px),
                    font_config=font_config,
                    glyph_ranges=io.fonts.get_glyph_ranges_japanese(),
                )

        if favorite_font is not None and favorite_font.is_file():
            # Japanese range には U+2605/U+2606 が含まれないため、同梱 Noto から
            # favorite icon の2 glyphだけを明示的に追加する。
            favorite_ranges = self._imgui.core.GlyphRanges((0x2605, 0x2606, 0))
            favorite_cfg = self._imgui.core.FontConfig(merge_mode=True)
            io.fonts.add_font_from_file_ttf(
                str(favorite_font),
                float(font_px),
                font_config=favorite_cfg,
                glyph_ranges=favorite_ranges,
            )
            self._favorite_glyph_ranges = favorite_ranges

        self._backend.refresh_font_texture()

        self._font_backing_scale = backing_scale
        self._font_fallback_path_for_japanese = fallback
        self._favorite_glyph_font_path = favorite_font
        self._font_sync_key = sync_key

    def draw_frame(self) -> bool:
        """確定済み config を束縛して 1 フレーム分の UI を描画する。"""

        with (
            bind_runtime_config(self._effective_config),
            bind_evaluation_config(
                EvaluationConfig(font_dirs=self._effective_config.font_dirs)
            ),
        ):
            return self._draw_frame()

    def _render_parameter_workspace(
        self,
        *,
        content_width: float,
        monitor_snapshot: MonitorSnapshot | None,
    ) -> bool:
        """Parameter table と固定 bottom drawer を順に描画する。"""

        imgui = self._imgui
        table_view = self._session.table_view
        if table_view is None:
            raise RuntimeError("parameter table toolbar did not publish its view")

        coordinate_scale = _window_ui_coordinate_scale(
            self._window,
            ui_scale=float(self._ui_scale),
        )
        drawer_geometry = compute_bottom_drawer_geometry(
            content_width,
            coordinate_scale=coordinate_scale,
        )
        _section_separator(imgui)

        drawer_spacing = _vertical_item_spacing(imgui)
        imgui.begin_child(
            "##parameter_table_scroll",
            0,
            -(drawer_geometry.height + drawer_spacing),
            border=False,
        )
        try:
            table_result = render_store_parameter_table(
                self._store,
                table_view=table_view,
                widget_state=self._session.widgets,
                metric_scale=coordinate_scale,
                midi_learn_state=self._session.midi_learn,
                midi_last_cc_change=(
                    None if self._midi_session is None else self._midi_session.last_cc_change
                ),
                on_help_row=self._remember_parameter_help_row,
                history=self._history,
            )
        finally:
            imgui.end_child()

        if table_result.midi_learn_state is not None:
            self._session.midi_learn = table_result.midi_learn_state
        if table_result.changed:
            self._session.invalidate_table()

        self._render_bottom_drawer(
            geometry=drawer_geometry,
            monitor_snapshot=monitor_snapshot,
            monitor=self._monitor,
        )
        return bool(table_result.changed)

    def _render_frame_panels(
        self,
        *,
        monitor_snapshot: MonitorSnapshot | None,
    ) -> bool:
        """上から下への panel 順序を固定し、store 変更を集約する。"""

        content_width = float(content_region_available_width(self._imgui))
        changes = [
            self._render_toolbar_area(
                content_width=content_width,
                monitor_snapshot=monitor_snapshot,
            ),
            self._render_midi_clear_notice(),
            # MIDI global command は通常の parameter edit と coalesce させない。
            self._render_parameter_table_toolbar(),
            self._render_reconcile_orphan_control(),
        ]
        self._maybe_preview_range_edit_by_midi()
        changes.append(self._render_range_edit_mode())
        changes.append(
            self._render_parameter_workspace(
                content_width=content_width,
                monitor_snapshot=monitor_snapshot,
            )
        )
        return any(changes)

    def _draw_frame(self) -> bool:
        """1 フレーム分の GUI を描画し、変更があれば store に反映する。

        `flip()` は呼ばない。呼び出し側が `window.flip()` を担当する。
        """

        # close() 済みなら何もしない。
        if self._closed:
            return False

        import time

        # 前フレームからの経過秒（ImGui の IO に渡す）。
        now = time.monotonic()
        dt = now - self._prev_time
        self._prev_time = now

        imgui = self._imgui

        # font atlas 操作も backend が所有する context 上で行う。
        self._backend.activate_context()
        self._sync_font_for_window()

        # 注: 呼び出し側（pyglet.window.Window.draw）が事前に `self._window.switch_to()` 済みである前提。
        # ここで switch_to() を呼ぶと責務が分散し、点滅の原因（複数箇所での画面更新）になりやすい。

        # 注: imgui.integrations.pyglet の process_inputs() は内部で pyglet.clock.tick() を呼ぶ。
        # `pyglet.app.run()` 駆動時にこれを呼ぶと clock が二重に進みやすいので、ここでは呼ばない。
        # 入力イベント自体は pyglet のイベント配送で io に反映される前提。

        self._backend.begin_frame(dt)

        # GUI は 1 ウィンドウで全面表示する（位置/サイズ固定）。
        imgui.set_next_window_position(0, 0)
        imgui.set_next_window_size(self._window.width, self._window.height)
        imgui.begin(
            self._title,
            flags=imgui.WINDOW_NO_RESIZE | imgui.WINDOW_NO_COLLAPSE | imgui.WINDOW_NO_TITLE_BAR,
        )

        monitor = self._monitor
        monitor_snapshot = None if monitor is None else monitor.snapshot()
        changed_any = self._render_frame_panels(monitor_snapshot=monitor_snapshot)
        history = self._history
        parameter_edit_active = bool(imgui.is_any_item_active())
        self._session.parameter_edit_active = parameter_edit_active
        if history is not None and not parameter_edit_active:
            history.break_coalescing()
        imgui.end()

        self._backend.render()
        # `flip()` は MultiWindowLoop が担当する（ここでは呼ばない）。
        return bool(changed_any)

    def close(self) -> None:
        """GUI を終了し、コンテキストとウィンドウを破棄する。"""

        # 二重 close を許容する（呼び出し側の finally から安全に呼べるようにする）。
        if getattr(self, "_closed", False):
            return
        self._closed = True

        session = getattr(self, "_session", None)
        if isinstance(session, ParameterGuiSessionState):
            session.close()

        errors = CleanupErrors()

        window = getattr(self, "_window", None)
        backend = getattr(self, "_backend", None)
        if backend is not None:
            errors.attempt(backend.close)

        if window is not None:
            errors.attempt(lambda: close_pyglet_window(window))
        errors.raise_if_any()
