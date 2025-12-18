"""tools.cache_check.visualize_cache に関するテスト群。"""

from __future__ import annotations

import pytest

from grafix.core.geometry import Geometry
from grafix.core.realize import _inflight, _inflight_lock, realize, realize_cache
from grafix.core.primitives import circle as _circle_module  # noqa: F401
from tools.cache_check import (
    FrameRealizeLog,
    RealizeEventType,
    export_geometry_dag_dot,
    export_geometry_dag_dot_multiframe,
    frame_logging,
    install_realize_tracer,
    uninstall_realize_tracer,
)


@pytest.fixture(autouse=True)
def clear_realize_state() -> None:
    """各テスト前後で realize_cache と inflight をクリアする。"""
    with realize_cache._lock:  # type: ignore[attr-defined]
        realize_cache._items.clear()  # type: ignore[attr-defined]
    with _inflight_lock:
        _inflight.clear()
    yield
    with realize_cache._lock:  # type: ignore[attr-defined]
        realize_cache._items.clear()  # type: ignore[attr-defined]
    with _inflight_lock:
        _inflight.clear()


@pytest.fixture
def tracer_installed() -> None:
    """visualize_cache のトレーサを一時的にインストールする。"""
    install_realize_tracer()
    try:
        yield
    finally:
        uninstall_realize_tracer()


def test_visualize_cache_records_compute_and_cache_hit(tracer_installed: None) -> None:
    """1 回目 COMPUTE, 2 回目 CACHE_HIT のイベントが記録される。"""
    g = Geometry.create("circle", params={"r": 1.0})
    log = FrameRealizeLog()

    with frame_logging(log):
        realize(g)
        realize(g)

    events_for_g = [
        ev.event_type
        for ev in log.events
        if ev.geometry_id == g.id
    ]

    assert RealizeEventType.COMPUTE in events_for_g
    assert RealizeEventType.CACHE_HIT in events_for_g

    dot = export_geometry_dag_dot(root_geometry=g, frame_log=log)
    # 最終イベントが CACHE_HIT なのでノードは緑になるはず。
    for line in dot.splitlines():
        if f'"{g.id}"' in line:
            assert 'fillcolor="green"' in line
            break
    else:
        pytest.fail("Geometry ノードが DOT に含まれていない")


def test_export_multiframe_colors_per_frame(tracer_installed: None) -> None:
    """フレームごとに COMPUTE→CACHE_HIT の色変化が反映される。"""
    g = Geometry.create("circle", params={"r": 1.0})

    logs: list[FrameRealizeLog] = []

    # frame 1: 初回計算。
    log1 = FrameRealizeLog()
    with frame_logging(log1):
        realize(g)
    logs.append(log1)

    # frame 2: キャッシュヒット。
    log2 = FrameRealizeLog()
    with frame_logging(log2):
        realize(g)
    logs.append(log2)

    dot = export_geometry_dag_dot_multiframe(
        root_geometry=g,
        frame_logs=logs,
        frame_labels=["frame 1", "frame 2"],
    )

    line_frame1 = None
    line_frame2 = None
    for line in dot.splitlines():
        if f'"f0_{g.id}"' in line:
            line_frame1 = line
        if f'"f1_{g.id}"' in line:
            line_frame2 = line

    assert line_frame1 is not None, "frame 1 のノードが DOT に含まれていない"
    assert line_frame2 is not None, "frame 2 のノードが DOT に含まれていない"

    assert 'fillcolor="red"' in line_frame1
    assert 'fillcolor="green"' in line_frame2

    # 凡例のラベルが改行形式で含まれていることを確認。
    assert "COMPUTE\\n(計算)" in dot
    assert "no event\\n(未記録)" in dot
