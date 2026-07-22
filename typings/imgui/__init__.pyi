"""Grafix が利用する pyimgui 2 API の型定義。"""

from collections.abc import Callable, Iterator
from types import TracebackType
from typing import Any, Self

class Vec2:
    x: float
    y: float

    def __getitem__(self, index: int) -> float: ...
    def __iter__(self) -> Iterator[float]: ...


Vec2Value = Vec2 | tuple[float, float]
RGBA = tuple[float, float, float, float]


class _OpenedContext:
    opened: bool

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _BeginEnd(_OpenedContext):
    expanded: bool


class _DragDropSourceContext:
    dragging: bool

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _DragDropTargetContext:
    hovered: bool

    def __enter__(self) -> Self: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class _Viewport:
    work_pos: Vec2
    work_size: Vec2


class _DrawList:
    def add_line(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        color: int,
        thickness: float = ...,
    ) -> None: ...
    def get_clip_rect_min(self) -> Vec2: ...
    def get_clip_rect_max(self) -> Vec2: ...
    def push_clip_rect(
        self,
        clip_rect_min_x: float,
        clip_rect_min_y: float,
        clip_rect_max_x: float,
        clip_rect_max_y: float,
        intersect_with_current_clip_rect: bool = ...,
    ) -> None: ...
    def pop_clip_rect(self) -> None: ...


class _FontAtlas:
    def clear(self) -> None: ...
    def add_font_from_file_ttf(
        self,
        filename: str,
        size_pixels: float,
        *,
        font_config: object | None = ...,
        glyph_ranges: object | None = ...,
        merge: bool = ...,
    ) -> object: ...
    def get_glyph_ranges_default(self) -> object: ...
    def get_glyph_ranges_japanese(self) -> object: ...


class _IO:
    config_flags: int
    delta_time: float
    display_size: Vec2Value
    display_fb_scale: Vec2Value
    font_global_scale: float
    fonts: _FontAtlas
    get_clipboard_text_fn: Callable[[], str] | None
    key_ctrl: bool
    key_super: bool
    mouse_wheel: float
    set_clipboard_text_fn: Callable[[str], None] | None
    want_capture_keyboard: bool
    want_text_input: bool


class _Style:
    colors: list[RGBA]
    window_padding: Vec2
    frame_padding: Vec2
    item_spacing: Vec2
    item_inner_spacing: Vec2
    cell_padding: Vec2
    indent_spacing: float
    scrollbar_size: float
    grab_min_size: float
    window_rounding: float
    child_rounding: float
    frame_rounding: float
    popup_rounding: float
    scrollbar_rounding: float
    grab_rounding: float
    tab_rounding: float
    window_border_size: float
    child_border_size: float
    popup_border_size: float
    frame_border_size: float
    tab_border_size: float


class FontConfig:
    def __init__(self, *, merge_mode: bool = ...) -> None: ...


class GlyphRanges:
    def __init__(self, ranges: tuple[int, ...]) -> None: ...


class _CoreModule:
    FontConfig: type[FontConfig]
    GlyphRanges: type[GlyphRanges]


core: _CoreModule


