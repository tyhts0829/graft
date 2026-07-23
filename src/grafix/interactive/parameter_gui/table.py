# どこで: `src/grafix/interactive/parameter_gui/table.py`。
# 何を: ParameterRow を 4 列テーブルとして描画し、更新後の行モデルを返す。
# なぜ: テーブルの UI レイアウトを 1 箇所に閉じ込め、store 反映や backend と分離するため。

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType, ModuleType
from typing import Literal, assert_never

from grafix.core.parameters.collapsed_header import (
    CollapsedHeaderKey,
    STYLE_COLLAPSED_HEADER_KEY,
    effect_chain_collapsed_header_key,
    preset_collapsed_header_key,
    primitive_collapsed_header_key,
)
from grafix.core.parameters.effects import EffectStepKey
from grafix.core.parameters.identity import group_key, identity_string
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.source import ValueSource
from grafix.core.parameters.view import ParameterRow

from .catalog import ParameterGuiCatalog, current_parameter_gui_catalog
from .group_blocks import GroupBlockLayout
from .grouping import GroupId, GroupType
from .labeling import (
    format_contextual_row_label,
    humanize_identifier,
    operation_display_name,
)
from .midi_learn import MidiLearnCommand, MidiLearnState, transition_midi_learn
from .pyglet_backend import content_region_available_width
from .rules import RowUiRules, ui_rules_for_row
from .session_state import WidgetSessionState
from .snippet import snippet_for_block
from .table_model import EffectChainTableState
from .theme import PARAMETER_GUI_PALETTE, source_badge_color
from .widgets import render_value_widget

SNIPPET_POPUP_WINDOW_SIZE_PX = (960.0, 720.0)
SNIPPET_POPUP_VIEWPORT_MARGIN_PX = 24.0
EFFECT_STEP_DRAG_PAYLOAD_TYPE = "_GRAFIX_EFFECT_STEP"

# CODE/UI の選択位置を全行で揃える。bool 行も同じ幅の固定 UI 表示にして、
# parameter label の開始位置が kind によって揺れないようにする。
SOURCE_CODE_SEGMENT_WIDTH_PX = 40.0
SOURCE_UI_SEGMENT_WIDTH_PX = 24.0
SOURCE_ACTIONS_WIDTH_PX = 14.0
SOURCE_SEGMENT_GAP_PX = 1.0
SOURCE_LABEL_GAP_PX = 6.0
SOURCE_SELECTOR_TOTAL_WIDTH_PX = (
    SOURCE_CODE_SEGMENT_WIDTH_PX
    + SOURCE_UI_SEGMENT_WIDTH_PX
    + SOURCE_ACTIONS_WIDTH_PX
    + SOURCE_SEGMENT_GAP_PX * 2.0
)
SOURCE_SELECTOR_SHORT_BREAKPOINT_PX = 168.0
SOURCE_CODE_SHORT_WIDTH_PX = 18.0
SOURCE_UI_SHORT_WIDTH_PX = 18.0
SOURCE_ACTIONS_SHORT_WIDTH_PX = 14.0
SOURCE_SELECTOR_SHORT_TOTAL_WIDTH_PX = (
    SOURCE_CODE_SHORT_WIDTH_PX
    + SOURCE_UI_SHORT_WIDTH_PX
    + SOURCE_ACTIONS_SHORT_WIDTH_PX
    + SOURCE_SEGMENT_GAP_PX * 2.0
)

PARAMETER_TABLE_SOURCE_COLUMN_WIDTH_PX = 250.0
PARAMETER_TABLE_RANGE_COLUMN_WIDTH_PX = 130.0
PARAMETER_TABLE_MIDI_COLUMN_WIDTH_PX = 165.0

GROUP_HEADER_BASE_COLORS_RGBA: dict[str, tuple[int, int, int, int]] = {
    "style": (104, 164, 255, 94),
    "primitive": (229, 138, 125, 94),
    "preset": (170, 140, 255, 94),
    "effect": (107, 203, 149, 94),
}


@dataclass(frozen=True, slots=True)
class EffectOrderCommand:
    """描画後にParameterGUIがcommitするeffect順序操作。"""

    kind: Literal["move", "reset"]
    chain_id: str
    source: EffectStepKey | None = None
    target: EffectStepKey | None = None
    placement: Literal["before", "after"] | None = None

    def __post_init__(self) -> None:
        identity_string(self.chain_id, name="EffectOrderCommand.chain_id")
        if self.kind == "move":
            if self.source is None or self.target is None:
                raise ValueError("move command には source と target が必要です")
            group_key(self.source, name="EffectOrderCommand.source")
            group_key(self.target, name="EffectOrderCommand.target")
            if self.placement not in {"before", "after"}:
                raise ValueError("move command には placement が必要です")
            return
        if self.kind == "reset":
            if any(value is not None for value in (self.source, self.target, self.placement)):
                raise ValueError("reset command に move 引数は指定できません")
            return
        raise ValueError(f"unknown effect order command: {self.kind!r}")

    @classmethod
    def move(
        cls,
        *,
        chain_id: str,
        source: EffectStepKey,
        target: EffectStepKey,
        placement: Literal["before", "after"],
    ) -> EffectOrderCommand:
        """step移動commandを返す。"""

        return cls(
            kind="move",
            chain_id=identity_string(chain_id, name="chain_id"),
            source=group_key(source, name="source"),
            target=group_key(target, name="target"),
            placement=placement,
        )

    @classmethod
    def reset(cls, *, chain_id: str) -> EffectOrderCommand:
        """コード順へのreset commandを返す。"""

        return cls(
            kind="reset",
            chain_id=identity_string(chain_id, name="chain_id"),
        )


@dataclass(frozen=True, slots=True)
class TableRenderInput:
    """table renderer が読む一 frame 分の immutable snapshot。"""

    group_layout: tuple[GroupBlockLayout, ...]
    model_rows: tuple[ParameterRow, ...]
    catalog: ParameterGuiCatalog
    collapsed_headers: frozenset[CollapsedHeaderKey]
    metric_scale: float | None = None
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]] | None = None
    effect_chain_state_by_id: Mapping[str, EffectChainTableState] | None = None
    last_effective_by_key: Mapping[ParameterKey, object] | None = None
    last_source_by_key: Mapping[ParameterKey, ValueSource] | None = None
    raw_label_by_site: Mapping[tuple[str, str], str] | None = None
    midi_learn_state: MidiLearnState | None = None
    midi_last_cc_change: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if type(self.catalog) is not ParameterGuiCatalog:
            raise TypeError("catalog must be an exact ParameterGuiCatalog")
        if self.midi_learn_state is not None and not isinstance(
            self.midi_learn_state,
            MidiLearnState,
        ):
            raise TypeError("midi_learn_state must be a MidiLearnState or None")
        object.__setattr__(self, "group_layout", tuple(self.group_layout))
        object.__setattr__(self, "model_rows", tuple(self.model_rows))
        object.__setattr__(
            self,
            "collapsed_headers",
            frozenset(self.collapsed_headers),
        )
        for name in (
            "step_info_by_site",
            "effect_chain_state_by_id",
            "last_effective_by_key",
            "last_source_by_key",
            "raw_label_by_site",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, MappingProxyType(dict(value)))


