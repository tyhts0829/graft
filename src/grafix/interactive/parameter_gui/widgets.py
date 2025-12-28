# どこで: `src/grafix/interactive/parameter_gui/widgets.py`。
# 何を: ParameterRow.kind を pyimgui の値ウィジェットへ対応付けて描画する。
# なぜ: kind ごとの UI 実装を閉じ込め、テーブル描画から分離するため。

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from grafix.core.font_resolver import list_font_choices
from grafix.core.parameters.view import ParameterRow

WidgetFn = Callable[[ParameterRow], tuple[bool, Any]]

_FONT_FILTER_BY_KEY: dict[tuple[str, str, str], str] = {}


def _query_tokens_and(query: str) -> tuple[str, ...]:
    """フィルタークエリを AND 用トークン列へ正規化して返す。"""
    tokens = [t for t in str(query).lower().split() if t]
    return tuple(tokens)


def _filter_choices_by_query_and(
    choices: tuple[tuple[str, str, bool, str], ...], *, query: str
) -> list[tuple[str, str, bool, str]]:
    """AND クエリで choices を絞り込んで返す（純粋関数）。"""
    tokens = _query_tokens_and(query)
    if not tokens:
        return list(choices)
    out: list[tuple[str, str, bool, str]] = []
    for item in choices:
        _stem, _rel, _is_ttc, search_key = item
        if all(t in search_key for t in tokens):
            out.append(item)
    return out


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

    # ImGui の slider_int は min/max が int32 の “半分レンジ” 以内であることを要求する。
    # （範囲外だと assertion error でクラッシュする）
    # 参照: imgui-cpp/imgui_widgets.cpp の slider_int 実装。
    min_value = max(-1_073_741_824, min(1_073_741_823, min_value))
    max_value = max(-1_073_741_824, min(1_073_741_823, max_value))
    if min_value > max_value:
        min_value, max_value = max_value, min_value
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


def _as_rgb255(value: Any) -> tuple[int, int, int]:
    """値を長さ 3 の int タプル `(r, g, b)`（0..255）に変換して返す。"""

    try:
        r, g, b = value  # type: ignore[misc]
    except Exception as exc:
        raise ValueError(f"rgb ui_value must be a length-3 sequence: {value!r}") from exc

    out: list[int] = []
    for v in (r, g, b):
        iv = int(v)
        iv = max(0, min(255, iv))
        out.append(iv)
    return int(out[0]), int(out[1]), int(out[2])


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

    import imgui  # type: ignore[import-untyped]

    raw_value = row.ui_value
    if isinstance(raw_value, (list, tuple)) and raw_value:
        raw_value = raw_value[0]
    value = float(raw_value)
    min_value, max_value = _float_slider_range(row)
    if str(row.arg).endswith("thickness"):
        return imgui.slider_float(
            "##value",
            float(value),
            float(min_value),
            float(max_value),
            format="%.6f",
            flags=imgui.SLIDER_FLAGS_ALWAYS_CLAMP,
        )
    return imgui.slider_float("##value", float(value), float(min_value), float(max_value))


def widget_int_slider(row: ParameterRow) -> tuple[bool, int]:
    """kind=int のスライダーを描画し、(changed, value) を返す。"""

    import imgui  # type: ignore[import-untyped]

    raw_value = row.ui_value
    if isinstance(raw_value, (list, tuple)) and raw_value:
        raw_value = raw_value[0]
    value = int(raw_value)
    min_value, max_value = _int_slider_range(row)
    return imgui.slider_int("##value", int(value), int(min_value), int(max_value))


def widget_vec3_slider(row: ParameterRow) -> tuple[bool, tuple[float, float, float]]:
    """kind=vec3 のスライダーを描画し、(changed, value) を返す。"""

    import imgui  # type: ignore[import-untyped]

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


