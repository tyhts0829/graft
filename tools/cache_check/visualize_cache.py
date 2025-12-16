# tools/cache_check/visualize_cache.py
# realize_cache / inflight の挙動をイベントログとして収集し、
# Geometry DAG を DOT グラフとして可視化するための開発用ユーティリティ。

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Generator, Sequence

import graft.core.realize as _realize_module
from graft.core.geometry import Geometry


class RealizeEventType(Enum):
    """realize 実行時のイベント種別。"""

    CACHE_HIT = auto()
    INFLIGHT_WAIT = auto()
    COMPUTE = auto()


@dataclass(slots=True)
class RealizeEvent:
    """単一 GeometryId に対する realize イベント。"""

    geometry_id: str
    op: str
    event_type: RealizeEventType
    depth: int | None = None
    duration_sec: float | None = None


@dataclass
class FrameRealizeLog:
    """1 フレーム分の realize イベントログ。"""

    events: list[RealizeEvent] = field(default_factory=list)

    def record(
        self,
        geometry_id: str,
        op: str,
        event_type: RealizeEventType,
        depth: int | None = None,
        duration_sec: float | None = None,
    ) -> None:
        """イベントを 1 件追加する。"""
        self.events.append(
            RealizeEvent(
                geometry_id=geometry_id,
                op=op,
                event_type=event_type,
                depth=depth,
                duration_sec=duration_sec,
            ),
        )

    def count_events(
        self,
        geometry_id: str,
        event_type: RealizeEventType,
    ) -> int:
        """指定した GeometryId と種別に一致するイベント数を返す。"""
        return sum(
            1
            for event in self.events
            if event.geometry_id == geometry_id and event.event_type == event_type
        )


_thread_state = threading.local()
_tracer_installed: bool = False

_original_cache_get = None
_original_evaluate_node = None
_original_realize_function = None


def _get_current_log() -> FrameRealizeLog | None:
    return getattr(_thread_state, "frame_log", None)


def _set_current_log(frame_log: FrameRealizeLog | None) -> None:
    if frame_log is None:
        if hasattr(_thread_state, "frame_log"):
            delattr(_thread_state, "frame_log")
    else:
        _thread_state.frame_log = frame_log


def _get_eval_depth() -> int:
    return getattr(_thread_state, "eval_depth", 0)


def _set_eval_depth(depth: int) -> None:
    if depth <= 0:
        if hasattr(_thread_state, "eval_depth"):
            delattr(_thread_state, "eval_depth")
    else:
        _thread_state.eval_depth = depth


def _enter_evaluate() -> int:
    depth = _get_eval_depth() + 1
    _set_eval_depth(depth)
    return depth


def _exit_evaluate() -> None:
    depth = _get_eval_depth() - 1
    _set_eval_depth(depth)


def _cache_get_traced(cache_self, geometry_id: str):
    """RealizeCache.get 用ラッパ。キャッシュヒット時にイベントを記録する。"""
    assert _original_cache_get is not None
    value = _original_cache_get(cache_self, geometry_id)
    frame_log = _get_current_log()
    if frame_log is not None and value is not None:
        frame_log.record(
            geometry_id=geometry_id,
            op="",
            event_type=RealizeEventType.CACHE_HIT,
        )
    return value


def _evaluate_node_traced(geometry: Geometry):
    """_evaluate_geometry_node 用ラッパ。COMPUTE イベントを記録する。"""
    assert _original_evaluate_node is not None
    frame_log = _get_current_log()
    if frame_log is None:
        return _original_evaluate_node(geometry)

    depth = _enter_evaluate()
    start = time.perf_counter()
    try:
        return _original_evaluate_node(geometry)
    finally:
        duration = time.perf_counter() - start
        frame_log.record(
            geometry_id=geometry.id,
            op=geometry.op,
            event_type=RealizeEventType.COMPUTE,
            depth=depth,
            duration_sec=duration,
        )
        _exit_evaluate()


