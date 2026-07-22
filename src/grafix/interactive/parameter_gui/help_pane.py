# どこで: `src/grafix/interactive/parameter_gui/help_pane.py`。
# 何を: parameter metadata を制作中に参照できる短い Help pane へ整形する。
# なぜ: 説明・単位・推奨範囲を操作対象の近くに出し、試行錯誤を止めないため。

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType

from grafix.core.operation_selector import selector_help_identity
from grafix.core.parameters.view import ParameterRow

NO_DESCRIPTION = "No description is available for this parameter."
NOT_SPECIFIED = "Not specified"


@dataclass(frozen=True, slots=True)
class ParameterHelpContent:
    """Help pane に表示する正規化済みの parameter metadata。"""

    title: str
    identity: str
    description: str
    unit: str
    recommended_range: str


def parameter_help_content(row: ParameterRow) -> ParameterHelpContent:
    """``row`` から metadata 欠損を補った Help 内容を返す。"""

    display_name = "" if row.display_name is None else str(row.display_name).strip()
    title = display_name or row.arg.replace("_", " ").strip().title()
    description = "" if row.description is None else str(row.description).strip()
    unit = "" if row.unit is None else str(row.unit).strip()
    recommended = row.recommended_range
    recommended_text = (
        NOT_SPECIFIED
        if recommended is None
        else f"{float(recommended[0]):g} – {float(recommended[1]):g}"
    )
    return ParameterHelpContent(
        title=title or row.arg,
        identity=selector_help_identity(row.op, row.arg) or f"{row.op}.{row.arg}",
        description=description or NO_DESCRIPTION,
        unit=unit or NOT_SPECIFIED,
        recommended_range=recommended_text,
    )


def render_parameter_help_pane(imgui: ModuleType, row: ParameterRow | None) -> None:
    """selected/hover/focused row のコンパクトな Help pane を描画する。"""

    imgui.text_disabled("HELP")
    if row is None:
        imgui.same_line()
        imgui.text_disabled("Hover, focus, or select a parameter to see help.")
        return

    content = parameter_help_content(row)
    imgui.same_line()
    imgui.text(f"{content.title}  ·  {content.identity}")
    imgui.text_wrapped(content.description)
    imgui.text_disabled(
        f"Unit: {content.unit}    Recommended: {content.recommended_range}"
    )


__all__ = [
    "NO_DESCRIPTION",
    "NOT_SPECIFIED",
    "ParameterHelpContent",
    "parameter_help_content",
    "render_parameter_help_pane",
]