def widget_rgb_color_edit3(row: ParameterRow) -> tuple[bool, tuple[int, int, int]]:
    """kind=rgb のカラーピッカーを描画し、(changed, value) を返す。"""

    import imgui  # type: ignore[import-untyped]

    r, g, b = _as_rgb255(row.ui_value)
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    flags = (
        imgui.COLOR_EDIT_UINT8 | imgui.COLOR_EDIT_DISPLAY_RGB | imgui.COLOR_EDIT_INPUT_RGB
    )
    changed, out = imgui.color_edit3("##value", float(rf), float(gf), float(bf), flags=flags)
    if not changed:
        return False, (r, g, b)

    r2, g2, b2 = out
    r_out = int(round(float(r2) * 255.0))
    g_out = int(round(float(g2) * 255.0))
    b_out = int(round(float(b2) * 255.0))
    r_out = max(0, min(255, r_out))
    g_out = max(0, min(255, g_out))
    b_out = max(0, min(255, b_out))
    return True, (r_out, g_out, b_out)


def widget_bool_checkbox(row: ParameterRow) -> tuple[bool, bool]:
    """kind=bool のチェックボックスを描画し、(changed, value) を返す。"""

    import imgui  # type: ignore[import-untyped]

    clicked, state = imgui.checkbox("##value", bool(row.ui_value))
    return clicked, bool(state)


def widget_string_input(row: ParameterRow) -> tuple[bool, str]:
    """kind=str のテキスト入力を描画し、(changed, value) を返す。"""

    import imgui  # type: ignore[import-untyped]

    value = "" if row.ui_value is None else str(row.ui_value)
    line_count = int(value.count("\n")) + 1
    visible_lines = max(3, min(8, line_count))
    height = float(imgui.get_text_line_height()) * float(visible_lines) + 8.0
    return imgui.input_text_multiline("##value", value, -1, 0.0, float(height))


def widget_font_picker(row: ParameterRow) -> tuple[bool, str]:
    """kind=font のフォント選択を描画し、(changed, value) を返す。

    Notes
    -----
    control 列に以下を縦に描画する。
    - フィルター入力（AND: スペース区切り）
    - フィルター結果のプルダウン（表示は stem のみ）
    """

    import imgui  # type: ignore[import-untyped]

    key = (str(row.op), str(row.site_id), str(row.arg))
    filter_text = _FONT_FILTER_BY_KEY.get(key, "")

    # --- filter input ---
    imgui.set_next_item_width(-1)
    changed_filter, new_filter = imgui.input_text("##font_filter", str(filter_text))
    if changed_filter:
        _FONT_FILTER_BY_KEY[key] = str(new_filter)
        filter_text = str(new_filter)

    # --- dropdown ---
    choices = list_font_choices()
    filtered = _filter_choices_by_query_and(choices, query=str(filter_text))

    current_value = "" if row.ui_value is None else str(row.ui_value)
    preview = Path(current_value).stem if current_value else ""
    if not preview:
        preview = "(default)"

    imgui.set_next_item_width(-1)

    changed_value = False
    value_out = current_value

    if imgui.begin_combo("##font_combo", str(preview)):
        try:
            if not filtered:
                imgui.text("No match")
            else:
                for stem, rel, _is_ttc, _search_key in filtered:
                    selected = str(rel) == str(current_value)
                    label = f"{stem}##{rel}"
                    clicked, _selected_now = imgui.selectable(label, selected)
                    if clicked:
                        value_out = str(rel)
                        changed_value = True
                    if selected:
                        imgui.set_item_default_focus()
        finally:
            imgui.end_combo()

    return changed_value, str(value_out)


def widget_choice_radio(row: ParameterRow) -> tuple[bool, str]:
    """kind=choice のラジオボタン群を描画し、(changed, value) を返す。"""

    import imgui  # type: ignore[import-untyped]

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
    "rgb": widget_rgb_color_edit3,
    "bool": widget_bool_checkbox,
    "str": widget_string_input,
    "font": widget_font_picker,
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
