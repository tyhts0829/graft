"""
Purpose:
    interactive subsystemの失敗・警告・復旧actionを共通のbounded event streamへ集約する。
Use when:
    user-facing diagnostic、dedupe、保持上限、またはaction dispatchを変更する場合。
Constraints:
    - eventをimmutable valueとして渡し、同じidentityの再発はcountへ集約する。
    - action失敗をcenter自身の診断へ変換し、登録済みevent streamを壊さない。
"""

from __future__ import annotations

import traceback
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import RLock
from typing import Literal, TypeAlias, cast

from grafix.core.value_validation import (
    exact_integer,
    exact_string,
    exact_string_choice,
)

DiagnosticSeverity = Literal["info", "warning", "error"]
DiagnosticActionId = Literal[
    "copy",
    "retry",
    "open",
    "keep",
    "discard",
    "compare",
]
_ACTION_IDS: frozenset[str] = frozenset(
    {"copy", "retry", "open", "keep", "discard", "compare"}
)


@dataclass(frozen=True, slots=True)
class DiagnosticAction:
    """診断に表示する型付き action descriptor。"""

    action_id: DiagnosticActionId
    label: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_id",
            exact_string_choice(
                self.action_id,
                name="action_id",
                choices=tuple(sorted(_ACTION_IDS)),
            ),
        )
        label = exact_string(self.label, name="label")
        if not label.strip():
            raise ValueError("label は空にできません")


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    """scene/export/save/config 等に共通の user-facing 診断。"""

    category: str
    severity: DiagnosticSeverity
    summary: str
    details: str = ""
    source: str | None = None
    actions: tuple[DiagnosticAction, ...] = ()
    count: int = 1
    dedupe_key: str | None = None

    def __post_init__(self) -> None:
        category = exact_string(self.category, name="category")
        if not category.strip():
            raise ValueError("category は空にできません")
        object.__setattr__(
            self,
            "severity",
            exact_string_choice(
                self.severity,
                name="severity",
                choices=("info", "warning", "error"),
            ),
        )
        summary = exact_string(self.summary, name="summary")
        if not summary.strip():
            raise ValueError("summary は空にできません")
        exact_string(self.details, name="details")
        if self.source is not None:
            exact_string(self.source, name="source")
        if self.dedupe_key is not None:
            exact_string(self.dedupe_key, name="dedupe_key")
        if not isinstance(self.actions, tuple):
            raise TypeError("actions は DiagnosticAction の tuple である必要があります")
        if any(not isinstance(action, DiagnosticAction) for action in self.actions):
            raise TypeError("actions は DiagnosticAction の tuple である必要があります")
        object.__setattr__(
            self,
            "count",
            exact_integer(self.count, name="count", minimum=1),
        )

    def identity(self) -> tuple[object, ...]:
        """同一診断を数えるための安定 identity を返す。"""

        if self.dedupe_key is not None:
            return (self.category, self.dedupe_key)
        return (
            self.category,
            self.severity,
            self.summary,
            self.details,
            self.source,
        )


DiagnosticActionHandler: TypeAlias = Callable[[DiagnosticEvent], None]


