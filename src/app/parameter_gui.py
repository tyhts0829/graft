# どこで: `src/app/parameter_gui.py`。
# 何を: ParamStore の行モデル（ParameterRow）を pyimgui のウィジェットに対応付けて描画する。
# なぜ: kind ごとに関数を分離し、手動スモークで 1 行ずつ確認できるようにするため。

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.parameters.key import ParameterKey
from src.parameters.meta import ParamMeta
from src.parameters.store import ParamStore
from src.parameters.view import ParameterRow, rows_from_snapshot, update_state_from_ui

WidgetFn = Callable[[ParameterRow], tuple[bool, Any]]
COLUMN_WEIGHTS_DEFAULT = (0.20, 0.60, 0.15, 0.20)


def _row_visible_label(row: ParameterRow) -> str:
    """行の表示ラベル（`op#ordinal`）を返す。"""

    return f"{row.op}#{row.ordinal}"


def _row_id(row: ParameterRow) -> str:
    """ImGui の `push_id()` 用に、行の安定 ID を返す。"""

    return f"{row.op}#{row.ordinal}:{row.arg}"


def _float_slider_range(row: ParameterRow) -> tuple[float, float]:
    """float スライダーのレンジ (min, max) を返す。

    ui_min/ui_max が None の場合は -1.0..1.0 にフォールバックする。
    """

    min_value = -1.0 if row.ui_min is None else float(row.ui_min)
    max_value = 1.0 if row.ui_max is None else float(row.ui_max)
    return min_value, max_value


def _int_slider_range(row: ParameterRow) -> tuple[int, int]:
    """int スライダーのレンジ (min, max) を返す。

    ui_min/ui_max が None の場合は -10..10 にフォールバックする。
    """

    min_value = -10 if row.ui_min is None else int(row.ui_min)
    max_value = 10 if row.ui_max is None else int(row.ui_max)
    return min_value, max_value


def _as_float3(value: Any) -> tuple[float, float, float]:
    """値を長さ 3 の float タプル `(x, y, z)` に変換して返す。"""

    try:
        x, y, z = value  # type: ignore[misc]
    except Exception as exc:
        raise ValueError(
            f"vec3 ui_value must be a length-3 sequence: {value!r}"
        ) from exc
    return float(x), float(y), float(z)


def widget_float_slider(row: ParameterRow) -> tuple[bool, float]:
    """kind=float のスライダーを描画し、(changed, value) を返す。

    Parameters
    ----------
    row : ParameterRow
        kind=float の行モデル。

    Returns
    -------
    changed : bool
        値が変更された場合 True。
    value : float
        変更後の値。
    """

    import imgui

    value = float(row.ui_value)
    min_value, max_value = _float_slider_range(row)
    return imgui.slider_float(
        "##value", float(value), float(min_value), float(max_value)
    )


def widget_int_slider(row: ParameterRow) -> tuple[bool, int]:
    """kind=int のスライダーを描画し、(changed, value) を返す。"""

    import imgui

    value = int(row.ui_value)
    min_value, max_value = _int_slider_range(row)
    return imgui.slider_int("##value", int(value), int(min_value), int(max_value))


def widget_vec3_slider(row: ParameterRow) -> tuple[bool, tuple[float, float, float]]:
    """kind=vec3 のスライダーを描画し、(changed, value) を返す。"""

    import imgui

    value0, value1, value2 = _as_float3(row.ui_value)
    min_value, max_value = _float_slider_range(row)
    changed, out = imgui.slider_float3(
        "##value",
        float(value0),
        float(value1),
        float(value2),
        float(min_value),
        float(max_value),
    )
    return changed, _as_float3(out)


def widget_bool_checkbox(row: ParameterRow) -> tuple[bool, bool]:
    """kind=bool のチェックボックスを描画し、(changed, value) を返す。"""

    import imgui

    clicked, state = imgui.checkbox("##value", bool(row.ui_value))
    return clicked, bool(state)