@dataclass(frozen=True, slots=True)
class TableEdits:
    """table renderer が返す immutable edit 集合。"""

    rows: tuple[ParameterRow, ...]
    collapsed_headers: frozenset[CollapsedHeaderKey]
    midi_learn_state: MidiLearnState | None
    effect_order_commands: tuple[EffectOrderCommand, ...] = ()

    def __post_init__(self) -> None:
        if self.midi_learn_state is not None and not isinstance(
            self.midi_learn_state,
            MidiLearnState,
        ):
            raise TypeError("midi_learn_state must be a MidiLearnState or None")
        object.__setattr__(self, "rows", tuple(self.rows))
        object.__setattr__(
            self,
            "collapsed_headers",
            frozenset(self.collapsed_headers),
        )
        object.__setattr__(
            self,
            "effect_order_commands",
            tuple(self.effect_order_commands),
        )


@dataclass(slots=True)
class _TableRenderState:
    """section helper 間で引き継ぐ一 frame の描画結果。"""

    collapsed_headers: set[CollapsedHeaderKey]
    midi_learn_state: MidiLearnState | None
    updated_rows: list[ParameterRow]
    effect_order_commands: list[EffectOrderCommand]
    drew_column_headers: bool = False
    want_open_snippet_popup: bool = False
    snippet_popup_text_new: str | None = None


def _encode_effect_step_drag_payload(
    chain_id: str,
    step: EffectStepKey,
) -> bytes:
    """drag payloadを小さなUTF-8 JSONとして返す。"""

    normalized_step = group_key(step, name="step")
    return json.dumps(
        [
            identity_string(chain_id, name="chain_id"),
            normalized_step[0],
            normalized_step[1],
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_effect_step_drag_payload(
    payload: object,
) -> tuple[str, EffectStepKey] | None:
    """自分が生成したdrag payloadだけを正規化して返す。"""

    if not isinstance(payload, (bytes, bytearray)):
        return None
    try:
        decoded = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(decoded, list)
        or len(decoded) != 3
        or not all(isinstance(item, str) and item for item in decoded)
    ):
        return None
    return decoded[0], (decoded[1], decoded[2])


def _effect_step_drop_placement(
    *,
    mouse_y: float,
    item_top: float,
    item_bottom: float,
) -> Literal["before", "after"]:
    """target見出しの上半分/下半分をbefore/afterへ変換する。"""

    midpoint = (float(item_top) + float(item_bottom)) * 0.5
    return "before" if float(mouse_y) < midpoint else "after"


def _clamp01(x: float) -> float:
    """0..1 に clamp した値を返す。"""

    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return float(x)


def _rgba01_from_rgba255(
    rgba: tuple[int, int, int, int],
) -> tuple[float, float, float, float]:
    """0..255 の RGBA を 0..1 の RGBA に変換して返す。"""

    r, g, b, a = rgba
    return (
        _clamp01(float(r) / 255.0),
        _clamp01(float(g) / 255.0),
        _clamp01(float(b) / 255.0),
        _clamp01(float(a) / 255.0),
    )


def _derive_header_colors(
    base: tuple[float, float, float, float],
) -> tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]:
    """(normal, hovered, active) のヘッダ色を base から作る。"""

    normal = base

    def _tint_towards_white(
        rgba: tuple[float, float, float, float],
        *,
        t: float,
        alpha_add: float,
    ) -> tuple[float, float, float, float]:
        t = _clamp01(float(t))
        r, g, b, a = rgba
        return (
            _clamp01(r * (1.0 - t) + 1.0 * t),
            _clamp01(g * (1.0 - t) + 1.0 * t),
            _clamp01(b * (1.0 - t) + 1.0 * t),
            _clamp01(a + float(alpha_add)),
        )

    # hover/active の色は base から自動導出する（白方向へ補間 + alpha を少し増やす）。
    hovered = _tint_towards_white(base, t=0.12, alpha_add=0.08)
    active = _tint_towards_white(base, t=0.22, alpha_add=0.14)
    return normal, hovered, active


def _header_kind_for_group_id(group_id: GroupId) -> str:
    """group_id からヘッダ種別（style/preset/primitive/effect）を返す。"""

    group_type = group_id[0]
    if group_type is GroupType.EFFECT_CHAIN:
        return "effect"
    if group_type is GroupType.STYLE:
        return "style"
    if group_type is GroupType.PRESET:
        return "preset"
    if group_type is GroupType.PRIMITIVE:
        return "primitive"
    assert_never(group_type)


def _collapse_key_for_group(
    group_id: GroupId,
    first_row: ParameterRow | None,
) -> CollapsedHeaderKey | None:
    """group id と先頭行から折りたたみ永続キーを返す。"""

    group_type = group_id[0]
    if group_type is GroupType.STYLE:
        return STYLE_COLLAPSED_HEADER_KEY
    if group_type is GroupType.EFFECT_CHAIN:
        chain_id = identity_string(group_id[1], name="effect chain group id")
        return effect_chain_collapsed_header_key(chain_id)
    if group_type is GroupType.PRESET:
        if first_row is None:
            return None
        return preset_collapsed_header_key((first_row.op, first_row.site_id))
    if group_type is GroupType.PRIMITIVE:
        if first_row is None:
            return None
        return primitive_collapsed_header_key((first_row.op, first_row.site_id))
    assert_never(group_type)


def parameter_group_collapse_keys(
    rows: list[ParameterRow],
    *,
    group_layout: Sequence[GroupBlockLayout],
) -> tuple[CollapsedHeaderKey, ...]:
    """現在の行モデルから Expand/Collapse all 対象の header key を返す。"""

    keys: list[CollapsedHeaderKey] = []
    seen: set[CollapsedHeaderKey] = set()
    for block in group_layout:
        if not block.header:
            continue
        first_row = None if not block.items else rows[block.items[0].row_index]
        key = _collapse_key_for_group(block.group_id, first_row)
        if key is None or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return tuple(keys)


def _row_visible_label(
    row: ParameterRow,
    *,
    catalog: ParameterGuiCatalog | None = None,
) -> str:
    """行の表示ラベル（`op#ordinal arg`）を返す。"""

    selected_catalog = current_parameter_gui_catalog() if catalog is None else catalog
    op = row.op
    entry = selected_catalog.resolve(op)
    if entry is not None and entry.kind == "preset":
        op = entry.call_name
    display_arg = row.display_name or row.arg
    return format_contextual_row_label(op, int(row.ordinal), display_arg)


def _row_id(row: ParameterRow) -> str:
    """ImGui の `push_id()` 用に、行の安定 ID を返す。"""

    return f"{row.op}#{row.ordinal}:{row.arg}"


def _set_item_tooltip(imgui: ModuleType, text: str) -> None:
    """直前の item がhoverまたはkeyboard focus中ならtooltipを設定する。"""

    if imgui.is_item_hovered() or imgui.is_item_focused():
        imgui.set_tooltip(str(text))


def _notify_parameter_help(
    imgui: ModuleType,
    row: ParameterRow,
    callback: Callable[[ParameterRow, bool], None] | None,
) -> None:
    """直前 item の hover/focus/select を Help pane へ通知する。"""

    if callback is None:
        return

    selected = bool(imgui.is_item_clicked())
    if (
        selected
        or bool(imgui.is_item_hovered())
        or bool(imgui.is_item_focused())
        or bool(imgui.is_item_active())
    ):
        callback(row, selected)


def _imgui_metric_scale(imgui: ModuleType) -> float:
    """font atlas の座標系に合わせる寸法倍率を返す。

    Retina では ImGui の content width / font が backing pixel 単位になるため、
    固定寸法と breakpoint も同じ倍率へ揃える。通常 DPI と簡易 test double は
    1.0 のままにする。
    """

    text_height = float(imgui.calc_text_size("CODE")[1])
    return min(3.0, max(1.0, text_height / 14.0))