def _realize_traced(geometry: Geometry):
    """realize 用ラッパ。INFLIGHT_WAIT イベントを推定して記録する。"""
    assert _original_realize_function is not None
    frame_log = _get_current_log()
    if frame_log is None:
        return _original_realize_function(geometry)

    geometry_id = geometry.id
    before_cache_hits = frame_log.count_events(
        geometry_id=geometry_id,
        event_type=RealizeEventType.CACHE_HIT,
    )
    before_computes = frame_log.count_events(
        geometry_id=geometry_id,
        event_type=RealizeEventType.COMPUTE,
    )

    start = time.perf_counter()
    try:
        return _original_realize_function(geometry)
    finally:
        duration = time.perf_counter() - start
        after_cache_hits = frame_log.count_events(
            geometry_id=geometry_id,
            event_type=RealizeEventType.CACHE_HIT,
        )
        after_computes = frame_log.count_events(
            geometry_id=geometry_id,
            event_type=RealizeEventType.COMPUTE,
        )

        has_new_cache_hit = after_cache_hits > before_cache_hits
        has_new_compute = after_computes > before_computes

        if not has_new_cache_hit and not has_new_compute:
            frame_log.record(
                geometry_id=geometry_id,
                op=geometry.op,
                event_type=RealizeEventType.INFLIGHT_WAIT,
                duration_sec=duration,
            )


def install_realize_tracer() -> None:
    """realize 用トレーサをインストールする（モンキーパッチ）。"""
    global _tracer_installed
    global _original_cache_get, _original_evaluate_node, _original_realize_function

    if _tracer_installed:
        return

    cache_class = type(_realize_module.realize_cache)

    _original_cache_get = cache_class.get
    _original_evaluate_node = _realize_module._evaluate_geometry_node
    _original_realize_function = _realize_module.realize

    cache_class.get = _cache_get_traced  # type: ignore[assignment]
    _realize_module._evaluate_geometry_node = _evaluate_node_traced  # type: ignore[assignment]
    _realize_module.realize = _realize_traced  # type: ignore[assignment]

    _tracer_installed = True


def uninstall_realize_tracer() -> None:
    """realize 用トレーサをアンインストールして元に戻す。"""
    global _tracer_installed
    global _original_cache_get, _original_evaluate_node, _original_realize_function

    if not _tracer_installed:
        return

    cache_class = type(_realize_module.realize_cache)

    assert _original_cache_get is not None
    assert _original_evaluate_node is not None
    assert _original_realize_function is not None

    cache_class.get = _original_cache_get  # type: ignore[assignment]
    _realize_module._evaluate_geometry_node = _original_evaluate_node  # type: ignore[assignment]
    _realize_module.realize = _original_realize_function  # type: ignore[assignment]

    _original_cache_get = None
    _original_evaluate_node = None
    _original_realize_function = None

    _tracer_installed = False


@contextmanager
def frame_logging(frame_log: FrameRealizeLog | None = None) -> Generator[FrameRealizeLog, None, None]:
    """1 フレーム分の realize イベント収集コンテキストを張る。"""
    previous_log = _get_current_log()
    previous_depth = _get_eval_depth()

    if frame_log is None:
        active_log = FrameRealizeLog()
    else:
        active_log = frame_log

    _set_current_log(active_log)
    _set_eval_depth(0)

    try:
        yield active_log
    finally:
        _set_current_log(previous_log)
        _set_eval_depth(previous_depth)