def widget_string_input(row: ParameterRow) -> tuple[bool, str]:
    """kind=string のテキスト入力を描画し、(changed, value) を返す。"""

    import imgui

    value = "" if row.ui_value is None else str(row.ui_value)
    return imgui.input_text("##value", value)


def widget_choice_radio(row: ParameterRow) -> tuple[bool, str]:
    """kind=choice のラジオボタン群を描画し、(changed, value) を返す。"""

    import imgui

    if row.choices is None or not list(row.choices):
        raise ValueError("choice requires non-empty choices")

    choices = [str(x) for x in row.choices]
    current_value = str(row.ui_value)
    changed_any = False
    try:
        selected_index = choices.index(current_value)
    except ValueError:
        # choices 外の値は先頭へ丸める（normalize_input の方針に合わせる）
        selected_index = 0
        changed_any = True

    for i, choice in enumerate(choices):
        clicked = imgui.radio_button(f"{choice}##{i}", i == selected_index)
        if clicked:
            selected_index = i
            changed_any = True
        if i != len(choices) - 1:
            imgui.same_line(0.0, 6.0)

    return changed_any, choices[int(selected_index)]


_KIND_TO_WIDGET: dict[str, WidgetFn] = {
    "float": widget_float_slider,
    "int": widget_int_slider,
    "vec3": widget_vec3_slider,
    "bool": widget_bool_checkbox,
    "string": widget_string_input,
    "choice": widget_choice_radio,
}


def render_value_widget(row: ParameterRow) -> tuple[bool, Any]:
    """row.kind に応じたウィジェットを描画し、(changed, value) を返す。

    Parameters
    ----------
    row : ParameterRow
        GUI 行モデル。

    Returns
    -------
    changed : bool
        値が変更された場合 True。
    value : Any
        変更後の値。

    Raises
    ------
    ValueError
        未知 kind の場合。
    """

    fn = _KIND_TO_WIDGET.get(row.kind)
    if fn is None:
        raise ValueError(f"unknown kind: {row.kind}")
    return fn(row)


def widget_registry() -> dict[str, WidgetFn]:
    """kind→widget 関数マップのコピーを返す。"""

    return dict(_KIND_TO_WIDGET)