def _setup_parameter_table_columns(
    imgui: ModuleType,
    *,
    metric_scale: float | None = None,
) -> None:
    """固定 3 列と、残り幅を受け取る Value 列を設定する。"""

    if metric_scale is None:
        scale = _imgui_metric_scale(imgui)
    else:
        scale = float(metric_scale)
        if not math.isfinite(scale) or scale <= 0.0:
            raise ValueError("metric_scale は finite な正の値である必要がある")
    fixed_flags = imgui.TABLE_COLUMN_WIDTH_FIXED | imgui.TABLE_COLUMN_NO_RESIZE
    imgui.table_setup_column(
        "  Source / Parameter",
        fixed_flags,
        PARAMETER_TABLE_SOURCE_COLUMN_WIDTH_PX * scale,
    )
    imgui.table_setup_column(
        "  Value",
        imgui.TABLE_COLUMN_WIDTH_STRETCH,
        1.0,
    )
    imgui.table_setup_column(
        "  Range",
        fixed_flags,
        PARAMETER_TABLE_RANGE_COLUMN_WIDTH_PX * scale,
    )
    imgui.table_setup_column(
        "  MIDI",
        fixed_flags,
        PARAMETER_TABLE_MIDI_COLUMN_WIDTH_PX * scale,
    )


def _midi_mapping_summary(
    *,
    kind: str,
    cc_key: int | tuple[int | None, int | None, int | None] | None,
) -> str | None:
    """source tooltip 用に、割当済み MIDI CC を短く説明する。"""

    if isinstance(cc_key, int):
        return f"MIDI CC {int(cc_key)}"
    if not isinstance(cc_key, tuple):
        return None

    components = ("R", "G", "B") if str(kind) == "rgb" else ("X", "Y", "Z")
    assigned = [
        f"{components[index]}:CC {int(cc)}" for index, cc in enumerate(cc_key) if cc is not None
    ]
    if not assigned:
        return None
    return "MIDI " + " / ".join(assigned)


def _source_selector_tooltip(
    *,
    source: str,
    kind: str,
    cc_key: int | tuple[int | None, int | None, int | None] | None,
    last_source: ValueSource | None,
) -> str:
    """CODE/UI selector の意味を、MIDI priority を含めて説明する。"""

    source_upper = str(source).upper()
    mapping = _midi_mapping_summary(kind=kind, cc_key=cc_key)
    if mapping is not None:
        if last_source == "midi_live":
            activity = " is controlled by live MIDI now"
        elif last_source == "midi_frozen":
            activity = " is controlled by a frozen saved MIDI value"
        else:
            activity = " is assigned"
        return (
            f"{mapping}{activity}. {source_upper} is the fallback; "
            "switching source keeps the MIDI mapping."
        )
    if source_upper == "UI":
        return "Use the value edited in the Inspector."
    return "Use the value defined in code."


def _source_segment_style(
    source: str,
    *,
    active: bool,
) -> tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]:
    """source segment の (button, hover, pressed, text) 色を返す。"""

    if not active:
        return (
            PARAMETER_GUI_PALETTE["surface"],
            PARAMETER_GUI_PALETTE["frame_hovered"],
            PARAMETER_GUI_PALETTE["frame_active"],
            PARAMETER_GUI_PALETTE["text_muted"],
        )

    source_color = source_badge_color(source)
    red, green, blue, _alpha = source_color
    # active source は低彩度の面 + source 色の文字で示す。全行を強い青で
    # 塗りつぶさず、inactive の暗い面との「塗り形状」の差も残す。
    return (
        (red, green, blue, 0.24),
        (red, green, blue, 0.34),
        (red, green, blue, 0.44),
        source_color,
    )


def _render_source_segment_button(
    imgui: ModuleType,
    *,
    source: str,
    visible_label: str | None = None,
    active: bool,
    width: float,
) -> bool:
    """選択状態を面・色・文字で示す source segment button を描画する。"""

    colors = _source_segment_style(source, active=bool(active))
    color_indices = (
        imgui.COLOR_BUTTON,
        imgui.COLOR_BUTTON_HOVERED,
        imgui.COLOR_BUTTON_ACTIVE,
        imgui.COLOR_TEXT,
    )
    for color_index, color in zip(color_indices, colors, strict=True):
        imgui.push_style_color(color_index, *color)
    metric_scale = _imgui_metric_scale(imgui)
    frame_padding_y = float(imgui.get_style().frame_padding[1])
    # 横paddingだけを詰め、40px CODE segmentでも文字をclipしない。
    imgui.push_style_var(
        imgui.STYLE_FRAME_PADDING,
        (2.0 * metric_scale, frame_padding_y),
    )
    try:
        # ``##source_*`` を固定し、active state が変わっても ImGui ID を保つ。
        visible = str(source) if visible_label is None else str(visible_label)
        return bool(imgui.button(f"{visible}##source_{source.lower()}", float(width)))
    finally:
        imgui.pop_style_var()
        imgui.pop_style_color(len(colors))


def _render_midi_button(
    imgui: ModuleType,
    *,
    label: str,
    width: float,
    driving: bool,
) -> bool:
    """MIDI item を描画する。入力駆動中は amber の LIVE chip として示す。"""

    warning = PARAMETER_GUI_PALETTE["source_midi"]
    red, green, blue, _alpha = warning
    colors = (
        (red, green, blue, 0.28),
        (red, green, blue, 0.38),
        (red, green, blue, 0.48),
        warning,
    )
    color_indices = (
        imgui.COLOR_BUTTON,
        imgui.COLOR_BUTTON_HOVERED,
        imgui.COLOR_BUTTON_ACTIVE,
        imgui.COLOR_TEXT,
    )
    if driving:
        for color_index, color in zip(color_indices, colors, strict=True):
            imgui.push_style_color(color_index, *color)
    try:
        return bool(imgui.button(str(label), float(width)))
    finally:
        if driving:
            imgui.pop_style_color(len(colors))


def _render_source_actions_menu(
    imgui: ModuleType,
    *,
    reset_available: bool,
    width: float,
) -> bool:
    """visible な source menu を描画し、明示 reset が選ばれたら True を返す。"""

    metric_scale = _imgui_metric_scale(imgui)
    frame_padding_y = float(imgui.get_style().frame_padding[1])
    imgui.push_style_var(
        imgui.STYLE_FRAME_PADDING,
        (2.0 * metric_scale, frame_padding_y),
    )
    try:
        clicked_menu = bool(imgui.button("v##source_actions", float(width)))
    finally:
        imgui.pop_style_var()
    _set_item_tooltip(imgui, "Source actions")
    if clicked_menu:
        imgui.open_popup("Source actions##source_actions_popup")

    reset_to_code = False
    with imgui.begin_popup("Source actions##source_actions_popup") as popup:
        if popup.opened:
            imgui.text_disabled("Source")
            imgui.separator()
            clicked_reset, _selected = imgui.menu_item(
                "Reset to CODE and clear MIDI##reset_to_code",
                None,
                False,
                bool(reset_available),
            )
            if clicked_reset:
                reset_to_code = True
                imgui.close_current_popup()
    return reset_to_code