ALWAYS: int
COLOR_BORDER: int
COLOR_BORDER_SHADOW: int
COLOR_BUTTON: int
COLOR_BUTTON_ACTIVE: int
COLOR_BUTTON_HOVERED: int
COLOR_CHECK_MARK: int
COLOR_CHILD_BACKGROUND: int
COLOR_DRAG_DROP_TARGET: int
COLOR_EDIT_DISPLAY_RGB: int
COLOR_EDIT_INPUT_RGB: int
COLOR_EDIT_UINT8: int
COLOR_FRAME_BACKGROUND: int
COLOR_FRAME_BACKGROUND_ACTIVE: int
COLOR_FRAME_BACKGROUND_HOVERED: int
COLOR_HEADER: int
COLOR_HEADER_ACTIVE: int
COLOR_HEADER_HOVERED: int
COLOR_MENUBAR_BACKGROUND: int
COLOR_MODAL_WINDOW_DIM_BACKGROUND: int
COLOR_NAV_HIGHLIGHT: int
COLOR_NAV_WINDOWING_DIM_BACKGROUND: int
COLOR_NAV_WINDOWING_HIGHLIGHT: int
COLOR_PLOT_HISTOGRAM: int
COLOR_PLOT_HISTOGRAM_HOVERED: int
COLOR_PLOT_LINES: int
COLOR_PLOT_LINES_HOVERED: int
COLOR_POPUP_BACKGROUND: int
COLOR_RESIZE_GRIP: int
COLOR_RESIZE_GRIP_ACTIVE: int
COLOR_RESIZE_GRIP_HOVERED: int
COLOR_SCROLLBAR_BACKGROUND: int
COLOR_SCROLLBAR_GRAB: int
COLOR_SCROLLBAR_GRAB_ACTIVE: int
COLOR_SCROLLBAR_GRAB_HOVERED: int
COLOR_SEPARATOR: int
COLOR_SEPARATOR_ACTIVE: int
COLOR_SEPARATOR_HOVERED: int
COLOR_SLIDER_GRAB: int
COLOR_SLIDER_GRAB_ACTIVE: int
COLOR_TAB: int
COLOR_TABLE_BORDER_LIGHT: int
COLOR_TABLE_BORDER_STRONG: int
COLOR_TABLE_HEADER_BACKGROUND: int
COLOR_TABLE_ROW_BACKGROUND: int
COLOR_TABLE_ROW_BACKGROUND_ALT: int
COLOR_TAB_ACTIVE: int
COLOR_TAB_HOVERED: int
COLOR_TAB_UNFOCUSED: int
COLOR_TAB_UNFOCUSED_ACTIVE: int
COLOR_TEXT: int
COLOR_TEXT_DISABLED: int
COLOR_TEXT_SELECTED_BACKGROUND: int
COLOR_TITLE_BACKGROUND: int
COLOR_TITLE_BACKGROUND_ACTIVE: int
COLOR_TITLE_BACKGROUND_COLLAPSED: int
COLOR_WINDOW_BACKGROUND: int
COMBO_HEIGHT_LARGE: int
CONFIG_NAV_ENABLE_KEYBOARD: int
DRAG_DROP_ACCEPT_NO_DRAW_DEFAULT_RECT: int
DRAG_DROP_ACCEPT_PEEK_ONLY: int
INPUT_TEXT_AUTO_SELECT_ALL: int
INPUT_TEXT_READ_ONLY: int
KEY_C: int
SLIDER_FLAGS_ALWAYS_CLAMP: int
STYLE_FRAME_PADDING: int
TABLE_BORDERS_INNER_VERTICAL: int
TABLE_COLUMN_NO_RESIZE: int
TABLE_COLUMN_WIDTH_FIXED: int
TABLE_COLUMN_WIDTH_STRETCH: int
TABLE_ROW_BACKGROUND: int
TABLE_SIZING_FIXED_FIT: int
TREE_NODE_ALLOW_ITEM_OVERLAP: int
TREE_NODE_DEFAULT_OPEN: int
WINDOW_NO_COLLAPSE: int
WINDOW_NO_RESIZE: int
WINDOW_NO_SCROLLBAR: int
WINDOW_NO_TITLE_BAR: int


def create_context(shared_font_atlas: object | None = ...) -> object: ...
def destroy_context(context: object | None = ...) -> None: ...
def set_current_context(context: object) -> None: ...
def get_io() -> _IO: ...
def get_style() -> _Style: ...
def style_colors_dark(style: _Style | None = ...) -> None: ...
def new_frame() -> None: ...
def render() -> None: ...
def get_draw_data() -> object: ...
def show_demo_window(closable: bool = ...) -> bool: ...

