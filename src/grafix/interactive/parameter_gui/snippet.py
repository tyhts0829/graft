# どこで: `src/grafix/interactive/parameter_gui/snippet.py`。
# 何を: Parameter GUI の状態から「コピペ可能な Python スニペット文字列」を生成する純粋関数を提供する。
# なぜ: UI（imgui）から分離し、出力仕様をユニットテストで担保するため。

"""Parameter GUI の状態から「コピペ可能な Python スニペット」を生成する。

このモジュールは、UI（imgui など）の描画やイベント処理から切り離した **純粋関数** を提供する。
入力は Parameter GUI が持つ不変 layout と indexed `ParameterRow` で、出力は
`P.` / `G.` / `E.` などの呼び出しを組み立てた **Python コード断片（文字列）**。

生成されるスニペットは「関数内へ貼る」用途を想定し、全行が `_CODE_INDENT` だけインデントされている。
（返り値が空文字の場合は、そのブロックからは出力しない）

副作用
------
なし。入力から文字列を組み立てて返すだけ。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Literal, assert_never

from grafix.core.operation_selector import (
    decode_selector_param_key,
    selector_effect_n_inputs,
    selector_kind,
)
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
    rgb255_to_rgb01,
    validate_rgb255,
)
from grafix.core.parameters.view import ParameterRow

from .catalog import ParameterGuiCatalog, current_parameter_gui_catalog
from .group_blocks import GroupBlockLayout
from .grouping import GroupType


_CODE_INDENT = "    "


def _indent_code(code: str) -> str:
    """コード文字列を “全行” インデントして返す。

    Parameters
    ----------
    code:
        インデントしたいコード文字列（複数行可）。

    Returns
    -------
    str
        各行の先頭に `_CODE_INDENT` を付与した文字列。
        入力が末尾改行で終わる場合は、その改行を保持する。

    Notes
    -----
    `splitlines()` は末尾の空行（トレーリング改行）を落とすため、
    改行の有無を別途保持してから組み立てる。
    """

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
    """スニペットに埋め込む値（実効値 or UI 値）を返す。

    Parameter GUI では、表示・編集用の値（`row.ui_value`）と、内部で確定した実効値が
    ずれることがある（入力正規化、型変換、choice の丸めなど）。
    `last_effective_by_key` が渡されていれば、その実効値を優先してスニペットへ出力する。

    Parameters
    ----------
    row:
        GUI の 1 行（op/site_id/arg/ui_value など）。
    last_effective_by_key:
        `ParameterKey(op, site_id, arg)` -> 実効値 のマップ。未指定なら常に UI 値を使う。

    Returns
    -------
    object
        スニペットに埋め込む値。
    """

    # `ParameterRow` 自体はキーではないので、(op, site_id, arg) から ParameterKey を復元して照合する。
    key = ParameterKey(op=row.op, site_id=row.site_id, arg=row.arg)
    if last_effective_by_key is not None and key in last_effective_by_key:
        return last_effective_by_key[key]
    return row.ui_value


def _py_literal(value: object) -> str:
    """canonical parameter 値を Python リテラルへ変換する。

    Copy Code に未知オブジェクトの ``repr`` を混ぜると、実行不能な文字列や
    メモリアドレス依存の出力を生成し得る。Parameter 値と selector 用の内部 dict だけを
    明示的に受理し、それ以外は拒否する。

    Parameters
    ----------
    value:
        変換対象。

    Returns
    -------
    str
        Python ソースコードとして埋め込める見た目の文字列。
    """

    if value is None:
        return "None"
    if type(value) is bool:
        return "True" if value else "False"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("snippet の float は有限値である必要があります")
        return repr(value)
    if type(value) is str:
        return repr(value)
    if type(value) is tuple:
        # 1 要素タプルは末尾カンマが無いとただの括弧式になるため、必ず付ける。
        return "(" + ", ".join(_py_literal(v) for v in value) + (")" if len(value) != 1 else ",)")
    if type(value) is dict:
        items = ", ".join(f"{_py_literal(k)}: {_py_literal(v)}" for k, v in value.items())
        return "{" + items + "}"
    raise TypeError(
        "snippet の値は None、exact scalar、tuple、plain dict "
        f"のいずれかである必要があります: {type(value).__name__}"
    )


def _format_kwargs_call(prefix: str, *, op: str, kwargs: Sequence[tuple[str, str]]) -> str:
    """`prefix + op` の呼び出し（kwargs）を、読みやすい複数行形式で整形する。

    Parameters
    ----------
    prefix:
        先頭に付ける接頭辞（例: `"G."` / `"E(name='foo')."`）。
    op:
        呼び出し名（例: `"circle"` / `"blur"`）。
    kwargs:
        `(key, value_literal)` の列。`value_literal` は `_py_literal()` 済みを想定する。

    Returns
    -------
    str
        `prefix + op(...)` のコード文字列（まだ `_indent_code` は掛けない）。

    Notes
    -----
    ここでのインデントは「括弧の内側」を 4 スペースで揃えるだけ。
    最終的な貼り付け用インデントは `_indent_code()` が担う。
    """

    if not kwargs:
        return f"{prefix}{op}()"
    lines = [f"{prefix}{op}("]
    for k, v in kwargs:
        lines.append(f"    {k}={v},")
    lines.append(")")
    return "\n".join(lines)


def _with_explicit_key(
    kwargs: list[tuple[str, str]],
    *,
    op: str,
    site_id: str,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> list[tuple[str, str]]:
    """指定された重要/反復 group の ``key=`` を kwargs 末尾へ加える。"""

    if explicit_key_by_site is None:
        return kwargs
    group = (op, site_id)
    if group not in explicit_key_by_site:
        return kwargs
    explicit_key = explicit_key_by_site[group]
    if not isinstance(explicit_key, (str, int)):
        raise TypeError("snippet の explicit key は str|int である必要があります")
    return [*kwargs, ("key", _py_literal(explicit_key))]


def _selector_kwargs(
    rows: Sequence[ParameterRow],
    *,
    catalog: ParameterGuiCatalog,
    op: str,
    site_id: str,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> tuple[list[tuple[str, str]], str | None]:
    """selector rows を公開 ``select(...)`` kwargs へ戻す。

    GUI 行から復元できない引数を target が受け取る場合は、不完全な呼び出しを
    生成せず、手動で ``params_by_target`` を維持するための NOTE を返す。
    """

    target_row = next((row for row in rows if row.arg == "target"), None)
    if target_row is None:
        return [], (
            "# NOTE: selector の target 行が無いため Copy Code を生成できません。"
            "元コードの select(...) を確認してください。"
        )
    target = str(
        _effective_or_ui_value(
            target_row,
            last_effective_by_key=last_effective_by_key,
        )
    )
    kind = selector_kind(op)
    if kind is None:
        raise ValueError(f"selector operation ではありません: {op!r}")
    try:
        target_spec = catalog.resolve_operation(kind, target)
    except KeyError:
        target_spec = None
    if target_spec is None:
        return [], (
            f"# NOTE: selector target {target!r} が現在の catalog に無いため "
            "Copy Code を生成できません。target と登録順を確認してください。"
        )

    non_gui_args = tuple(
        arg for arg in target_spec.accepted_args if arg not in target_spec.schema.meta
    )
    if non_gui_args or target_spec.accepts_var_kwargs:
        details: list[str] = []
        if non_gui_args:
            details.append("GUI 非公開引数 " + ", ".join(repr(arg) for arg in non_gui_args))
        if target_spec.accepts_var_kwargs:
            details.append("GUI から復元できない **kwargs")
        detail = " と ".join(details)
        public_api = "G.select" if kind == "primitive" else "E.select"
        return [], (
            f"# NOTE: {public_api} target {target!r} は {detail} を受け取るため、"
            "安全な Copy Code を生成できません。"
            f"元コードの params_by_target[{target!r}] を手動で保持してください。"
        )

    target_params: dict[str, object] = {}
    for row in rows:
        decoded = decode_selector_param_key(row.arg)
        if decoded is None:
            continue
        row_target, original_arg = decoded
        if row_target != target:
            continue
        target_params[original_arg] = _effective_or_ui_value(
            row,
            last_effective_by_key=last_effective_by_key,
        )

    kwargs: list[tuple[str, str]] = [("target", _py_literal(target))]
    n_inputs = selector_effect_n_inputs(op)
    if n_inputs is not None:
        kwargs.append(("n_inputs", str(int(n_inputs))))
    if target_params:
        kwargs.append(
            (
                "params_by_target",
                _py_literal({target: target_params}),
            )
        )
    return _with_explicit_key(
        kwargs,
        op=op,
        site_id=site_id,
        explicit_key_by_site=explicit_key_by_site,
    ), None


def _selector_rows_for_site(
    indexed_rows: Sequence[ParameterRow],
    *,
    op: str,
    site_id: str,
) -> list[ParameterRow]:
    """selector site の非表示行を含む全 ParameterRow を返す。"""

    site_rows = [row for row in indexed_rows if row.op == op and row.site_id == site_id]
    if not site_rows:
        raise AssertionError("layout が参照する selector site が model rows に存在しません")
    return site_rows


def _kwargs_for_rows(
    rows: Sequence[ParameterRow],
    *,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> list[tuple[str, str]]:
    """同じ呼び出しに属する rows を順序付き kwargs へ変換する。"""

    row0 = rows[0]
    kwargs = [
        (
            row.arg,
            _py_literal(
                _effective_or_ui_value(
                    row,
                    last_effective_by_key=last_effective_by_key,
                )
            ),
        )
        for row in rows
    ]
    return _with_explicit_key(
        kwargs,
        op=row0.op,
        site_id=row0.site_id,
        explicit_key_by_site=explicit_key_by_site,
    )


def _first_raw_label(
    rows: Sequence[ParameterRow],
    raw_label_by_site: Mapping[tuple[str, str], str] | None,
) -> str:
    """rows の順で最初に見つかる空でない生ラベルを返す。"""

    if raw_label_by_site is None:
        return ""
    for row in rows:
        raw_label = raw_label_by_site.get((row.op, row.site_id))
        if raw_label is not None and raw_label.strip():
            return raw_label.strip()
    return ""


def _labeled_prefix(
    namespace: str,
    rows: Sequence[ParameterRow],
    *,
    raw_label_by_site: Mapping[tuple[str, str], str] | None,
    omit_label: str | None = None,
) -> str:
    """raw label を反映した ``P.`` / ``G.`` / ``E.`` prefix を返す。"""

    raw_label = _first_raw_label(rows, raw_label_by_site)
    if not raw_label or raw_label == omit_label:
        return f"{namespace}."
    return f"{namespace}(name={_py_literal(raw_label)})."


def _operation_call_for_rows(
    rows: Sequence[ParameterRow],
    indexed_rows: Sequence[ParameterRow],
    *,
    selector_group_kind: Literal["primitive", "effect"],
    catalog: ParameterGuiCatalog,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> tuple[str, list[tuple[str, str]], str | None]:
    """通常 operation/selector を公開 call 名、kwargs、任意 NOTE へ変換する。"""

    row0 = rows[0]
    if selector_kind(row0.op) == selector_group_kind:
        selector_rows = _selector_rows_for_site(
            indexed_rows,
            op=row0.op,
            site_id=row0.site_id,
        )
        kwargs, note = _selector_kwargs(
            selector_rows,
            catalog=catalog,
            op=row0.op,
            site_id=row0.site_id,
            last_effective_by_key=last_effective_by_key,
            explicit_key_by_site=explicit_key_by_site,
        )
        return "select", kwargs, note
    return (
        row0.op,
        _kwargs_for_rows(
            rows,
            last_effective_by_key=last_effective_by_key,
            explicit_key_by_site=explicit_key_by_site,
        ),
        None,
    )


def _emit_style_snippet(
    rows: Sequence[ParameterRow],
    *,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
    raw_label_by_site: Mapping[tuple[str, str], str] | None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> str:
    """global/layer style block の未インデントコードを返す。"""

    style_rows = [row for row in rows if row.op == STYLE_OP]
    layer_rows = [row for row in rows if row.op == LAYER_STYLE_OP]
    by_arg = {row.arg: row for row in style_rows}
    global_items: list[tuple[str, str]] = []
    if STYLE_BACKGROUND_COLOR in by_arg:
        rgb255 = validate_rgb255(
            _effective_or_ui_value(
                by_arg[STYLE_BACKGROUND_COLOR],
                last_effective_by_key=last_effective_by_key,
            )
        )
        global_items.append(("background_color", _py_literal(rgb255_to_rgb01(rgb255))))
    if STYLE_GLOBAL_THICKNESS in by_arg:
        thickness = _effective_or_ui_value(
            by_arg[STYLE_GLOBAL_THICKNESS],
            last_effective_by_key=last_effective_by_key,
        )
        global_items.append(("line_thickness", _py_literal(thickness)))
    if STYLE_GLOBAL_LINE_COLOR in by_arg:
        rgb255 = validate_rgb255(
            _effective_or_ui_value(
                by_arg[STYLE_GLOBAL_LINE_COLOR],
                last_effective_by_key=last_effective_by_key,
            )
        )
        global_items.append(("line_color", _py_literal(rgb255_to_rgb01(rgb255))))

    output_lines: list[str] = []
    if global_items:
        output_lines.append("# --- run(...) ---")
        output_lines.extend(f"{key}={value}," for key, value in global_items)

    layer_by_site: dict[str, list[ParameterRow]] = {}
    for row in layer_rows:
        layer_by_site.setdefault(row.site_id, []).append(row)

    named_layer_blocks: list[list[str]] = []
    unnamed_layer_site_ids: list[str] = []
    for site_id, site_rows in layer_by_site.items():
        layer_name = _first_raw_label(site_rows, raw_label_by_site)
        if not layer_name:
            unnamed_layer_site_ids.append(site_id)
            continue

        by_arg = {row.arg: row for row in site_rows}
        layer_items: list[tuple[str, str]] = []
        if LAYER_STYLE_LINE_COLOR in by_arg:
            rgb255 = validate_rgb255(
                _effective_or_ui_value(
                    by_arg[LAYER_STYLE_LINE_COLOR],
                    last_effective_by_key=last_effective_by_key,
                )
            )
            layer_items.append(("color", _py_literal(rgb255_to_rgb01(rgb255))))
        if LAYER_STYLE_LINE_THICKNESS in by_arg:
            thickness = _effective_or_ui_value(
                by_arg[LAYER_STYLE_LINE_THICKNESS],
                last_effective_by_key=last_effective_by_key,
            )
            layer_items.append(("thickness", _py_literal(thickness)))
        layer_items = _with_explicit_key(
            layer_items,
            op=LAYER_STYLE_OP,
            site_id=site_id,
            explicit_key_by_site=explicit_key_by_site,
        )
        if layer_items:
            named_layer_blocks.append(
                [
                    "# --- L(name=...).layer(..., color/thickness) ---",
                    f"# {layer_name}: paste into `L(name={_py_literal(layer_name)}).layer(...)`",
                    *[f"{key}={value}," for key, value in layer_items],
                ]
            )

    if named_layer_blocks:
        if output_lines:
            output_lines.append("")
        output_lines.extend(named_layer_blocks[0])
        for block_lines in named_layer_blocks[1:]:
            output_lines.append("")
            output_lines.extend(block_lines[1:])

    if unnamed_layer_site_ids:
        if output_lines:
            output_lines.append("")
        output_lines.append(
            "# NOTE: 名前の無い layer_style は snippet に出しません。"
            "（`L(name=...).layer(...)` でラベル付けすると出ます）"
        )
    return "\n".join(output_lines)


def _emit_preset_snippet(
    rows: Sequence[ParameterRow],
    *,
    catalog: ParameterGuiCatalog,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
    raw_label_by_site: Mapping[tuple[str, str], str] | None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> str:
    """preset block の未インデントコードを返す。"""

    row0 = rows[0]
    entry = catalog.resolve(row0.op)
    if entry is None or entry.kind != "preset":
        raise LookupError(f"preset catalog entry が見つかりません: {row0.op!r}")
    prefix = _labeled_prefix(
        "P",
        rows,
        raw_label_by_site=raw_label_by_site,
        omit_label=entry.call_name,
    )
    kwargs = _kwargs_for_rows(
        rows,
        last_effective_by_key=last_effective_by_key,
        explicit_key_by_site=explicit_key_by_site,
    )
    return _format_kwargs_call(prefix, op=entry.call_name, kwargs=kwargs)


def _emit_primitive_snippet(
    rows: Sequence[ParameterRow],
    indexed_rows: Sequence[ParameterRow],
    *,
    catalog: ParameterGuiCatalog,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
    raw_label_by_site: Mapping[tuple[str, str], str] | None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> str:
    """primitive block の未インデントコードまたは selector NOTE を返す。"""

    prefix = _labeled_prefix("G", rows, raw_label_by_site=raw_label_by_site)
    call_op, kwargs, note = _operation_call_for_rows(
        rows,
        indexed_rows,
        selector_group_kind="primitive",
        catalog=catalog,
        last_effective_by_key=last_effective_by_key,
        explicit_key_by_site=explicit_key_by_site,
    )
    return note if note is not None else _format_kwargs_call(prefix, op=call_op, kwargs=kwargs)


def _emit_effect_chain_snippet(
    rows: Sequence[ParameterRow],
    indexed_rows: Sequence[ParameterRow],
    *,
    catalog: ParameterGuiCatalog,
    last_effective_by_key: Mapping[ParameterKey, object] | None,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]] | None,
    raw_label_by_site: Mapping[tuple[str, str], str] | None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None,
) -> str:
    """effect-chain block の未インデントコードまたは selector NOTE を返す。"""

    steps: dict[tuple[int, str, str], list[ParameterRow]] = {}
    for row in rows:
        step_index = 10**9
        if step_info_by_site is not None:
            info = step_info_by_site.get((row.op, row.site_id))
            if info is not None:
                _chain_id, step_index = info
        steps.setdefault((step_index, row.op, row.site_id), []).append(row)
    if not steps:
        return ""

    prefix = _labeled_prefix("E", rows, raw_label_by_site=raw_label_by_site)
    output_lines: list[str] = []
    for index, ((_step_index, _op, _site_id), step_rows) in enumerate(sorted(steps.items())):
        call_op, kwargs, note = _operation_call_for_rows(
            step_rows,
            indexed_rows,
            selector_group_kind="effect",
            catalog=catalog,
            last_effective_by_key=last_effective_by_key,
            explicit_key_by_site=explicit_key_by_site,
        )
        if note is not None:
            return note
        if index == 0:
            output_lines.extend(_format_kwargs_call(prefix, op=call_op, kwargs=kwargs).splitlines())
            continue

        output_lines[-1] += f".{call_op}("
        call_lines = _format_kwargs_call("", op=call_op, kwargs=kwargs).splitlines()
        if len(call_lines) == 1:
            output_lines[-1] = output_lines[-1].rstrip("(") + "()"
        else:
            output_lines.extend(call_lines[1:])
    return "\n".join(output_lines)


def snippet_for_block(
    block: GroupBlockLayout,
    indexed_rows: Sequence[ParameterRow],
    *,
    catalog: ParameterGuiCatalog | None = None,
    last_effective_by_key: Mapping[ParameterKey, object] | None = None,
    step_info_by_site: Mapping[tuple[str, str], tuple[str, int]] | None = None,
    raw_label_by_site: Mapping[tuple[str, str], str] | None = None,
    explicit_key_by_site: Mapping[tuple[str, str], str | int] | None = None,
) -> str:
    """1 ブロック（collapsing header 1 つ相当）のスニペットを返す。

    Parameters
    ----------
    block:
        連続する group を表す不変 layout。
    indexed_rows:
        layout item の ``row_index`` が参照する table model の全行。selector の
        ``ui_visible`` で非表示になった target 引数もここから復元する。
    last_effective_by_key:
        UI 値ではなく実効値で出したい場合の参照マップ。
    step_info_by_site:
        effect_chain の「並び順」を安定させるための補助情報。
        `((op, site_id) -> (chain_id, step_index))` を想定する。
    raw_label_by_site:
        GUI 上でユーザーが付けた “生のラベル”。
        `((op, site_id) -> label)` の形で渡すと、`P(name=...)` / `G(name=...)` / `E(name=...)` などに反映される。
    explicit_key_by_site:
        重要 parameter や loop group に固定 semantic key を出すための map。
        ``(op, site_id) -> key`` を指定した呼び出しだけに ``key=`` を追加する。

    Returns
    -------
    str
        貼り付け用に `_CODE_INDENT` でインデント済みのコード断片。
        生成対象が無い場合は空文字。

    Notes
    -----
    - `group_type` ごとに出力フォーマットが異なる（style / preset / primitive / effect_chain）。
    - 返り値は **末尾改行あり**（空文字を除く）。
    """

    selected_catalog = current_parameter_gui_catalog() if catalog is None else catalog
    if type(selected_catalog) is not ParameterGuiCatalog:
        raise TypeError("catalog は exact ParameterGuiCatalog である必要があります")
    group_type = block.group_id[0]
    rows = [indexed_rows[item.row_index] for item in block.items]

    if group_type is GroupType.STYLE:
        code = _emit_style_snippet(
            rows,
            last_effective_by_key=last_effective_by_key,
            raw_label_by_site=raw_label_by_site,
            explicit_key_by_site=explicit_key_by_site,
        )
    elif group_type is GroupType.PRESET:
        code = _emit_preset_snippet(
            rows,
            catalog=selected_catalog,
            last_effective_by_key=last_effective_by_key,
            raw_label_by_site=raw_label_by_site,
            explicit_key_by_site=explicit_key_by_site,
        )
    elif group_type is GroupType.PRIMITIVE:
        code = _emit_primitive_snippet(
            rows,
            indexed_rows,
            catalog=selected_catalog,
            last_effective_by_key=last_effective_by_key,
            raw_label_by_site=raw_label_by_site,
            explicit_key_by_site=explicit_key_by_site,
        )
    elif group_type is GroupType.EFFECT_CHAIN:
        code = _emit_effect_chain_snippet(
            rows,
            indexed_rows,
            catalog=selected_catalog,
            last_effective_by_key=last_effective_by_key,
            step_info_by_site=step_info_by_site,
            raw_label_by_site=raw_label_by_site,
            explicit_key_by_site=explicit_key_by_site,
        )
    else:
        assert_never(group_type)

    return "" if not code else _indent_code(code.rstrip() + "\n")


__all__ = ["snippet_for_block"]