def export_geometry_dag_dot(
    root_geometry: Geometry | Sequence[Geometry],
    frame_log: FrameRealizeLog,
) -> str:
    """Geometry DAG を DOT 形式の文字列としてエクスポートする。"""
    if isinstance(root_geometry, Sequence) and not isinstance(root_geometry, Geometry):
        root_list: list[Geometry] = list(root_geometry)
    else:
        root_list = [root_geometry]  # type: ignore[list-item]

    last_event_by_id: dict[str, RealizeEventType] = {}
    for event in frame_log.events:
        last_event_by_id[event.geometry_id] = event.event_type

    visited_nodes: set[str] = set()
    lines: list[str] = []

    lines.append("digraph GeometryDAG {")
    lines.append("  rankdir=LR;")

    def visit(geometry: Geometry) -> None:
        if geometry.id not in visited_nodes:
            visited_nodes.add(geometry.id)
            event_type = last_event_by_id.get(geometry.id)
            if event_type is RealizeEventType.COMPUTE:
                color = "red"
            elif event_type is RealizeEventType.CACHE_HIT:
                color = "green"
            elif event_type is RealizeEventType.INFLIGHT_WAIT:
                color = "orange"
            else:
                color = "gray"

            label = f"{geometry.op}\\n{geometry.id[:6]}"
            lines.append(
                f'  "{geometry.id}" [label="{label}", style=filled, fillcolor="{color}"];',
            )

        for input_geometry in geometry.inputs:
            lines.append(f'  "{geometry.id}" -> "{input_geometry.id}";')
            visit(input_geometry)

    for root in root_list:
        visit(root)

    # 色の意味を示す凡例クラスタを追加する。
    lines.append("  subgraph cluster_legend {")
    lines.append('    label="Legend";')
    lines.append("    style=rounded;")
    lines.append(
        '    "legend_compute" [label="COMPUTE\\n(計算)", '
        'style=filled, fillcolor="red"];',
    )
    lines.append(
        '    "legend_cache_hit" [label="CACHE_HIT\\n(キャッシュヒット)", '
        'style=filled, fillcolor="green"];',
    )
    lines.append(
        '    "legend_inflight" [label="INFLIGHT_WAIT\\n(inflight 待ち)", '
        'style=filled, fillcolor="orange"];',
    )
    lines.append(
        '    "legend_other" [label="no event\\n(未記録)", '
        'style=filled, fillcolor="gray"];',
    )
    lines.append("  }")

    lines.append("}")
    return "\n".join(lines)


def export_geometry_dag_dot_multiframe(
    root_geometry: Geometry | Sequence[Geometry],
    frame_logs: Sequence[FrameRealizeLog],
    *,
    frame_labels: Sequence[str] | None = None,
) -> str:
    """複数フレーム分の Geometry DAG を 1 つの DOT にまとめてエクスポートする。

    各フレームは subgraph cluster として並べ、同じ GeometryId でも
    フレームごとにノードを複製して色付けを変える。
    """
    if isinstance(root_geometry, Sequence) and not isinstance(root_geometry, Geometry):
        roots_seq: Sequence[Geometry] = list(root_geometry)
    else:
        roots_seq = [root_geometry] * len(frame_logs)  # type: ignore[list-item]

    if len(roots_seq) != len(frame_logs):
        raise ValueError("root_geometry と frame_logs の長さが一致している必要がある")

    if frame_labels is None:
        labels = [f"frame {i}" for i in range(len(frame_logs))]
    else:
        if len(frame_labels) != len(frame_logs):
            raise ValueError("frame_labels と frame_logs の長さが一致している必要がある")
        labels = list(frame_labels)

    lines: list[str] = []
    lines.append("digraph GeometryDAGMultiFrame {")
    lines.append("  rankdir=LR;")

    for index, (root, log, label) in enumerate(zip(roots_seq, frame_logs, labels)):
        last_event_by_id: dict[str, RealizeEventType] = {}
        for event in log.events:
            last_event_by_id[event.geometry_id] = event.event_type

        visited_nodes: set[str] = set()

        lines.append(f'  subgraph cluster_{index} {{')
        lines.append(f'    label="{label}";')
        lines.append("    style=rounded;")

        def visit(geometry: Geometry) -> None:
            node_id = f"f{index}_{geometry.id}"
            if node_id not in visited_nodes:
                visited_nodes.add(node_id)
                event_type = last_event_by_id.get(geometry.id)
                if event_type is RealizeEventType.COMPUTE:
                    color = "red"
                elif event_type is RealizeEventType.CACHE_HIT:
                    color = "green"
                elif event_type is RealizeEventType.INFLIGHT_WAIT:
                    color = "orange"
                else:
                    color = "gray"

                label_inner = f"{geometry.op}\\n{geometry.id[:6]}"
                lines.append(
                    f'    "{node_id}" [label="{label_inner}", '
                    f'style=filled, fillcolor="{color}"];',
                )

            for input_geometry in geometry.inputs:
                parent_id = node_id
                child_id = f"f{index}_{input_geometry.id}"
                lines.append(f'    "{parent_id}" -> "{child_id}";')
                visit(input_geometry)

        visit(root)
        lines.append("  }")

    # 色の意味を示す凡例クラスタを追加する。
    lines.append("  subgraph cluster_legend {")
    lines.append('    label="Legend";')
    lines.append("    style=rounded;")
    lines.append(
        '    "legend_compute" [label="COMPUTE\\n(計算)", '
        'style=filled, fillcolor="red"];',
    )
    lines.append(
        '    "legend_cache_hit" [label="CACHE_HIT\\n(キャッシュヒット)", '
        'style=filled, fillcolor="green"];',
    )
    lines.append(
        '    "legend_inflight" [label="INFLIGHT_WAIT\\n(inflight 待ち)", '
        'style=filled, fillcolor="orange"];',
    )
    lines.append(
        '    "legend_other" [label="no event\\n(未記録)", '
        'style=filled, fillcolor="gray"];',
    )
    lines.append("  }")

    lines.append("}")
    return "\n".join(lines)


