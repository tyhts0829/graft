"""
Purpose:
    SceneRunnerのinternal MpDraw factory seamへ、世代ごとのclientまたは起動失敗を決定的に注入する。
Use when:
    draw generation交換、worker startup rollback、perf callback配線をテストするとき。
Constraints:
    - outcomeはgeneration client構築要求ごとにFIFOで一度だけ消費する。
    - 全factory引数をcall recordへ保存し、definitions/config/callback identityを検証可能にする。
    - 予定外の追加構築は暗黙にfakeを再利用せず即座に失敗させる。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from grafix.core.authoring_definitions import AuthoringDefinitionsSnapshot
from grafix.core.runtime_config import RuntimeConfig
from grafix.core.scene import SceneItem
from grafix.interactive.runtime.scene_runner import (
    _MpDrawClient,
    _MpDrawEventCallback,
)


@dataclass(frozen=True, slots=True)
class MpDrawFactoryCall:
    """一回の generation client 構築要求。"""

    draw: Callable[[float], SceneItem]
    n_worker: int
    evaluation_timeout: float | None
    effective_config: RuntimeConfig
    definitions: AuthoringDefinitionsSnapshot
    event_callback: _MpDrawEventCallback | None


class MpDrawFactoryFixture:
    """指定した client/例外を generation ごとに順番に返す factory。"""

    def __init__(self, *outcomes: _MpDrawClient | BaseException) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[MpDrawFactoryCall] = []

    def __call__(
        self,
        draw: Callable[[float], SceneItem],
        *,
        n_worker: int,
        evaluation_timeout: float | None,
        effective_config: RuntimeConfig,
        definitions: AuthoringDefinitionsSnapshot,
        event_callback: _MpDrawEventCallback | None,
    ) -> _MpDrawClient:
        self.calls.append(
            MpDrawFactoryCall(
                draw=draw,
                n_worker=n_worker,
                evaluation_timeout=evaluation_timeout,
                effective_config=effective_config,
                definitions=definitions,
                event_callback=event_callback,
            )
        )
        if not self._outcomes:
            raise AssertionError("unexpected MpDraw factory call")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


__all__ = ["MpDrawFactoryCall", "MpDrawFactoryFixture"]
