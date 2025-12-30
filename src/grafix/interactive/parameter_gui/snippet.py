# どこで: `src/grafix/interactive/parameter_gui/snippet.py`。
# 何を: Parameter GUI の状態から「コピペ可能な Python スニペット文字列」を生成する純粋関数を提供する。
# なぜ: UI（imgui）から分離し、出力仕様をユニットテストで担保するため。

from __future__ import annotations

from collections.abc import Mapping, Sequence

from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.layer_style import (
    LAYER_STYLE_LINE_COLOR,
    LAYER_STYLE_LINE_THICKNESS,
    LAYER_STYLE_OP,
)
from grafix.core.parameters.style import (
    STYLE_BACKGROUND_COLOR,
    STYLE_GLOBAL_LINE_COLOR,
    STYLE_GLOBAL_THICKNESS,
    STYLE_OP,
    coerce_rgb255,
    rgb255_to_rgb01,
)
from grafix.core.parameters.view import ParameterRow
from grafix.core.preset_registry import preset_registry

from .group_blocks import GroupBlock


_CODE_INDENT = "    "


def _indent_code(code: str) -> str:
    """コード文字列を “全行” インデントして返す。"""

    if not code:
        return ""
    has_trailing_newline = code.endswith("\n")
    lines = code.splitlines()
    out = "\n".join(_CODE_INDENT + line for line in lines)
    return out + ("\n" if has_trailing_newline else "")


def _effective_or_ui_value(
    row: ParameterRow,
    *,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
) -> object:
    key = ParameterKey(op=row.op, site_id=row.site_id, arg=row.arg)
    if last_effective_by_key is not None and key in last_effective_by_key:
        return last_effective_by_key[key]
    return row.ui_value


def _py_literal(value: object) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(int(value))
    if isinstance(value, float):
        return repr(float(value))
    if isinstance(value, str):
        return repr(str(value))
    if isinstance(value, tuple):
        return "(" + ", ".join(_py_literal(v) for v in value) + (")" if len(value) != 1 else ",)")
    if isinstance(value, list):
        return "[" + ", ".join(_py_literal(v) for v in value) + "]"
    # numpy scalar などは repr が長くなりやすいので、まずは str に寄せる。
    try:
        return repr(value)
    except Exception:
        return repr(str(value))


def _format_kwargs_call(prefix: str, *, op: str, kwargs: Sequence[tuple[str, str]]) -> str:
    if not kwargs:
        return f"{prefix}{op}()"
    lines = [f"{prefix}{op}("]
    for k, v in kwargs:
        lines.append(f"    {k}={v},")
    lines.append(")")
    return "\n".join(lines)


