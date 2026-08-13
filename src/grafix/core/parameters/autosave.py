"""
Purpose:
    ParamStoreのrevisionを監視し、操作中の同期保存を避けながらautosave時機を調停する。
Use when:
    interactive sessionのrecovery保存をdebounceと最大待機時間で制御する場合。
Constraints:
    - suspended中は保存せず、解除後にdebounce境界を取り直す。
    - 保存失敗後もdirty状態を維持し、毎frameの即時retry loopを作らない。
Side effects:
    注入されたsave callbackを呼び出し、callback側でfilesystem更新が起こり得る。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from grafix.core.value_validation import exact_bool, finite_real

from .store import ParamStore

SaveParamStore = Callable[[ParamStore, Path], None]
AutosaveStatus = Literal["clean", "dirty", "saving", "failed"]


class ParamStoreAutosave:
    """ParamStore を debounce 後、または最大保存間隔で atomic save する。"""

    def __init__(
        self,
        store: ParamStore,
        path: Path,
        *,
        save: SaveParamStore,
        debounce_seconds: float = 0.75,
        max_interval_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(store, ParamStore):
            raise TypeError("store は ParamStore である必要があります")
        if not isinstance(path, Path):
            raise TypeError("path は Path である必要があります")
        debounce = finite_real(
            debounce_seconds,
            name="debounce_seconds",
            minimum=0.0,
        )
        max_interval = finite_real(
            max_interval_seconds,
            name="max_interval_seconds",
            minimum=0.0,
            minimum_inclusive=False,
        )
        if not callable(clock):
            raise TypeError("clock は callable である必要があります")
        if not callable(save):
            raise TypeError("save は callable である必要があります")
        self._store = store
        self._path = path
        self._debounce_seconds = debounce
        self._max_interval_seconds = max_interval
        self._clock = clock
        self._save = save
        self._observed_revision = store.revision
        self._saved_revision = store.revision
        self._dirty_since: float | None = None
        self._first_dirty_at: float | None = None
        self._status: AutosaveStatus = "clean"
        self._last_error: str | None = None
        self._was_suspended = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def dirty(self) -> bool:
        return self._store.revision != self._saved_revision

    @property
    def last_saved_revision(self) -> int:
        return self._saved_revision

    @property
    def status(self) -> AutosaveStatus:
        """現在の保存状態を返す。"""

        return self._status

    @property
    def last_error(self) -> str | None:
        """直近の保存失敗を返す。成功後は None。"""

        return self._last_error

    def tick(
        self,
        *,
        now: float | None = None,
        suspended: bool = False,
    ) -> bool:
        """変更を観測し、操作終了後の debounce を経て保存する。

        ``suspended=True`` の間は同期 I/O を行わない。slider release 後に
        debounce を開始し直すため、長い drag が最大保存間隔を越えても
        操作中の frame を停止させない。
        """

        suspended = exact_bool(suspended, name="suspended")
        current_time = finite_real(
            self._clock() if now is None else now,
            name="now" if now is not None else "clock()",
        )
        self._observe(current_time)
        if suspended:
            self._was_suspended = True
            return False
        if self._was_suspended:
            self._was_suspended = False
            if self.dirty:
                self._dirty_since = current_time
                self._first_dirty_at = current_time
                if self._status != "failed":
                    self._status = "dirty"
            return False
        if not self.dirty or self._dirty_since is None:
            return False
        settled = current_time - self._dirty_since >= self._debounce_seconds
        reached_max_interval = (
            self._first_dirty_at is not None
            and current_time - self._first_dirty_at >= self._max_interval_seconds
        )
        if not settled and not reached_max_interval:
            return False
        return self._save_now(retry_from=current_time)

    def flush(self) -> bool:
        """未保存の変更があれば、debounce を待たずに保存する。"""

        current_time = finite_real(self._clock(), name="clock()")
        self._observe(current_time)
        if not self.dirty:
            return False
        return self._save_now(retry_from=current_time)

    def mark_clean(self) -> None:
        """別経路で保存した現在状態を保存済みとして取り込む。"""

        self._observed_revision = self._store.revision
        self._saved_revision = self._store.revision
        self._dirty_since = None
        self._first_dirty_at = None
        self._status = "clean"
        self._last_error = None
        self._was_suspended = False

    def _observe(self, now: float) -> None:
        revision = self._store.revision
        if revision == self._observed_revision:
            return
        self._observed_revision = revision
        if self._first_dirty_at is None:
            self._first_dirty_at = now
        self._dirty_since = now
        if self._status != "failed":
            self._status = "dirty"

    def _save_now(self, *, retry_from: float) -> bool:
        self._status = "saving"
        self._last_error = None
        try:
            self._save(self._store, self._path)
        except Exception as exc:
            # 毎 frame リトライする hot loop を避け、次の debounce 後に再試行する。
            self._observed_revision = self._store.revision
            self._dirty_since = retry_from
            self._first_dirty_at = retry_from
            self._status = "failed"
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        self._observed_revision = self._store.revision
        self._saved_revision = self._store.revision
        self._dirty_since = None
        self._first_dirty_at = None
        self._status = "clean"
        self._last_error = None
        self._was_suspended = False
        return True


__all__ = ["AutosaveStatus", "ParamStoreAutosave", "SaveParamStore"]