def render_parameter_row_4cols(row: ParameterRow) -> tuple[bool, ParameterRow]:
    """1 行（1 key）を 4 列テーブルとして描画し、更新後の row を返す。

    Columns
    -------
    1. label : op#ordinal
    2. control : kind に応じたウィジェット
    3. min-max : ui_min/ui_max
    4. cc override : cc_key/override

    Returns
    -------
    changed : bool
        いずれかの UI 値が変更された場合 True。
    row : ParameterRow
        変更を反映した新しい行モデル。
    """

    import imgui

    # この 1 行（= 1 key）で何かが変更されたかの集計フラグ。
    changed_any = False

    # ParameterRow は immutable（frozen）なので、まずは更新候補をローカル変数として持つ。
    ui_value = row.ui_value
    ui_min = row.ui_min
    ui_max = row.ui_max
    cc_key = row.cc_key
    override = row.override

    CC_KEY_WIDTH = 30
    WIDTH_SPACER = 4

    # テーブル内のウィジェット ID が行ごとに衝突しないよう、push_id でスコープを切る。
    # ここで `row.arg` まで含めているのは、同じ op#ordinal でも arg が異なる可能性があるため。
    imgui.push_id(_row_id(row))
    try:
        # 以降の描画は「この行」に対して行う。
        imgui.table_next_row()

        # --- Column 1: label（op#ordinal のみ表示）---
        imgui.table_set_column_index(0)
        imgui.text(_row_visible_label(row))

        # --- Column 2: control（kind に応じたウィジェット）---
        # slider の visible label はテーブルの label 列で代替するため、ウィジェット側は "##value" を使って非表示にしている。
        imgui.table_set_column_index(1)
        imgui.set_next_item_width(-1)  # 残り幅いっぱい
        changed, value = render_value_widget(row)
        if changed:
            changed_any = True
            ui_value = value

        # --- Column 3: min-max（ui_min/ui_max）---
        imgui.table_set_column_index(2)
        # min-max スライダーは float/vec3/int のみ表示可能。
        if row.kind in {"float", "vec3"}:
            min_display = -1.0 if ui_min is None else float(ui_min)
            max_display = 1.0 if ui_max is None else float(ui_max)
            imgui.set_next_item_width(-1)
            changed_range, min_display, max_display = imgui.drag_float_range2(
                "##ui_range",
                float(min_display),  # current_min
                float(max_display),  # current_max
                0.1,  # speed
                0.0,  # min_value
                0.0,  # max_value
                "%.1f",  # format
                None,
            )
            if changed_range:
                changed_any = True
                ui_min = float(min_display)
                ui_max = float(max_display)
        elif row.kind == "int":
            min_display_i = -10 if ui_min is None else int(ui_min)
            max_display_i = 10 if ui_max is None else int(ui_max)
            imgui.set_next_item_width(-1)
            changed_range, min_display_i, max_display_i = imgui.drag_int_range2(
                "##ui_range",
                int(min_display_i),  # current_min
                int(max_display_i),  # current_max
                0.1,  # speed
                0,  # min_value
                0,  # max_value
            )
            if changed_range:
                changed_any = True
                ui_min = int(min_display_i)
                ui_max = int(max_display_i)

        # --- Column 4: cc override（cc_key/override）---
        imgui.table_set_column_index(3)
        if row.kind in {"bool", "string", "choice"}:
            pass
        elif row.kind == "vec3":
            if isinstance(cc_key, tuple):
                a, b, c = cc_key
                v0 = -1 if a is None else int(a)
                v1 = -1 if b is None else int(b)
                v2 = -1 if c is None else int(c)
            else:
                v0, v1, v2 = -1, -1, -1

            changed_cc, out = imgui.input_int3("##cc_key", int(v0), int(v1), int(v2))
            if changed_cc:
                changed_any = True
                cc_tuple = tuple(None if v < 0 else int(v) for v in out)
                cc_key = None if cc_tuple == (None, None, None) else cc_tuple
            imgui.same_line(0.0, WIDTH_SPACER)
            clicked_override, override = imgui.checkbox("##override", bool(override))
            if clicked_override:
                changed_any = True
        else:
            cc_display = -1 if not isinstance(cc_key, int) else int(cc_key)

            imgui.push_item_width(CC_KEY_WIDTH * 0.88)
            changed_cc, cc_display = imgui.input_int("##cc_key", int(cc_display), 0, 0)
            imgui.pop_item_width()

            imgui.same_line(0.0, WIDTH_SPACER)
            clicked_override, override = imgui.checkbox("##override", bool(override))

            if changed_cc:
                changed_any = True
                cc_key = None if cc_display < 0 else int(cc_display)
            if clicked_override:
                changed_any = True
    finally:
        # push_id と必ず対になるよう finally で pop_id する。
        imgui.pop_id()

    # ローカル変数へ反映した結果を、新しい ParameterRow として返す。
    updated = ParameterRow(
        label=row.label,
        op=row.op,
        arg=row.arg,
        kind=row.kind,
        ui_value=ui_value,
        ui_min=ui_min,
        ui_max=ui_max,
        choices=row.choices,
        cc_key=cc_key,
        override=override,
        ordinal=row.ordinal,
    )

    return changed_any, updated