def begin(
    label: str,
    closable: bool = ...,
    flags: int = ...,
) -> _BeginEnd: ...
def end() -> None: ...
def begin_child(
    label: str,
    width: float = ...,
    height: float = ...,
    border: bool = ...,
    flags: int = ...,
) -> _OpenedContext: ...
def end_child() -> None: ...
def begin_combo(label: str, preview_value: str, flags: int = ...) -> _OpenedContext: ...
def end_combo() -> None: ...
def begin_popup(label: str, flags: int = ...) -> _OpenedContext: ...
def begin_popup_context_item(
    label: str | None = ...,
    mouse_button: int = ...,
) -> _OpenedContext: ...
def begin_popup_modal(
    label: str,
    visible: bool | None = ...,
    flags: int = ...,
) -> _OpenedContext: ...
def end_popup() -> None: ...
def open_popup(label: str, flags: int = ...) -> None: ...
def close_current_popup() -> None: ...
def begin_table(
    label: str,
    column: int,
    flags: int = ...,
    outer_size_width: float = ...,
    outer_size_height: float = ...,
    inner_width: float = ...,
) -> _OpenedContext: ...
def end_table() -> None: ...
def begin_group() -> None: ...
def end_group() -> None: ...

def button(label: str, width: float = ..., height: float = ...) -> bool: ...
def small_button(label: str) -> bool: ...
def checkbox(label: str, state: bool) -> tuple[bool, bool]: ...
def radio_button(label: str, active: bool) -> bool: ...
def selectable(
    label: str,
    selected: bool = ...,
    flags: int = ...,
    width: float = ...,
    height: float = ...,
) -> tuple[bool, bool]: ...
def menu_item(
    label: str,
    shortcut: str | None = ...,
    selected: bool = ...,
    enabled: bool = ...,
) -> tuple[bool, bool]: ...
def collapsing_header(
    text: str,
    visible: bool | None = ...,
    flags: int = ...,
) -> tuple[bool, bool | None]: ...

def slider_float(
    label: str,
    value: float,
    min_value: float,
    max_value: float,
    format: str = ...,
    power: float = ...,
    flags: int = ...,
) -> tuple[bool, float]: ...
def slider_int(
    label: str,
    value: int,
    min_value: int,
    max_value: int,
    format: str = ...,
    flags: int = ...,
) -> tuple[bool, int]: ...
def slider_float3(
    label: str,
    value0: float,
    value1: float,
    value2: float,
    min_value: float,
    max_value: float,
    format: str = ...,
    power: float = ...,
    flags: int = ...,
) -> tuple[bool, tuple[float, float, float]]: ...
def color_edit3(
    label: str,
    value0: float,
    value1: float,
    value2: float,
    flags: int = ...,
) -> tuple[bool, tuple[float, float, float]]: ...
def drag_float(
    label: str,
    value: float,
    change_speed: float = ...,
    min_value: float = ...,
    max_value: float = ...,
    format: str = ...,
    power: float = ...,
) -> tuple[bool, float]: ...
def drag_float_range2(
    label: str,
    current_min: float,
    current_max: float,
    speed: float = ...,
    min_value: float = ...,
    max_value: float = ...,
    format: str = ...,
    format_max: str | None = ...,
    power: float = ...,
) -> tuple[bool, float, float]: ...
def drag_int_range2(
    label: str,
    current_min: int,
    current_max: int,
    speed: float = ...,
    min_value: int = ...,
    max_value: int = ...,
    format: str = ...,
    format_max: str | None = ...,
) -> tuple[bool, int, int]: ...
def input_int(
    label: str,
    value: int,
    step: int = ...,
    step_fast: int = ...,
    flags: int = ...,
) -> tuple[bool, int]: ...
def input_text(
    label: str,
    value: str,
    buffer_length: int = ...,
    flags: int = ...,
    callback: Any = ...,
    user_data: Any = ...,
) -> tuple[bool, str]: ...
def input_text_with_hint(
    label: str,
    hint: str,
    value: str,
    buffer_length: int = ...,
    flags: int = ...,
    callback: Any = ...,
    user_data: Any = ...,
) -> tuple[bool, str]: ...
def input_text_multiline(
    label: str,
    value: str,
    buffer_length: int = ...,
    width: float = ...,
    height: float = ...,
    flags: int = ...,
    callback: Any = ...,
    user_data: Any = ...,
) -> tuple[bool, str]: ...