class DiagnosticCenter:
    """同一診断を集約し、直近の有限件数だけを保持する。"""

    def __init__(self, *, max_events: int = 100) -> None:
        self._max_events = exact_integer(max_events, name="max_events", minimum=1)
        self._events: OrderedDict[tuple[object, ...], DiagnosticEvent] = OrderedDict()
        self._action_handlers: dict[
            tuple[str | None, DiagnosticActionId], DiagnosticActionHandler
        ] = {}
        self._lock = RLock()

    @property
    def max_events(self) -> int:
        return self._max_events

    def publish(self, event: DiagnosticEvent) -> DiagnosticEvent:
        """診断を追加し、同一診断なら発生回数を加算して返す。"""

        if not isinstance(event, DiagnosticEvent):
            raise TypeError("event は DiagnosticEvent である必要があります")
        identity = event.identity()
        with self._lock:
            previous = self._events.pop(identity, None)
            stored = (
                event
                if previous is None
                else replace(event, count=previous.count + event.count)
            )
            self._events[identity] = stored
            while len(self._events) > self._max_events:
                self._events.popitem(last=False)
            return stored

    def dismiss(self, event: DiagnosticEvent) -> bool:
        """指定診断を削除し、存在した場合は True を返す。"""

        if not isinstance(event, DiagnosticEvent):
            raise TypeError("event は DiagnosticEvent である必要があります")
        with self._lock:
            return self._events.pop(event.identity(), None) is not None

    def register_action(
        self,
        action_id: DiagnosticActionId,
        handler: DiagnosticActionHandler,
        *,
        category: str | None = None,
    ) -> None:
        """action handler を型付き ID と任意の category へ登録する。"""

        action_id = cast(
            DiagnosticActionId,
            exact_string_choice(
                action_id,
                name="action_id",
                choices=tuple(sorted(_ACTION_IDS)),
            ),
        )
        if not callable(handler):
            raise TypeError("handler は callable である必要があります")
        category_key = (
            None
            if category is None
            else exact_string(category, name="category")
        )
        if category_key is not None and not category_key.strip():
            raise ValueError("category は空にできません")
        key = (category_key, action_id)
        with self._lock:
            if key in self._action_handlers:
                raise ValueError(
                    "action handler は登録済みです: "
                    f"category={category_key!r}, action_id={action_id!r}"
                )
            self._action_handlers[key] = handler

    def dispatch_action(
        self,
        event: DiagnosticEvent,
        action: DiagnosticAction,
    ) -> bool:
        """action を実行し、未登録・失敗も同じ center へ診断する。"""

        if not isinstance(event, DiagnosticEvent):
            raise TypeError("event は DiagnosticEvent である必要があります")
        if not isinstance(action, DiagnosticAction):
            raise TypeError("action は DiagnosticAction である必要があります")
        if action not in event.actions:
            self.publish(
                DiagnosticEvent(
                    category="diagnostic",
                    severity="warning",
                    summary=f"Action is not available: {action.label}",
                    details=f"action_id={action.action_id}",
                    dedupe_key=f"action-not-available:{action.action_id}",
                )
            )
            return False

        with self._lock:
            handler = self._action_handlers.get((event.category, action.action_id))
            if handler is None:
                handler = self._action_handlers.get((None, action.action_id))
        if handler is None:
            self.publish(
                DiagnosticEvent(
                    category="diagnostic",
                    severity="warning",
                    summary=f"Action is unavailable: {action.label}",
                    details=f"No handler is registered for {action.action_id!r}.",
                    dedupe_key=f"action-unregistered:{action.action_id}",
                )
            )
            return False

        try:
            handler(event)
        except Exception as exc:
            self.publish(
                DiagnosticEvent(
                    category=event.category,
                    severity="error",
                    summary=f"Action failed: {action.label}",
                    details="".join(
                        traceback.format_exception(type(exc), exc, exc.__traceback__)
                    ),
                    source=event.source,
                    actions=(DiagnosticAction("copy", "Copy details"),),
                    dedupe_key=(
                        f"action-failed:{action.action_id}:"
                        f"{type(exc).__name__}:{exc}"
                    ),
                )
            )
            return False
        return True

    def clear(self, *, category: str | None = None) -> None:
        """全診断、または指定 category の診断を削除する。"""

        with self._lock:
            if category is None:
                self._events.clear()
                return
            category_s = exact_string(category, name="category")
            for identity in tuple(self._events):
                if identity[0] == category_s:
                    self._events.pop(identity, None)

    def snapshot(self) -> tuple[DiagnosticEvent, ...]:
        """古い順の immutable snapshot を返す。"""

        with self._lock:
            return tuple(self._events.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


__all__ = [
    "DiagnosticAction",
    "DiagnosticActionHandler",
    "DiagnosticActionId",
    "DiagnosticCenter",
    "DiagnosticEvent",
    "DiagnosticSeverity",
]