def render_parameter_table(
    rows: list[ParameterRow],
    *,
    column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
) -> tuple[bool, list[ParameterRow]]:
    """ParameterRow の列を 4 列テーブルとして描画し、更新後の rows を返す。"""

    import imgui

    label_weight, control_weight, range_weight, meta_weight = column_weights
    if (
        label_weight <= 0.0
        or control_weight <= 0.0
        or range_weight <= 0.0
        or meta_weight <= 0.0
    ):
        raise ValueError(f"column_weights must be > 0: {column_weights}")

    changed_any = False
    updated_rows: list[ParameterRow] = []

    table = imgui.begin_table("##parameters", 4, imgui.TABLE_SIZING_STRETCH_PROP)
    opened = getattr(table, "opened", table)
    if not opened:
        return False, rows

    try:
        imgui.table_setup_column(
            "label", imgui.TABLE_COLUMN_WIDTH_STRETCH, float(label_weight)
        )
        imgui.table_setup_column(
            "control", imgui.TABLE_COLUMN_WIDTH_STRETCH, float(control_weight)
        )
        imgui.table_setup_column(
            "min-max",
            imgui.TABLE_COLUMN_WIDTH_STRETCH,
            float(range_weight),
        )
        imgui.table_setup_column(
            "cc",
            imgui.TABLE_COLUMN_WIDTH_STRETCH,
            float(meta_weight),
        )
        imgui.table_headers_row()
        imgui.table_next_row(0, 1)

        for row in rows:
            row_changed, updated = render_parameter_row_4cols(row)
            changed_any = changed_any or row_changed
            updated_rows.append(updated)
    finally:
        imgui.end_table()

    return changed_any, updated_rows


def _row_identity(row: ParameterRow) -> tuple[str, int, str]:
    """store snapshot と突き合わせるための行識別子（op, ordinal, arg）を返す。"""

    return row.op, int(row.ordinal), row.arg


def _apply_updated_rows_to_store(
    store: ParamStore,
    snapshot: dict[ParameterKey, tuple[ParamMeta, object, int, str | None]],
    rows_before: list[ParameterRow],
    rows_after: list[ParameterRow],
) -> None:
    """rows の変更を ParamStore に反映する。

    - ui_min/ui_max の変更は meta に反映する
    - ui_value/override/cc_key の変更は `update_state_from_ui` 経由で反映する
    """

    entry_by_identity: dict[tuple[str, int, str], tuple[ParameterKey, ParamMeta]] = {}
    for key, (meta, _state, ordinal, _label) in snapshot.items():
        entry_by_identity[(key.op, int(ordinal), key.arg)] = (key, meta)

    for before, after in zip(rows_before, rows_after, strict=True):
        key, meta = entry_by_identity[_row_identity(before)]
        effective_meta = meta

        if after.ui_min != before.ui_min or after.ui_max != before.ui_max:
            effective_meta = ParamMeta(
                kind=meta.kind,
                ui_min=after.ui_min,
                ui_max=after.ui_max,
                choices=meta.choices,
            )
            store.set_meta(key, effective_meta)

        if (
            after.ui_value != before.ui_value
            or after.override != before.override
            or after.cc_key != before.cc_key
        ):
            update_state_from_ui(
                store,
                key,
                after.ui_value,
                meta=effective_meta,
                override=after.override,
                cc_key=after.cc_key,
            )


def render_store_parameter_table(
    store: ParamStore,
    *,
    column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
) -> bool:
    """ParamStore の snapshot を 4 列テーブルとして描画し、変更を store に反映する。"""

    snapshot = store.snapshot()
    rows_before = rows_from_snapshot(snapshot)
    changed, rows_after = render_parameter_table(
        rows_before, column_weights=column_weights
    )
    if changed:
        _apply_updated_rows_to_store(store, snapshot, rows_before, rows_after)
    return changed


def _create_imgui_pyglet_renderer(imgui_pyglet_mod: Any, gui_window: Any) -> object:
    """pyglet 用の ImGui renderer を作成する。"""

    factory = getattr(imgui_pyglet_mod, "create_renderer", None)
    if callable(factory):
        return factory(gui_window)
    renderer_type = getattr(imgui_pyglet_mod, "PygletRenderer", None)
    if renderer_type is None:
        raise RuntimeError("imgui.integrations.pyglet renderer is unavailable")
    return renderer_type(gui_window)