def text(value: str) -> None: ...
def text_disabled(value: str) -> None: ...
def text_wrapped(value: str) -> None: ...
def text_colored(value: str, red: float, green: float, blue: float, alpha: float = ...) -> None: ...
def separator() -> None: ...
def spacing() -> None: ...
def same_line(position: float = ..., spacing: float = ...) -> None: ...
def calc_text_size(
    text: str,
    hide_text_after_double_hash: bool = ...,
    wrap_width: float = ...,
) -> Vec2: ...
def get_text_line_height() -> float: ...
def get_frame_height() -> float: ...
def get_content_region_available() -> Vec2: ...
def get_content_region_available_width() -> float: ...
def get_window_width() -> float: ...
def get_cursor_pos_x() -> float: ...
def set_cursor_pos_x(value: float) -> None: ...
def get_window_position() -> Vec2: ...
def get_window_content_region_min() -> Vec2: ...
def get_window_content_region_max() -> Vec2: ...
def get_item_rect_min() -> Vec2: ...
def get_item_rect_max() -> Vec2: ...
def get_mouse_position() -> Vec2: ...
def get_mouse_pos() -> Vec2: ...
def get_main_viewport() -> _Viewport: ...
def get_window_draw_list() -> _DrawList: ...
def get_color_u32_rgba(red: float, green: float, blue: float, alpha: float) -> int: ...

def align_text_to_frame_padding() -> None: ...
def set_next_item_open(is_open: bool, condition: int = ...) -> None: ...
def set_item_allow_overlap() -> None: ...
def set_item_default_focus() -> None: ...
def set_keyboard_focus_here(offset: int = ...) -> None: ...
def set_next_item_width(width: float) -> None: ...
def set_next_window_position(
    x: float,
    y: float,
    condition: int = ...,
    pivot_x: float = ...,
    pivot_y: float = ...,
) -> None: ...
def set_next_window_size(
    width: float,
    height: float,
    condition: int = ...,
) -> None: ...
def set_clipboard_text(text: str) -> None: ...
def set_tooltip(text: str) -> None: ...
def is_item_active() -> bool: ...
def is_item_clicked(mouse_button: int = ...) -> bool: ...
def is_item_focused() -> bool: ...
def is_item_hovered() -> bool: ...
def is_any_item_active() -> bool: ...
def is_key_pressed(key_index: int, repeat: bool = ...) -> bool: ...

def push_id(value: str | int) -> None: ...
def pop_id() -> None: ...
def push_style_color(
    variable: int,
    red: float,
    green: float,
    blue: float,
    alpha: float = ...,
) -> None: ...
def pop_style_color(count: int = ...) -> None: ...
def push_style_var(variable: int, value: float | Vec2Value) -> None: ...
def pop_style_var(count: int = ...) -> None: ...

def table_setup_column(
    label: str,
    flags: int = ...,
    init_width_or_weight: float = ...,
    user_id: int = ...,
) -> None: ...
def table_headers_row() -> None: ...
def table_next_row(row_flags: int = ..., min_row_height: float = ...) -> None: ...
def table_set_column_index(column_n: int) -> bool: ...

def begin_drag_drop_source(flags: int = ...) -> _DragDropSourceContext: ...
def end_drag_drop_source() -> None: ...
def begin_drag_drop_target() -> _DragDropTargetContext: ...
def end_drag_drop_target() -> None: ...
def set_drag_drop_payload(type: str, data: bytes, condition: int = ...) -> bool: ...
def accept_drag_drop_payload(type: str, flags: int = ...) -> bytes | None: ...
