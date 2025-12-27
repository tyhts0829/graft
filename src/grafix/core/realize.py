# src/core/realize.py
# Geometry ノードの評価と realize_cache/inflight 管理を実装する。

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import MutableMapping

from grafix.core.effect_registry import effect_registry
from grafix.core.geometry import Geometry, GeometryId
from grafix.core.primitive_registry import primitive_registry
from grafix.core.realized_geometry import RealizedGeometry, concat_realized_geometries


class RealizeError(RuntimeError):
    """realize 実行中の例外をラップするエラー。"""


@dataclass
class _InflightEntry:
    """inflight 計算の同期オブジェクト。"""

    condition: threading.Condition
    done: bool = False
    result: RealizedGeometry | None = None
    error: BaseException | None = None


class RealizeCache:
    """GeometryId をキーとする実体ジオメトリのキャッシュ。

    Notes
    -----
    現時点では容量上限は設けず、将来の最適化で
    推定バイト数による上限管理を追加する想定とする。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[GeometryId, RealizedGeometry] = {}

    def get(self, key: GeometryId) -> RealizedGeometry | None:
        """キャッシュから値を取得する。見つからなければ None を返す。"""
        with self._lock:
            return self._items.get(key)

    def set(self, key: GeometryId, value: RealizedGeometry) -> None:
        """キャッシュに値を保存する。"""
        with self._lock:
            self._items[key] = value


# グローバルキャッシュと inflight テーブル
realize_cache = RealizeCache()
_inflight: MutableMapping[GeometryId, _InflightEntry] = {}
_inflight_lock = threading.Lock()


def _evaluate_geometry_node(geometry: Geometry) -> RealizedGeometry:
    """単一 Geometry ノードを評価して RealizedGeometry を生成する。"""
    op = geometry.op
    if op == "concat":
        realized_inputs = [realize(g) for g in geometry.inputs]
        return concat_realized_geometries(*realized_inputs)

    if not geometry.inputs:
        # primitive
        primitive_func = primitive_registry.get(op)
        return primitive_func(geometry.args)

    # effect
    realized_inputs = [realize(g) for g in geometry.inputs]
    effect_func = effect_registry.get(op)
    return effect_func(realized_inputs, geometry.args)


def realize(geometry: Geometry) -> RealizedGeometry:
    """Geometry を評価し、RealizedGeometry を返す。

    realize_cache と inflight を用いて重複計算を避ける。

    Parameters
    ----------
    geometry : Geometry
        評価対象の Geometry ノード。

    Returns
    -------
    RealizedGeometry
        評価結果。

    Raises
    ------
    RealizeError
        評価中に発生した例外をラップして送出する。
    """
    geometry_id = geometry.id

    # 1. キャッシュヒットを確認
    cached = realize_cache.get(geometry_id)
    if cached is not None:
        return cached

    # 2. inflight テーブルで重複計算を排除
    with _inflight_lock:
        entry = _inflight.get(geometry_id)
        if entry is None:
            # 先行計算者になる
            entry = _InflightEntry(condition=threading.Condition(_inflight_lock))
            _inflight[geometry_id] = entry
            is_leader = True
        else:
            is_leader = False

    if not is_leader:
        # 既に別スレッドが計算中なので完了を待つ
        with entry.condition:
            while not entry.done:
                entry.condition.wait()
        if entry.error is not None:
            raise RealizeError(f"realize に失敗した: id={geometry_id}") from entry.error
        assert entry.result is not None
        return entry.result

    # 3. 自分が先行計算者として評価を行う
    try:
        result = _evaluate_geometry_node(geometry)
        # RealizedGeometry.__post_init__ で不変条件と writeable=False が保証される
        realize_cache.set(geometry_id, result)
        error: BaseException | None = None
    except BaseException as exc:  # noqa: BLE001
        result = None
        error = exc

    # 4. inflight を更新し、待機者に通知
    with _inflight_lock:
        entry = _inflight.pop(geometry_id)
        entry.result = result  # type: ignore[assignment]
        entry.error = error
        entry.done = True
        entry.condition.notify_all()

    if error is not None:
        raise RealizeError(f"realize に失敗した: id={geometry_id}") from error

    assert result is not None
    return result