def snippet_for_block(
    block: GroupBlock,
    *,
    last_effective_by_key: Mapping[ParameterKey, object] | None = None,
    layer_style_name_by_site_id: Mapping[str, str] | None = None,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]] | None = None,
) -> str:
    """1 ブロック（1 ヘッダ相当）のスニペットを返す。"""

    group_type = str(block.group_id[0])
    rows = [it.row for it in block.items]

    if group_type == "style":
        # Style は 1 ヘッダ内に「global + layer_style」が混ざるので、出力は中で分割する。
        style_rows = [r for r in rows if r.op == STYLE_OP]
        layer_rows = [r for r in rows if r.op == LAYER_STYLE_OP]

        out_blocks: list[str] = []

        # --- global style ---
        global_items: list[tuple[str, str]] = []
        by_arg = {str(r.arg): r for r in style_rows}
        if STYLE_BACKGROUND_COLOR in by_arg:
            bg255 = coerce_rgb255(
                _effective_or_ui_value(by_arg[STYLE_BACKGROUND_COLOR], last_effective_by_key=last_effective_by_key)
            )
            global_items.append(("background_color", _py_literal(rgb255_to_rgb01(bg255))))
        if STYLE_GLOBAL_THICKNESS in by_arg:
            thickness = _effective_or_ui_value(by_arg[STYLE_GLOBAL_THICKNESS], last_effective_by_key=last_effective_by_key)
            global_items.append(("line_thickness", _py_literal(thickness)))
        if STYLE_GLOBAL_LINE_COLOR in by_arg:
            line255 = coerce_rgb255(
                _effective_or_ui_value(by_arg[STYLE_GLOBAL_LINE_COLOR], last_effective_by_key=last_effective_by_key)
            )
            global_items.append(("line_color", _py_literal(rgb255_to_rgb01(line255))))

        if global_items:
            out_blocks.append(
                "dict(\n"
                + "\n".join(f"    {k}={v}," for k, v in global_items)
                + "\n)"
            )

        # --- layer style (site_id ごと) ---
        layer_by_site: dict[str, list[ParameterRow]] = {}
        for r in layer_rows:
            layer_by_site.setdefault(str(r.site_id), []).append(r)

        for site_id, site_rows in layer_by_site.items():
            # 行は (arg の並び) が欲しいので明示で揃える。
            by_arg2 = {str(r.arg): r for r in site_rows}

            layer_items: list[tuple[str, str]] = []
            if LAYER_STYLE_LINE_COLOR in by_arg2:
                rgb255 = coerce_rgb255(
                    _effective_or_ui_value(by_arg2[LAYER_STYLE_LINE_COLOR], last_effective_by_key=last_effective_by_key)
                )
                layer_items.append(("color", _py_literal(rgb255_to_rgb01(rgb255))))
            if LAYER_STYLE_LINE_THICKNESS in by_arg2:
                th = _effective_or_ui_value(by_arg2[LAYER_STYLE_LINE_THICKNESS], last_effective_by_key=last_effective_by_key)
                layer_items.append(("thickness", _py_literal(th)))

            if layer_items:
                out_blocks.append(
                    "dict(\n"
                    + "\n".join(f"    {k}={v}," for k, v in layer_items)
                    + "\n)"
                )

        if not out_blocks:
            return ""
        return _indent_code("\n\n".join(out_blocks).rstrip() + "\n")

    if group_type == "preset":
        row0 = rows[0]
        op = str(row0.op)
        call_name = preset_registry.get_display_op(op)
        kwargs = [
            (str(r.arg), _py_literal(_effective_or_ui_value(r, last_effective_by_key=last_effective_by_key)))
            for r in rows
        ]
        return _indent_code(_format_kwargs_call("", op=call_name, kwargs=kwargs).rstrip() + "\n")

    if group_type == "primitive":
        row0 = rows[0]
        op = str(row0.op)
        kwargs = [
            (str(r.arg), _py_literal(_effective_or_ui_value(r, last_effective_by_key=last_effective_by_key)))
            for r in rows
        ]
        return _indent_code(_format_kwargs_call("G.", op=op, kwargs=kwargs).rstrip() + "\n")

    if group_type == "effect_chain":
        steps: dict[tuple[int, str, str], list[ParameterRow]] = {}
        for r in rows:
            step_index = 10**9
            if step_info_by_site is not None:
                info = step_info_by_site.get((str(r.op), str(r.site_id)))
                if info is not None:
                    _cid, idx = info
                    step_index = int(idx)
            key = (int(step_index), str(r.op), str(r.site_id))
            steps.setdefault(key, []).append(r)

        if not steps:
            return ""

        out_lines: list[str] = []
        for i, ((_step_index, op, _site_id), step_rows) in enumerate(
            sorted(steps.items(), key=lambda x: x[0])
        ):
            kwargs = [
                (
                    str(r.arg),
                    _py_literal(
                        _effective_or_ui_value(
                            r, last_effective_by_key=last_effective_by_key
                        )
                    ),
                )
                for r in step_rows
            ]
            if i == 0:
                call = _format_kwargs_call("E.", op=op, kwargs=kwargs)
                out_lines.extend(call.splitlines())
                continue

            out_lines[-1] = out_lines[-1] + f".{op}("
            call_lines = _format_kwargs_call("", op=op, kwargs=kwargs).splitlines()
            if len(call_lines) == 1:
                out_lines[-1] = out_lines[-1].rstrip("(") + "()"
                continue
            out_lines.extend(call_lines[1:])

        return _indent_code("\n".join(out_lines).rstrip() + "\n")

    # fallback
    if rows:
        row0 = rows[0]
        op = str(row0.op)
        kwargs = [
            (str(r.arg), _py_literal(_effective_or_ui_value(r, last_effective_by_key=last_effective_by_key)))
            for r in rows
        ]
        return _indent_code(
            ("dict(\n" + "\n".join(f"    {k}={v}," for k, v in kwargs) + "\n)")
            .rstrip()
            + "\n"
        )
    return ""


__all__ = ["snippet_for_block"]