def _render_label_cell(
    imgui: ModuleType,
    *,
    row_label: str,
    kind: str,
    override: bool,
    cc_key: int | tuple[int | None, int | None, int | None] | None,
    last_source: ValueSource | None = None,
) -> tuple[bool, bool, bool]:
    """source + label を描画し、(source変更, override, 明示reset) を返す。"""

    imgui.table_set_column_index(0)
    cell_width = content_region_available_width(imgui)
    metric_scale = _imgui_metric_scale(imgui)
    compact = cell_width < (SOURCE_SELECTOR_SHORT_BREAKPOINT_PX * metric_scale)
    code_width = (
        SOURCE_CODE_SHORT_WIDTH_PX if compact else SOURCE_CODE_SEGMENT_WIDTH_PX
    ) * metric_scale
    ui_width = (SOURCE_UI_SHORT_WIDTH_PX if compact else SOURCE_UI_SEGMENT_WIDTH_PX) * metric_scale
    actions_width = (
        SOURCE_ACTIONS_SHORT_WIDTH_PX if compact else SOURCE_ACTIONS_WIDTH_PX
    ) * metric_scale
    segment_gap = SOURCE_SEGMENT_GAP_PX * metric_scale
    label_gap = SOURCE_LABEL_GAP_PX * metric_scale
    override_out = bool(override)
    source_changed = False
    reset_to_code = False

    code_clicked = _render_source_segment_button(
        imgui,
        source="CODE",
        visible_label="C" if compact else "CODE",
        active=not override_out,
        width=code_width,
    )
    _set_item_tooltip(
        imgui,
        _source_selector_tooltip(
            source="CODE",
            kind=kind,
            cc_key=cc_key,
            last_source=last_source,
        ),
    )
    if code_clicked and override_out:
        override_out = False
        source_changed = True

    imgui.same_line(0.0, segment_gap)
    ui_clicked = _render_source_segment_button(
        imgui,
        source="UI",
        visible_label="U" if compact else "UI",
        active=override_out,
        width=ui_width,
    )
    _set_item_tooltip(
        imgui,
        _source_selector_tooltip(
            source="UI",
            kind=kind,
            cc_key=cc_key,
            last_source=last_source,
        ),
    )
    if ui_clicked and not override_out:
        override_out = True
        source_changed = True

    imgui.same_line(0.0, segment_gap)
    reset_to_code = _render_source_actions_menu(
        imgui,
        reset_available=bool(override_out or cc_key is not None),
        width=actions_width,
    )

    imgui.same_line(0.0, label_gap)
    imgui.text(str(row_label))
    return source_changed, override_out, reset_to_code


def _render_favorite_toggle(
    imgui: ModuleType,
    favorite: bool,
) -> tuple[bool, bool]:
    """label cell 末尾に favorite/pin を描画して更新値を返す。"""

    imgui.same_line(0.0, 4.0 * _imgui_metric_scale(imgui))
    label = "★##favorite" if favorite else "☆##favorite"
    clicked = bool(imgui.small_button(label))
    _set_item_tooltip(
        imgui,
        "Remove from favorites" if favorite else "Pin to favorites",
    )
    return clicked, (not favorite if clicked else bool(favorite))


def _render_control_cell(
    imgui: ModuleType,
    row: ParameterRow,
    *,
    widget_state: WidgetSessionState,
) -> tuple[bool, object]:
    """control 列を描画し、(changed, ui_value) を返す。"""

    imgui.table_set_column_index(1)
    imgui.set_next_item_width(-1)  # 残り幅いっぱい
    return render_value_widget(row, state=widget_state)


def _draw_effect_step_insertion_line(
    imgui: ModuleType,
    *,
    item_min: tuple[float, float],
    item_max: tuple[float, float],
    placement: Literal["before", "after"],
) -> None:
    """drop previewの水平挿入線を描画する。"""

    draw_list = imgui.get_window_draw_list()
    window_position = imgui.get_window_position()
    region_min = imgui.get_window_content_region_min()
    region_max = imgui.get_window_content_region_max()
    x_min = float(window_position[0]) + float(region_min[0])
    x_max = float(window_position[0]) + float(region_max[0])
    y = float(item_min[1]) if placement == "before" else float(item_max[1])
    color = imgui.get_color_u32_rgba(*PARAMETER_GUI_PALETTE["success"])
    # table column 内で取得した draw list のclip矩形は通常その列幅に限られる。
    # 挿入線だけはtable全幅へ見せたいので、縦方向のclipを保ったまま横方向を
    # window content領域へ広げる。
    clip_min = draw_list.get_clip_rect_min()
    clip_max = draw_list.get_clip_rect_max()
    draw_list.push_clip_rect(
        x_min,
        float(clip_min[1]),
        x_max,
        float(clip_max[1]),
        False,
    )
    try:
        draw_list.add_line(
            x_min,
            y,
            x_max,
            y,
            int(color),
            max(1.0, 2.0 * _imgui_metric_scale(imgui)),
        )
    finally:
        draw_list.pop_clip_rect()


