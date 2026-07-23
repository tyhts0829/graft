"""Parameter row の有効値 source を表示・検索用の短い表記へ変換する。"""

from __future__ import annotations

from grafix.core.parameters.source import ValueSource
from grafix.core.parameters.view import ParameterRow


def source_badge_for_row(row: ParameterRow, last_source: ValueSource | None) -> str:
    """現在の control 状態と両立する有効値 source badge を返す。"""

    # last_source は直近に実現した frame の観測値。Undo/Redo や
    # Snapshot Load の直後は row だけが新状態に進んでいるため、
    # 現在の control 状態と両立する観測値だけを使う。
    cc_can_be_source = (
        isinstance(row.cc_key, int) and row.kind in {"float", "int", "choice"}
    ) or (
        isinstance(row.cc_key, tuple)
        and row.kind == "vec3"
        and any(cc is not None for cc in row.cc_key)
    )
    if last_source in {"midi_live", "midi_frozen"} and cc_can_be_source:
        return "MIDI LIVE" if last_source == "midi_live" else "MIDI FROZEN"
    if last_source == "ui" and row.override:
        return "UI"
    if last_source == "code" and not row.override:
        return "CODE"
    return "UI" if row.override else "CODE"


__all__ = ["source_badge_for_row"]