def _sync_imgui_io_for_window(imgui_mod: Any, gui_window: Any, *, dt: float) -> None:
    """ImGui IO をウィンドウ状態（サイズ/Retina スケール/Δt）に同期する。"""

    io = imgui_mod.get_io()
    io.delta_time = max(float(dt), 1e-4)

    fb_w, fb_h = gui_window.get_framebuffer_size()
    win_w, win_h = gui_window.width, gui_window.height
    io.display_size = (float(win_w), float(win_h))
    io.display_fb_scale = (
        float(fb_w) / float(max(1, win_w)),
        float(fb_h) / float(max(1, win_h)),
    )


def create_parameter_gui_window(
    *,
    width: int = 800,
    height: int = 480,
    caption: str = "Parameter GUI",
    vsync: bool = True,
) -> Any:
    """Parameter GUI 用の pyglet ウィンドウを生成する。"""

    import pyglet

    gl_cfg = pyglet.gl.Config(double_buffer=True, sample_buffers=1, samples=4)
    return pyglet.window.Window(
        width=int(width),
        height=int(height),
        caption=str(caption),
        resizable=False,
        vsync=bool(vsync),
        config=gl_cfg,
    )


class ParameterGUI:
    """pyimgui で ParamStore を編集するための最小 GUI。

    `draw_frame()` を呼ぶことで 1 フレーム分の UI を描画する。
    """

    def __init__(
        self,
        gui_window: Any,
        *,
        store: ParamStore,
        title: str = "Parameters",
        column_weights: tuple[float, float, float, float] = COLUMN_WEIGHTS_DEFAULT,
    ) -> None:
        """GUI の初期化（ImGui コンテキスト / renderer 作成）。"""

        import imgui

        try:
            from imgui.integrations import pyglet as imgui_pyglet
        except Exception as exc:
            raise RuntimeError(f"imgui.integrations.pyglet を import できない: {exc}")

        self._window = gui_window
        self._store = store
        self._title = str(title)
        self._column_weights = column_weights

        self._imgui = imgui
        self._context = imgui.create_context()
        imgui.style_colors_dark()
        imgui.set_current_context(self._context)

        self._renderer = _create_imgui_pyglet_renderer(imgui_pyglet, gui_window)
        refresh_font = getattr(self._renderer, "refresh_font_texture", None)
        if callable(refresh_font):
            refresh_font()

        import time

        self._prev_time = time.monotonic()
        self._closed = False

    def draw_frame(self) -> bool:
        """1 フレーム分の GUI を描画し、変更があれば store に反映する。"""

        if self._closed:
            return False

        import time

        now = time.monotonic()
        dt = now - self._prev_time
        self._prev_time = now

        imgui = self._imgui
        imgui.set_current_context(self._context)

        self._window.switch_to()
        self._renderer.process_inputs()
        imgui.new_frame()
        _sync_imgui_io_for_window(imgui, self._window, dt=dt)

        imgui.set_next_window_position(0, 0)
        imgui.set_next_window_size(self._window.width, self._window.height)
        imgui.begin(
            self._title,
            flags=imgui.WINDOW_NO_RESIZE | imgui.WINDOW_NO_COLLAPSE,
        )
        changed = render_store_parameter_table(
            self._store,
            column_weights=self._column_weights,
        )
        imgui.end()
        imgui.render()

        import pyglet

        pyglet.gl.glClearColor(0.12, 0.12, 0.12, 1.0)
        self._window.clear()
        self._renderer.render(imgui.get_draw_data())
        self._window.flip()
        return changed

    def close(self) -> None:
        """GUI を終了し、コンテキストとウィンドウを破棄する。"""

        if self._closed:
            return
        self._closed = True

        shutdown = getattr(self._renderer, "shutdown", None)
        if callable(shutdown):
            shutdown()
        self._imgui.destroy_context(self._context)
        self._window.close()