def _effect_step_item_rect(
    imgui: ModuleType,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """直前itemのscreen-space矩形を返す。"""

    item_min_raw = imgui.get_item_rect_min()
    item_max_raw = imgui.get_item_rect_max()
    return (
        (float(item_min_raw[0]), float(item_min_raw[1])),
        (float(item_max_raw[0]), float(item_max_raw[1])),
    )


def _effect_step_mouse_y(imgui: ModuleType) -> float:
    """現在のmouse yを返す。"""

    return float(imgui.get_mouse_position()[1])


def _render_effect_step_context_menu(
    imgui: ModuleType,
    *,
    state: EffectChainTableState | None,
    step: EffectStepKey,
) -> EffectOrderCommand | None:
    """step見出しのMove Up/Down context menuを描画する。"""

    with imgui.begin_popup_context_item("Effect step actions##effect_step_actions") as popup:
        if not popup.opened:
            return None
        up = None if state is None else state.neighbor_move(step, direction=-1)
        down = None if state is None else state.neighbor_move(step, direction=1)
        clicked_up, _selected = imgui.menu_item(
            "Move Up##effect_step_move_up",
            None,
            False,
            up is not None,
        )
        clicked_down, _selected = imgui.menu_item(
            "Move Down##effect_step_move_down",
            None,
            False,
            down is not None,
        )
        if state is not None and state.disabled_reason is not None:
            imgui.separator()
            imgui.text_disabled(state.disabled_reason)
        if clicked_up and state is not None and up is not None:
            target, placement = up
            return EffectOrderCommand.move(
                chain_id=state.chain_id,
                source=step,
                target=target,
                placement=placement,
            )
        if clicked_down and state is not None and down is not None:
            target, placement = down
            return EffectOrderCommand.move(
                chain_id=state.chain_id,
                source=step,
                target=target,
                placement=placement,
            )
    return None


def _render_effect_step_heading(
    imgui: ModuleType,
    label: str,
    *,
    step: EffectStepKey,
    state: EffectChainTableState | None,
) -> EffectOrderCommand | None:
    """drag handle、drop target、補助menuを持つeffect小見出しを描画する。"""

    imgui.table_next_row()
    imgui.table_set_column_index(0)
    imgui.push_id(f"effect_step:{step[0]}:{step[1]}")
    try:
        if state is None:
            disabled_reason = "Effect topology is unavailable; render a successful frame first."
        elif state.disabled_reason is not None:
            disabled_reason = state.disabled_reason
        elif state.is_pinned(step):
            disabled_reason = "This multi-input effect is fixed at the start."
        else:
            disabled_reason = None

        # handleだけをsource、handle+label全体をtargetにする。labelだけを
        # targetにすると、gripを掴んだx位置のまま縦へ動かす自然な操作では
        # 別stepへdropできない。EndGroup後はgroup全体が直前itemになるため、
        # sourceを覆うoverlayを置かずに広いtarget矩形を得られる。
        imgui.begin_group()
        try:
            if disabled_reason is None:
                imgui.small_button("::##effect_step_drag_handle")
                _set_item_tooltip(imgui, "Drag to reorder this effect step.")

                if state is not None:
                    with imgui.begin_drag_drop_source() as source:
                        if source.dragging:
                            imgui.set_drag_drop_payload(
                                EFFECT_STEP_DRAG_PAYLOAD_TYPE,
                                _encode_effect_step_drag_payload(
                                    state.chain_id,
                                    step,
                                ),
                            )
                            imgui.text(str(label))
            else:
                imgui.text_disabled("::")
                _set_item_tooltip(imgui, disabled_reason)

            imgui.same_line(0.0, 6.0 * _imgui_metric_scale(imgui))
            imgui.push_style_color(
                imgui.COLOR_TEXT,
                *PARAMETER_GUI_PALETTE["success"],
            )
            try:
                imgui.selectable(
                    f"{label}##effect_step_drop_target",
                    False,
                    0,
                )
            finally:
                imgui.pop_style_color()
        finally:
            imgui.end_group()
        if disabled_reason is not None:
            _set_item_tooltip(imgui, disabled_reason)

        item_rect = _effect_step_item_rect(imgui)
        drop_command: EffectOrderCommand | None = None
        if state is not None and state.disabled_reason is None:
            with imgui.begin_drag_drop_target() as target:
                if target.hovered:
                    preview_payload = imgui.accept_drag_drop_payload(
                        EFFECT_STEP_DRAG_PAYLOAD_TYPE,
                        imgui.DRAG_DROP_ACCEPT_PEEK_ONLY,
                    )
                    decoded = _decode_effect_step_drag_payload(preview_payload)
                    mouse_y = _effect_step_mouse_y(imgui)
                    placement = _effect_step_drop_placement(
                        mouse_y=mouse_y,
                        item_top=item_rect[0][1],
                        item_bottom=item_rect[1][1],
                    )
                    valid_preview = (
                        decoded is not None
                        and decoded[0] == state.chain_id
                        and state.can_move(decoded[1], step, placement)
                    )
                    if valid_preview:
                        _draw_effect_step_insertion_line(
                            imgui,
                            item_min=item_rect[0],
                            item_max=item_rect[1],
                            placement=placement,
                        )

                    delivered_payload = imgui.accept_drag_drop_payload(
                        EFFECT_STEP_DRAG_PAYLOAD_TYPE,
                        imgui.DRAG_DROP_ACCEPT_NO_DRAW_DEFAULT_RECT,
                    )
                    delivered = _decode_effect_step_drag_payload(delivered_payload)
                    if (
                        delivered is not None
                        and delivered[0] == state.chain_id
                        and state.can_move(delivered[1], step, placement)
                    ):
                        drop_command = EffectOrderCommand.move(
                            chain_id=state.chain_id,
                            source=delivered[1],
                            target=step,
                            placement=placement,
                        )

        menu_command = _render_effect_step_context_menu(
            imgui,
            state=state,
            step=step,
        )
        return drop_command if drop_command is not None else menu_command
    finally:
        imgui.pop_id()


def _effect_step_heading_by_rows(
    rows: Sequence[ParameterRow],
) -> dict[EffectStepKey, str]:
    """Effect rows の各(op, site_id)に、短く一意な小見出しを割り当てる。"""

    steps_by_display_op: dict[str, list[EffectStepKey]] = {}
    for row in rows:
        display_op = operation_display_name(row.op)
        step = (row.op, row.site_id)
        op_steps = steps_by_display_op.setdefault(display_op, [])
        if step not in op_steps:
            op_steps.append(step)

    out: dict[EffectStepKey, str] = {}
    for display_op, steps in steps_by_display_op.items():
        op_label = humanize_identifier(display_op)
        for ordinal, step in enumerate(steps, start=1):
            out[step] = op_label if len(steps) == 1 else f"{op_label} {int(ordinal)}"
    return out


def _render_minmax_cell(
    imgui: ModuleType,
    *,
    rules: RowUiRules,
    ui_min: float | int | None,
    ui_max: float | int | None,
) -> tuple[bool, float | int | None, float | int | None]:
    """min-max 列を描画し、(changed, ui_min, ui_max) を返す。"""

    imgui.table_set_column_index(2)

    if rules.minmax == "float_range":
        min_display = -1.0 if ui_min is None else float(ui_min)
        max_display = 1.0 if ui_max is None else float(ui_max)
        imgui.set_next_item_width(-1)
        changed, min_display, max_display = imgui.drag_float_range2(
            "##ui_range",
            float(min_display),  # current_min
            float(max_display),  # current_max
            0.1,  # speed
            0.0,  # min_value
            0.0,  # max_value
            "%.1f",  # format
            None,
        )
        if not changed:
            return False, ui_min, ui_max
        return True, float(min_display), float(max_display)

    if rules.minmax == "int_range":
        min_display_i = -10 if ui_min is None else int(ui_min)
        max_display_i = 10 if ui_max is None else int(ui_max)
        imgui.set_next_item_width(-1)
        changed, min_display_i, max_display_i = imgui.drag_int_range2(
            "##ui_range",
            int(min_display_i),  # current_min
            int(max_display_i),  # current_max
            0.1,  # speed
            0,  # min_value
            0,  # max_value
        )
        if not changed:
            return False, ui_min, ui_max
        return True, int(min_display_i), int(max_display_i)

    return False, ui_min, ui_max


def _snippet_popup_geometry(
    imgui: ModuleType,
    *,
    preferred_size: tuple[float, float] = SNIPPET_POPUP_WINDOW_SIZE_PX,
    margin: float = SNIPPET_POPUP_VIEWPORT_MARGIN_PX,
) -> tuple[float, float, float, float]:
    """Code popup の中心座標と viewport 内に収まるサイズを返す。"""

    viewport_x = 0.0
    viewport_y = 0.0
    viewport_width = 0.0
    viewport_height = 0.0

    viewport = imgui.get_main_viewport()
    work_pos = viewport.work_pos
    work_size = viewport.work_size
    viewport_x = float(work_pos[0])
    viewport_y = float(work_pos[1])
    viewport_width = float(work_size[0])
    viewport_height = float(work_size[1])

    if (
        not math.isfinite(viewport_width)
        or not math.isfinite(viewport_height)
        or viewport_width <= 0.0
        or viewport_height <= 0.0
    ):
        # pyimgui 2.0 では frame 開始前の main viewport が 0x0 の場合がある。
        # GUI backend が毎 frame 同期する io.display_size を fallback にする。
        display_size = imgui.get_io().display_size
        viewport_x = 0.0
        viewport_y = 0.0
        viewport_width = float(display_size[0])
        viewport_height = float(display_size[1])

    if (
        not math.isfinite(viewport_width)
        or not math.isfinite(viewport_height)
        or viewport_width <= 0.0
        or viewport_height <= 0.0
    ):
        fallback_margin = max(0.0, float(margin)) * 2.0
        viewport_width = float(preferred_size[0]) + fallback_margin
        viewport_height = float(preferred_size[1]) + fallback_margin

    preferred_width = max(1.0, float(preferred_size[0]))
    preferred_height = max(1.0, float(preferred_size[1]))
    inset = max(0.0, float(margin))
    available_width = max(1.0, viewport_width - inset * 2.0)
    available_height = max(1.0, viewport_height - inset * 2.0)
    popup_width = min(preferred_width, available_width)
    popup_height = min(preferred_height, available_height)
    center_x = viewport_x + viewport_width * 0.5
    center_y = viewport_y + viewport_height * 0.5
    return center_x, center_y, popup_width, popup_height


def _apply_midi_learn_command(
    cc_key: int | tuple[int | None, int | None, int | None] | None,
    command: MidiLearnCommand,
) -> int | tuple[int | None, int | None, int | None] | None:
    """pure transition の component command を row の ``cc_key`` へ反映する。"""

    if command.component is None:
        return command.cc
    current = cc_key if isinstance(cc_key, tuple) else (None, None, None)
    components = [current[0], current[1], current[2]]
    components[int(command.component)] = command.cc
    updated = (components[0], components[1], components[2])
    return None if updated == (None, None, None) else updated


def _render_cc_cell(
    imgui: ModuleType,
    *,
    row: ParameterRow,
    rules: RowUiRules,
    cc_key: int | tuple[int | None, int | None, int | None] | None,
    width_spacer: int,
    midi_learn_state: MidiLearnState | None,
    midi_last_cc_change: tuple[int, int] | None,
    last_source: ValueSource | None = None,
) -> tuple[
    bool,
    int | tuple[int | None, int | None, int | None] | None,
    MidiLearnState | None,
]:
    """MIDI 列を描画し、値と immutable learn state を返す。"""

    imgui.table_set_column_index(3)

    changed_any = False
    learn_state = midi_learn_state
    cell_width = content_region_available_width(imgui)
    metric_scale = _imgui_metric_scale(imgui)
    midi_spacing = float(width_spacer) * metric_scale

    if rules.cc_key == "none":
        # Unsupported MIDI is intentionally blank. The bundled font may not
        # contain an em dash, which otherwise renders as a repeated "?" and
        # looks like an error state throughout the table.
        return changed_any, cc_key, learn_state

    key = ParameterKey(op=row.op, site_id=row.site_id, arg=row.arg)
    midi_is_driving = last_source in {"midi_live", "midi_frozen"}

    if rules.cc_key == "int3":
        component_names = ("R", "G", "B") if row.kind == "rgb" else ("X", "Y", "Z")
        current_tuple = cc_key if isinstance(cc_key, tuple) else (None, None, None)
        component_width = max(
            1.0,
            (float(cell_width) - midi_spacing * 2.0) / 3.0,
        )
        compact_components = component_width < 44.0 * metric_scale
        for i in range(3):
            component_cc = current_tuple[i]
            transition = transition_midi_learn(
                learn_state,
                target=key,
                component=int(i),
                current_cc=component_cc,
                last_cc_change=midi_last_cc_change,
                clicked=False,
            )
            learn_state = transition.state
            component_cc = transition.current_cc
            active = transition.active
            if transition.command is not None:
                cc_key = _apply_midi_learn_command(cc_key, transition.command)
                changed_any = True
                current_tuple = cc_key if isinstance(cc_key, tuple) else (None, None, None)

            if active:
                label_text = f"{component_names[i]}..."
            elif component_cc is None:
                label_text = component_names[i]
            elif compact_components:
                # 3桁 CC でも 1 行を維持し、完全な番号は tooltip に置く。
                label_text = f"{component_names[i]}="
            else:
                label_text = f"{component_names[i]}{int(component_cc)}"

            button_label = f"{label_text}##cc_learn_{i}"
            if i > 0:
                imgui.same_line(0.0, midi_spacing)
            clicked = _render_midi_button(
                imgui,
                label=button_label,
                width=component_width,
                driving=bool(midi_is_driving and component_cc is not None),
            )
            if active:
                _set_item_tooltip(
                    imgui, f"Waiting for {component_names[i]} MIDI CC; click to cancel"
                )
            elif component_cc is None:
                _set_item_tooltip(imgui, f"Learn a MIDI CC for {component_names[i]}")
            elif midi_is_driving:
                status = "FROZEN" if last_source == "midi_frozen" else "LIVE"
                source_explanation = (
                    "a saved snapshot"
                    if last_source == "midi_frozen"
                    else "the connected MIDI input"
                )
                _set_item_tooltip(
                    imgui,
                    f"{status} — {component_names[i]} MIDI CC {int(component_cc)} from "
                    f"{source_explanation} is driving this component; click to remove",
                )
            else:
                _set_item_tooltip(
                    imgui,
                    f"Remove {component_names[i]} MIDI CC mapping; keep its effective value in UI",
                )
            if clicked:
                transition = transition_midi_learn(
                    learn_state,
                    target=key,
                    component=int(i),
                    current_cc=component_cc,
                    last_cc_change=midi_last_cc_change,
                    clicked=True,
                )
                learn_state = transition.state
                if transition.command is not None:
                    cc_key = _apply_midi_learn_command(cc_key, transition.command)
                    changed_any = True

    else:
        current_cc = cc_key if isinstance(cc_key, int) else None
        transition = transition_midi_learn(
            learn_state,
            target=key,
            component=None,
            current_cc=current_cc,
            last_cc_change=midi_last_cc_change,
            clicked=False,
        )
        learn_state = transition.state
        current_cc = transition.current_cc
        active = transition.active
        if transition.command is not None:
            cc_key = _apply_midi_learn_command(cc_key, transition.command)
            changed_any = True

        if active:
            label_text = "V..."
        elif current_cc is None:
            label_text = "V"
        elif midi_is_driving:
            status = "FROZEN" if last_source == "midi_frozen" else "LIVE"
            label_text = f"V{int(current_cc)} {status}"
        else:
            label_text = f"V{int(current_cc)}"

        button_label = f"{label_text}##cc_learn"
        # scalar は vec3 と同じ learn control の 1 成分版。MIDI cell 全幅を使い、
        # 短い V 系列の表示を左右に孤立させない。
        button_width = max(1.0, float(cell_width))
        clicked = _render_midi_button(
            imgui,
            label=button_label,
            width=button_width,
            driving=bool(midi_is_driving and current_cc is not None),
        )
        if active:
            _set_item_tooltip(imgui, "Waiting for a MIDI CC; click to cancel")
        elif current_cc is None:
            _set_item_tooltip(imgui, "Learn a MIDI CC")
        elif midi_is_driving:
            status = "FROZEN" if last_source == "midi_frozen" else "LIVE"
            source_explanation = (
                "a saved snapshot" if last_source == "midi_frozen" else "the connected MIDI input"
            )
            _set_item_tooltip(
                imgui,
                f"{status} — MIDI CC {int(current_cc)} from {source_explanation} "
                "is driving this value; click to remove",
            )
        else:
            _set_item_tooltip(imgui, "Remove MIDI CC mapping; keep its effective value in UI")
        if clicked:
            transition = transition_midi_learn(
                learn_state,
                target=key,
                component=None,
                current_cc=current_cc,
                last_cc_change=midi_last_cc_change,
                clicked=True,
            )
            learn_state = transition.state
            if transition.command is not None:
                cc_key = _apply_midi_learn_command(cc_key, transition.command)
                changed_any = True

    return changed_any, cc_key, learn_state


def render_parameter_row_4cols(
    row: ParameterRow,
    *,
    widget_state: WidgetSessionState,
    catalog: ParameterGuiCatalog | None = None,
    visible_label: str | None = None,
    midi_learn_state: MidiLearnState | None = None,
    midi_last_cc_change: tuple[int, int] | None = None,
    last_source: ValueSource | None = None,
    on_help_row: Callable[[ParameterRow, bool], None] | None = None,
) -> tuple[bool, ParameterRow, MidiLearnState | None]:
    """1 行（1 key）を 4 列テーブルとして描画し、更新後の row を返す。

    Columns
    -------
    1. source / label : CODE/UI selector + op#ordinal
    2. control : kind に応じたウィジェット
    3. min-max : ui_min/ui_max
    4. MIDI : cc_key learn / unassign

    Returns
    -------
    changed : bool
        いずれかの UI 値が変更された場合 True。
    row : ParameterRow
        変更を反映した新しい行モデル。
    midi_learn_state : MidiLearnState or None
        行操作を反映した次 frame 用の immutable learn state。
    """

    import imgui

    row_label = (
        _row_visible_label(row, catalog=catalog) if visible_label is None else str(visible_label)
    )

    # この 1 行（= 1 key）で何かが変更されたかの集計フラグ。
    changed_any = False

    # ParameterRow は immutable（frozen）なので、まずは更新候補をローカル変数として持つ。
    ui_value = row.ui_value
    ui_min = row.ui_min
    ui_max = row.ui_max
    cc_key = row.cc_key
    override = row.override
    favorite = row.favorite

    width_spacer = 4

    rules = ui_rules_for_row(row)

    # テーブル内のウィジェット ID が行ごとに衝突しないよう、push_id でスコープを切る。
    # ここで `row.arg` まで含めているのは、同じ op#ordinal でも arg が異なる可能性があるため。
    imgui.push_id(_row_id(row))
    try:
        # 以降の描画は「この行」に対して行う。
        imgui.table_next_row()

        # --- Column 1: source selector + label ---
        source_changed, override, reset_to_code = _render_label_cell(
            imgui,
            row_label=row_label,
            kind=row.kind,
            override=bool(override),
            cc_key=cc_key,
            last_source=last_source,
        )
        if source_changed:
            changed_any = True
        if reset_to_code:
            cc_key = None
            override = False
            changed_any = True
            target = ParameterKey(op=row.op, site_id=row.site_id, arg=row.arg)
            if midi_learn_state is not None and midi_learn_state.active_target == target:
                midi_learn_state = replace(
                    midi_learn_state,
                    active_target=None,
                    active_component=None,
                )

        # _render_label_cell の最後の item は可視 label。label 自体の hover も
        # pin button と同様に Help pane の対象にする。
        _notify_parameter_help(imgui, row, on_help_row)
        favorite_changed, favorite = _render_favorite_toggle(imgui, favorite)
        if favorite_changed:
            changed_any = True
        _notify_parameter_help(imgui, row, on_help_row)

        # --- Column 2: control（kind に応じたウィジェット）---
        # slider の visible label はテーブルの label 列で代替するため、
        # ウィジェット側は "##value" を使って非表示にしている。
        changed, value = _render_control_cell(
            imgui,
            row,
            widget_state=widget_state,
        )
        if changed:
            changed_any = True
            ui_value = value
            if rules.show_override and not bool(override):
                override = True
        _notify_parameter_help(imgui, row, on_help_row)

        # --- Column 3: min-max（ui_min/ui_max）---
        changed_range, ui_min, ui_max = _render_minmax_cell(
            imgui,
            rules=rules,
            ui_min=ui_min,
            ui_max=ui_max,
        )
        if changed_range:
            changed_any = True
        _notify_parameter_help(imgui, row, on_help_row)

        # --- Column 4: MIDI（cc_key learn / unassign のみ）---
        changed_cc, cc_key, midi_learn_state = _render_cc_cell(
            imgui,
            row=row,
            rules=rules,
            cc_key=cc_key,
            width_spacer=width_spacer,
            midi_learn_state=midi_learn_state,
            midi_last_cc_change=midi_last_cc_change,
            last_source=last_source,
        )
        if changed_cc:
            changed_any = True
        _notify_parameter_help(imgui, row, on_help_row)
    finally:
        # push_id と必ず対になるよう finally で pop_id する。
        imgui.pop_id()

    if not changed_any:
        # steady frame では全 visible row の dataclass を作り直さない。
        # table commit は object identity で sparse change を判定できる。
        return False, row, midi_learn_state

    # ローカル変数へ反映した結果を、新しい ParameterRow として返す。
    updated = replace(
        row,
        ui_value=ui_value,
        ui_min=ui_min,
        ui_max=ui_max,
        cc_key=cc_key,
        override=override,
        favorite=bool(favorite),
        reset_to_code=bool(reset_to_code),
    )

    return changed_any, updated, midi_learn_state


def _effect_chain_state_for_block(
    block: GroupBlockLayout,
    render_input: TableRenderInput,
) -> EffectChainTableState | None:
    """effect-chain block の immutable order state を返す。"""

    if (
        block.group_id[0] is not GroupType.EFFECT_CHAIN
        or render_input.effect_chain_state_by_id is None
    ):
        return None
    return render_input.effect_chain_state_by_id.get(str(block.group_id[1]))


def _append_unchanged_block_rows(
    block: GroupBlockLayout,
    model_rows: Sequence[ParameterRow],
    state: _TableRenderState,
) -> None:
    """非描画 block の rows を identity のまま返却列へ足す。"""

    state.updated_rows.extend(model_rows[item.row_index] for item in block.items)


def _render_effect_order_controls(
    imgui: ModuleType,
    chain_state: EffectChainTableState | None,
    state: _TableRenderState,
) -> None:
    """effect chain の UI-order 表示と reset command を描画する。"""

    if chain_state is None or not chain_state.order_overridden:
        return
    imgui.text_colored(
        "UI order",
        *PARAMETER_GUI_PALETTE["success"],
    )
    imgui.same_line()
    if imgui.small_button("Reset##effect_order_reset"):
        state.effect_order_commands.append(EffectOrderCommand.reset(chain_id=chain_state.chain_id))
    _set_item_tooltip(imgui, "Reset this chain to code order.")
    imgui.same_line()


def _render_group_header(
    imgui: ModuleType,
    block: GroupBlockLayout,
    render_input: TableRenderInput,
    state: _TableRenderState,
) -> bool:
    """group header、collapse、Code/effect-order controls を描画する。"""

    if not block.header:
        return True

    first_row = None if not block.items else render_input.model_rows[block.items[0].row_index]
    collapse_key = _collapse_key_for_group(block.group_id, first_row)
    if collapse_key is not None:
        imgui.set_next_item_open(
            collapse_key not in state.collapsed_headers,
            imgui.ALWAYS,
        )

    header_kind = _header_kind_for_group_id(block.group_id)
    base = _rgba01_from_rgba255(GROUP_HEADER_BASE_COLORS_RGBA[header_kind])
    normal, hovered, active = _derive_header_colors(base)
    imgui.push_style_color(imgui.COLOR_HEADER, *normal)
    imgui.push_style_color(imgui.COLOR_HEADER_HOVERED, *hovered)
    imgui.push_style_color(imgui.COLOR_HEADER_ACTIVE, *active)
    try:
        group_open, _visible = imgui.collapsing_header(
            f"{humanize_identifier(block.header)}##group_header",
            None,
            flags=(imgui.TREE_NODE_DEFAULT_OPEN | imgui.TREE_NODE_ALLOW_ITEM_OVERLAP),
        )
        imgui.set_item_allow_overlap()
    finally:
        imgui.pop_style_color(3)

    chain_state = _effect_chain_state_for_block(block, render_input)
    button_label = "Code"
    text_w, _text_h = imgui.calc_text_size(button_label)
    button_w = float(text_w) + 24.0
    count_label = f"{len(block.items)} parameters"
    count_w, _count_h = imgui.calc_text_size(count_label)
    cluster_w = float(count_w) + 12.0 + float(button_w)
    if chain_state is not None and chain_state.order_overridden:
        ui_order_w, _ui_order_h = imgui.calc_text_size("UI order")
        reset_w, _reset_h = imgui.calc_text_size("Reset")
        cluster_w += float(ui_order_w) + float(reset_w) + 36.0
    pos_x = float(imgui.get_window_width()) - cluster_w - 16.0
    if pos_x > 0.0:
        imgui.same_line(position=pos_x)
    else:
        imgui.same_line()

    _render_effect_order_controls(imgui, chain_state, state)
    imgui.text_disabled(count_label)
    imgui.same_line()
    if imgui.small_button(button_label):
        state.snippet_popup_text_new = snippet_for_block(
            block,
            render_input.model_rows,
            catalog=render_input.catalog,
            last_effective_by_key=render_input.last_effective_by_key,
            step_info_by_site=render_input.step_info_by_site,
            raw_label_by_site=render_input.raw_label_by_site,
        )
        state.want_open_snippet_popup = True

    if collapse_key is not None:
        if group_open:
            state.collapsed_headers.discard(collapse_key)
        else:
            state.collapsed_headers.add(collapse_key)
    return bool(group_open)


def _render_group_row_table(
    imgui: ModuleType,
    block: GroupBlockLayout,
    render_input: TableRenderInput,
    *,
    widget_state: WidgetSessionState,
    state: _TableRenderState,
    on_help_row: Callable[[ParameterRow, bool], None] | None,
) -> None:
    """open group の effect headings と 4-column parameter rows を描画する。"""

    table_flags = (
        imgui.TABLE_SIZING_FIXED_FIT
        | imgui.TABLE_ROW_BACKGROUND
        | imgui.TABLE_BORDERS_INNER_VERTICAL
    )
    table = imgui.begin_table("##parameters", 4, table_flags)
    if not table.opened:
        _append_unchanged_block_rows(block, render_input.model_rows, state)
        return

    try:
        _setup_parameter_table_columns(
            imgui,
            metric_scale=render_input.metric_scale,
        )
        if not state.drew_column_headers:
            imgui.table_headers_row()
            state.drew_column_headers = True

        effect_heading_by_step = (
            _effect_step_heading_by_rows(
                [render_input.model_rows[item.row_index] for item in block.items]
            )
            if block.group_id[0] is GroupType.EFFECT_CHAIN
            else {}
        )
        previous_effect_step: EffectStepKey | None = None
        chain_state = _effect_chain_state_for_block(block, render_input)
        for item in block.items:
            row = render_input.model_rows[item.row_index]
            item_step = (row.op, row.site_id)
            if effect_heading_by_step and item_step != previous_effect_step:
                command = _render_effect_step_heading(
                    imgui,
                    effect_heading_by_step[item_step],
                    step=item_step,
                    state=chain_state,
                )
                if command is not None:
                    state.effect_order_commands.append(command)
                previous_effect_step = item_step

            row_key = ParameterKey(op=row.op, site_id=row.site_id, arg=row.arg)
            _row_changed, updated, state.midi_learn_state = render_parameter_row_4cols(
                row,
                widget_state=widget_state,
                catalog=render_input.catalog,
                visible_label=item.visible_label,
                midi_learn_state=state.midi_learn_state,
                midi_last_cc_change=render_input.midi_last_cc_change,
                last_source=(
                    None
                    if render_input.last_source_by_key is None
                    else render_input.last_source_by_key.get(row_key)
                ),
                on_help_row=on_help_row,
            )
            state.updated_rows.append(updated)
    finally:
        imgui.end_table()


def _render_snippet_modal(
    imgui: ModuleType,
    *,
    widget_state: WidgetSessionState,
    state: _TableRenderState,
) -> None:
    """Code popup の open trigger と modal editor を描画する。"""

    if state.want_open_snippet_popup and state.snippet_popup_text_new is not None:
        widget_state.snippet_popup_text = str(state.snippet_popup_text_new)
        widget_state.snippet_popup_focus_next = True
        imgui.open_popup("Code##snippet_popup")

    popup_x, popup_y, popup_width, popup_height = _snippet_popup_geometry(imgui)
    imgui.set_next_window_position(
        popup_x,
        popup_y,
        condition=imgui.ALWAYS,
        pivot_x=0.5,
        pivot_y=0.5,
    )
    imgui.set_next_window_size(
        popup_width,
        popup_height,
        condition=imgui.ALWAYS,
    )
    with imgui.begin_popup_modal("Code##snippet_popup") as popup:
        if not popup.opened:
            return
        if imgui.button("Close"):
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button("Copy"):
            imgui.set_clipboard_text(str(widget_state.snippet_popup_text))
            imgui.close_current_popup()
            widget_state.snippet_popup_focus_next = False
        imgui.same_line()
        imgui.text_disabled("macOS Cmd+A→Cmd+C / Win/Linux Ctrl+A→Ctrl+C")

        if widget_state.snippet_popup_focus_next:
            imgui.set_keyboard_focus_here()
            widget_state.snippet_popup_focus_next = False

        avail_w, avail_h = imgui.get_content_region_available()
        editor_width = max(1.0, float(avail_w))
        editor_height = max(1.0, float(avail_h) - 8.0)
        _changed, _text_out = imgui.input_text_multiline(
            "##snippet_text",
            str(widget_state.snippet_popup_text),
            -1,
            editor_width,
            editor_height,
            flags=imgui.INPUT_TEXT_READ_ONLY | imgui.INPUT_TEXT_AUTO_SELECT_ALL,
        )
        if imgui.is_item_focused() or imgui.is_item_active():
            io = imgui.get_io()
            if (io.key_ctrl or io.key_super) and imgui.is_key_pressed(imgui.KEY_C, False):
                imgui.set_clipboard_text(str(widget_state.snippet_popup_text))


def render_parameter_table(
    render_input: TableRenderInput,
    *,
    widget_state: WidgetSessionState,
    on_help_row: Callable[[ParameterRow, bool], None] | None = None,
) -> TableEdits:
    """immutable snapshot を section 順に描画し、immutable edit 集合を返す。"""

    import imgui

    state = _TableRenderState(
        collapsed_headers=set(render_input.collapsed_headers),
        midi_learn_state=render_input.midi_learn_state,
        updated_rows=[],
        effect_order_commands=[],
    )

    for block_index, block in enumerate(render_input.group_layout):
        if block_index > 0 and block.header:
            imgui.spacing()
        imgui.push_id(block.header_id)
        try:
            if not _render_group_header(imgui, block, render_input, state):
                _append_unchanged_block_rows(block, render_input.model_rows, state)
                continue
            _render_group_row_table(
                imgui,
                block,
                render_input,
                widget_state=widget_state,
                state=state,
                on_help_row=on_help_row,
            )
        finally:
            imgui.pop_id()

    _render_snippet_modal(imgui, widget_state=widget_state, state=state)
    return TableEdits(
        rows=tuple(state.updated_rows),
        collapsed_headers=frozenset(state.collapsed_headers),
        midi_learn_state=state.midi_learn_state,
        effect_order_commands=tuple(state.effect_order_commands),
    )
