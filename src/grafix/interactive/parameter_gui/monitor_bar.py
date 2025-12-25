# どこで: `src/grafix/interactive/parameter_gui/monitor_bar.py`。
# 何を: Parameter GUI 上部に表示する監視バー（テキスト 1 行）を描画する。
# なぜ: 実行中の負荷（FPS/CPU/Mem/頂点/ライン）を即座に把握できるようにするため。

from __future__ import annotations

from typing import Any


def _fmt_int(n: int) -> str:
    return f"{int(n):,}"


def render_monitor_bar(
    imgui: Any, snapshot: Any, *, midi_port_name: str | None
) -> None:
    """監視バーを 1 行で描画する。"""

    fps = float(snapshot.fps)
    cpu_percent = float(snapshot.cpu_percent)
    rss_mb = float(snapshot.rss_mb)
    vertices = int(snapshot.vertices)
    lines = int(snapshot.lines)

    text = (
        f"FPS: {fps:5.1f} | CPU: {cpu_percent:5.1f}% | MEM: {rss_mb:,.0f}MB"
        f" | Vtx {_fmt_int(vertices)} | Lines {_fmt_int(lines)}"
    )
    if midi_port_name is not None:
        text += f" | MIDI: {midi_port_name}"
    else:
        text += " | MIDI: (none)"
    imgui.text(str(text))
    imgui.separator()
