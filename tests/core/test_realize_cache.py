"""realize_cache / inflight に関するテスト群。"""

from __future__ import annotations

import threading
import time

import pytest

from src.core.geometry import Geometry
from src.core.primitive_registry import primitive_registry
from src.core.realize import _inflight, _inflight_lock, realize, realize_cache
from src.core.primitives import circle as _circle_module  # noqa: F401


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


def test_realize_cache_returns_same_instance_for_same_geometry() -> None:
    """同じ Geometry を 2 回 realize すると実体はキャッシュされる。"""
    g = Geometry.create("circle", params={"r": 1.0})

    r1 = realize(g)
    r2 = realize(g)

    assert r1 is r2
    cached = realize_cache.get(g.id)
    assert cached is r1


def test_realize_cache_shared_between_geometry_instances() -> None:
    """同一内容の Geometry インスタンス間でキャッシュが共有される。"""
    g1 = Geometry.create("circle", params={"r": 1.0})
    g2 = Geometry.create("circle", params={"r": 1.0})

    assert g1.id == g2.id

    r1 = realize(g1)
    r2 = realize(g2)

    assert r1 is r2


def test_inflight_avoids_duplicate_computation_under_concurrency() -> None:
    """複数スレッドから同時に realize しても計算が 1 回に潰れる。"""
    # circle primitive をラップして呼び出し回数を数える。
    original_circle = primitive_registry["circle"]
    call_count = {"value": 0}

    def wrapped(args):
        call_count["value"] += 1
        time.sleep(0.05)
        return original_circle(args)

    primitive_registry._items["circle"] = wrapped  # type: ignore[attr-defined]
    try:
        g = Geometry.create("circle", params={"r": 1.0})
        results = []

        def worker() -> None:
            results.append(realize(g))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 全スレッドが同じ RealizedGeometry インスタンスを共有しているはず。
        assert {id(r) for r in results} and len({id(r) for r in results}) == 1
        # 計算自体は 1 回だけ行われていることを期待する。
        assert call_count["value"] == 1
    finally:
        primitive_registry._items["circle"] = original_circle  # type: ignore[attr-defined]