def save_geometry_dag_dot(
    path: str,
    root_geometry: Geometry | Sequence[Geometry],
    frame_log: FrameRealizeLog,
) -> None:
    """Geometry DAG の DOT テキストをファイルに保存する。"""
    dot_text = export_geometry_dag_dot(root_geometry=root_geometry, frame_log=frame_log)
    with open(path, "w", encoding="utf-8") as file:
        file.write(dot_text)


def save_geometry_dag_png(
    path: str,
    root_geometry: Geometry | Sequence[Geometry],
    frame_log: FrameRealizeLog,
) -> None:
    """Geometry DAG を PNG 画像として保存する。

    Notes
    -----
    - 実行には Python の graphviz パッケージと、システムにインストールされた Graphviz 本体（dot）が必要。
    - path が *.png の場合は、そのパスに一致するようにファイル名を調整して出力する。
    """
    try:
        from graphviz import Source
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "PNG 出力には 'graphviz' パッケージが必要です。",
        ) from exc

    from pathlib import Path

    dot_text = export_geometry_dag_dot(root_geometry=root_geometry, frame_log=frame_log)

    target_path = Path(path)
    directory = str(target_path.parent)
    if target_path.suffix.lower() == ".png":
        filename = target_path.with_suffix("").name
    else:
        filename = target_path.name

    try:
        Source(dot_text).render(
            filename=filename,
            directory=directory,
            format="png",
            cleanup=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "PNG 出力には Graphviz 本体（'dot' コマンド）が必要です。",
        ) from exc


def save_geometry_dag_png_multiframe(
    path: str,
    root_geometry: Geometry | Sequence[Geometry],
    frame_logs: Sequence[FrameRealizeLog],
    *,
    frame_labels: Sequence[str] | None = None,
) -> None:
    """複数フレーム分の Geometry DAG を PNG として保存する。"""
    try:
        from graphviz import Source
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "PNG 出力には 'graphviz' パッケージが必要です。",
        ) from exc

    from pathlib import Path

    dot_text = export_geometry_dag_dot_multiframe(
        root_geometry=root_geometry,
        frame_logs=frame_logs,
        frame_labels=frame_labels,
    )

    target_path = Path(path)
    directory = str(target_path.parent)
    if target_path.suffix.lower() == ".png":
        filename = target_path.with_suffix("").name
    else:
        filename = target_path.name

    try:
        Source(dot_text).render(
            filename=filename,
            directory=directory,
            format="png",
            cleanup=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "PNG 出力には Graphviz 本体（'dot' コマンド）が必要です。",
        ) from exc


__all__ = [
    "RealizeEventType",
    "RealizeEvent",
    "FrameRealizeLog",
    "install_realize_tracer",
    "uninstall_realize_tracer",
    "frame_logging",
    "export_geometry_dag_dot",
    "save_geometry_dag_dot",
    "save_geometry_dag_png",
    "export_geometry_dag_dot_multiframe",
    "save_geometry_dag_png_multiframe",
]
