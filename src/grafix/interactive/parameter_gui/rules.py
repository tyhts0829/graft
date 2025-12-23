# どこで: `src/grafix/interactive/parameter_gui/rules.py`。
# 何を: Parameter GUI の「列ごとの描画ルール（min-max / cc_key / override）」を 1 箇所に集約する。
# なぜ: `table.py` に例外条件が分散すると、変更時に漏れやすく管理が難しくなるため。

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from grafix.core.parameters.layer_style import LAYER_STYLE_OP
from grafix.core.parameters.style import STYLE_OP
from grafix.core.parameters.view import ParameterRow

MinMaxMode = Literal["none", "float_range", "int_range"]
CcKeyMode = Literal["none", "int", "int3"]


@dataclass(frozen=True, slots=True)
class RowUiRules:
    """ParameterRow をどう描画するかのルール。"""

    minmax: MinMaxMode
    cc_key: CcKeyMode
    show_override: bool


_DISABLE_MINMAX_KEYS: frozenset[tuple[str, str]] = frozenset(
    {
        (STYLE_OP, "global_thickness"),
        (LAYER_STYLE_OP, "line_thickness"),
    }
)


def ui_rules_for_row(row: ParameterRow) -> RowUiRules:
    """行の UI ルールを返す。

    優先順位:
    1) kind によるデフォルト
    2) (op, arg) による例外上書き（意味/セマンティクス）
    """

    # --- 1) kind によるデフォルト ---
    if row.kind in {"float", "vec3"}:
        minmax: MinMaxMode = "float_range"
    elif row.kind == "int":
        minmax = "int_range"
    else:
        minmax = "none"

    if row.kind == "bool":
        cc_key: CcKeyMode = "none"
        show_override = False
    elif row.kind in {"str", "font", "choice"}:
        cc_key = "none"
        show_override = True
    elif row.kind in {"vec3", "rgb"}:
        cc_key = "int3"
        show_override = True
    else:
        cc_key = "int"
        show_override = True

    # --- 2) (op, arg) の例外上書き ---
    if (row.op, row.arg) in _DISABLE_MINMAX_KEYS:
        minmax = "none"

    return RowUiRules(minmax=minmax, cc_key=cc_key, show_override=show_override)
