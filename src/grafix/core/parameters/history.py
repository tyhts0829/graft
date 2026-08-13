"""
Purpose:
    ParamStoreのGUI-owned調整に対するbounded Undo/RedoとA/B比較snapshotを所有する。
Use when:
    UI操作を一履歴単位へまとめる、連続操作をcoalesceする、または調整案を比較する場合。
Constraints:
    - historyとslotは所属する一つのstoreのimmutable adjustment snapshotだけを扱う。
    - 未記録のstore変更を操作前に同期し、code-owned変更やno-opだけでUndoを増やさない。
    - transaction内の複数変更を一操作として記録し、live ParamStateを履歴へ保持しない。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Hashable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal

from grafix.core.value_validation import (
    exact_bool,
    exact_integer,
    exact_string_choice,
    finite_real,
)

from .adjustment_snapshot import (
    ParameterAdjustmentPatch,
    ParameterAdjustmentSnapshot,
    _ParameterAdjustmentSlot,
)
from .collapsed_header import CollapsedHeaderKey
from .key import ParameterKey
from .store import ParamStore

SnapshotSlot = Literal["A", "B"]
_SNAPSHOT_SLOTS: tuple[SnapshotSlot, SnapshotSlot] = ("A", "B")


def _slots_match(
    left: _ParameterAdjustmentSlot,
    right: _ParameterAdjustmentSlot,
) -> bool:
    left_state = left.state
    right_state = right.state
    left_meta = left.meta
    right_meta = right.meta
    if left_state is None or right_state is None or left_meta is None or right_meta is None:
        return left_state == right_state and left_meta == right_meta
    return (
        left_meta.kind == right_meta.kind
        and left_state.override == right_state.override
        and left_state.ui_value == right_state.ui_value
        and left_state.cc_key == right_state.cc_key
        and left_meta.ui_min == right_meta.ui_min
        and left_meta.ui_max == right_meta.ui_max
    )


class _ParameterAdjustmentPatchCapture:
    """GUI transaction 中に、触れた key の変更前値だけを遅延 capture する。"""

    __slots__ = ("_store", "_before_by_key", "_collapsed_before")

    def __init__(self, store: ParamStore) -> None:
        self._store = store
        self._before_by_key: dict[ParameterKey, _ParameterAdjustmentSlot] = {}
        self._collapsed_before: frozenset[CollapsedHeaderKey] | None = None

    def observe_key(self, key: ParameterKey) -> None:
        """key を最初に変更する直前の値だけ保存する。"""

        if key not in self._before_by_key:
            self._before_by_key[key] = self._store._capture_adjustment_slot(key)

    def observe_headers(
        self,
        headers: frozenset[CollapsedHeaderKey] | None = None,
    ) -> None:
        """Collapse set を最初に変更する直前だけ保存する。"""

        if self._collapsed_before is None:
            self._collapsed_before = (
                self._store.collapsed_headers()
                if headers is None
                else frozenset(headers)
            )

    def finish(self) -> ParameterAdjustmentPatch | None:
        """実差分だけを immutable history entry として返す。"""

        before_by_key: dict[ParameterKey, _ParameterAdjustmentSlot] = {}
        after_by_key: dict[ParameterKey, _ParameterAdjustmentSlot] = {}
        for key, before in self._before_by_key.items():
            after = self._store._capture_adjustment_slot(key)
            if _slots_match(before, after):
                continue
            before_by_key[key] = before
            after_by_key[key] = after

        collapsed_before: dict[CollapsedHeaderKey, bool] = {}
        collapsed_after: dict[CollapsedHeaderKey, bool] = {}
        if self._collapsed_before is not None:
            after_headers = self._store.collapsed_headers()
            for header in self._collapsed_before ^ after_headers:
                collapsed_before[header] = header in self._collapsed_before
                collapsed_after[header] = header in after_headers

        if not before_by_key and not collapsed_before:
            return None
        return ParameterAdjustmentPatch(
            before_by_key=before_by_key,
            after_by_key=after_by_key,
            collapsed_before=collapsed_before,
            collapsed_after=collapsed_after,
        )


@dataclass(frozen=True, slots=True)
class _SnapshotTarget:
    """Undo または Redo で復元する full adjustment snapshot。"""

    snapshot: ParameterAdjustmentSnapshot


@dataclass(frozen=True, slots=True)
class _PatchTarget:
    """Undo または Redo で適用する patch と方向。"""

    patch: ParameterAdjustmentPatch
    after: bool


_HistoryEntry = _SnapshotTarget | _PatchTarget


class ParamStoreHistory:
    """ParamStore の変更履歴。

    ``record_change`` は変更後に呼ぶ簡易 API。フレーム内で store が
    別用途でも変更される UI では、変更前を確実に捕まえる
    ``transaction`` を推奨する。
    """

    def __init__(
        self,
        store: ParamStore,
        *,
        capacity: int = 100,
        coalesce_seconds: float = 0.35,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(store, ParamStore):
            raise TypeError("store は ParamStore である必要があります")
        normalized_capacity = exact_integer(
            capacity,
            name="capacity",
            minimum=1,
        )
        normalized_coalesce_seconds = finite_real(
            coalesce_seconds,
            name="coalesce_seconds",
            minimum=0.0,
        )
        if not callable(clock):
            raise TypeError("clock は callable である必要があります")

        self._store = store
        self._capacity = normalized_capacity
        self._coalesce_seconds = normalized_coalesce_seconds
        self._clock = clock
        self._undo: deque[_HistoryEntry] = deque(maxlen=self._capacity)
        self._redo: deque[_HistoryEntry] = deque(maxlen=self._capacity)
        self._current = store.capture_adjustment_snapshot()
        self._seen_revision = store.revision
        self._last_source: Hashable | None = None
        self._last_change_at: float | None = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_depth(self) -> int:
        return len(self._undo)

    @property
    def redo_depth(self) -> int:
        return len(self._redo)

    def synchronize(self, *, clear_history: bool = False) -> bool:
        """現在状態を履歴の基準にし、未記録の変更を Undo 対象にしない。

        draw によるパラメータ発見など、ユーザー操作以外の変更を
        GUI トランザクションの前に取り込む用途を想定する。
        """

        clear = exact_bool(clear_history, name="clear_history")
        if self._store.revision == self._seen_revision and not clear:
            return False
        self._current = self._store.capture_adjustment_snapshot()
        self._seen_revision = self._store.revision
        self._redo.clear()
        if clear:
            self._undo.clear()
        self.break_coalescing()
        return True

    def record_change(
        self,
        *,
        source: Hashable,
        now: float | None = None,
    ) -> bool:
        """直前の基準から現在までの変更を 1 操作として記録する。"""

        try:
            hash(source)
        except TypeError:
            raise TypeError("source は hashable である必要があります") from None
        explicit_now = (
            None
            if now is None
            else finite_real(now, name="now")
        )
        if self._store.revision == self._seen_revision:
            return False
        before = self._current
        after = self._store.capture_adjustment_snapshot()
        return self._record_transition(
            before=before,
            after=after,
            source=source,
            now=(
                finite_real(self._clock(), name="clock()")
                if explicit_now is None
                else explicit_now
            ),
        )

    @contextmanager
    def transaction(
        self,
        *,
        source: Hashable,
        now: float | None = None,
        patch: bool = False,
    ) -> Iterator[None]:
        """ブロック内の store 変更を 1 操作として記録する。

        ブロック開始時の未記録変更は基準に取り込む。そのため、
        初回 draw の parameter discovery を Undo で消してしまわない。

        ``patch=True`` では、実際に変更した既存 parameter だけを遅延
        capture する。slider のような単一 key 操作向けであり、variation
        や reconcile などの bulk 操作は既定の full snapshot を使う。
        """

        try:
            hash(source)
        except TypeError:
            raise TypeError("source は hashable である必要があります") from None
        explicit_now = (
            None
            if now is None
            else finite_real(now, name="now")
        )
        use_patch = exact_bool(patch, name="patch")
        self._store._begin_history_transaction(self)
        try:
            if self._store.revision != self._seen_revision:
                self.synchronize()
            before_revision = self._store.revision
            if use_patch:
                capture = _ParameterAdjustmentPatchCapture(self._store)
                self._store._begin_history_patch_capture(
                    observe_key=capture.observe_key,
                    observe_headers=capture.observe_headers,
                )
                try:
                    yield
                finally:
                    self._store._end_history_patch_capture()
                    if self._store.revision != before_revision:
                        operation = capture.finish()
                        change_at = (
                            finite_real(self._clock(), name="clock()")
                            if explicit_now is None
                            else explicit_now
                        )
                        if operation is None:
                            # favorite や code-owned metadata など、snapshot の対象外だけが
                            # 変わった場合は Undo を増やさず基準 revision だけ進める。
                            self._seen_revision = self._store.revision
                        else:
                            self._record_operation(
                                operation=_PatchTarget(
                                    patch=operation,
                                    after=False,
                                ),
                                source=source,
                                now=change_at,
                            )
                            self._current = self._current.with_patch(
                                operation,
                                after=True,
                            )
                return

            before = self._current
            try:
                yield
            finally:
                if self._store.revision != before_revision:
                    after = self._store.capture_adjustment_snapshot()
                    self._record_transition(
                        before=before,
                        after=after,
                        source=source,
                        now=(
                            finite_real(self._clock(), name="clock()")
                            if explicit_now is None
                            else explicit_now
                        ),
                    )
        finally:
            self._store._end_history_transaction(self)

    def undo(self) -> bool:
        """直前の記録済み操作を戻す。履歴が無ければ False。"""

        self._adopt_untracked_state()
        if not self._undo:
            return False
        target = self._undo.pop()
        self._redo.append(self._inverse_target(target))
        changed = self._restore_target(target)
        # merge restore 後の store には、target 作成後に発見された
        # parameter も残る。次の履歴基準は target そのものではなく、
        # 実際に復元された現在状態から再 capture する。
        self._current = self._store.capture_adjustment_snapshot()
        self._seen_revision = self._store.revision
        self.break_coalescing()
        return changed

    def redo(self) -> bool:
        """直前に Undo した操作を再適用する。履歴が無ければ False。"""

        self._adopt_untracked_state()
        if not self._redo:
            return False
        target = self._redo.pop()
        self._undo.append(self._inverse_target(target))
        changed = self._restore_target(target)
        self._current = self._store.capture_adjustment_snapshot()
        self._seen_revision = self._store.revision
        self.break_coalescing()
        return changed

    def clear(self) -> None:
        """履歴を消去し、現在状態を新しい基準にする。"""

        self.synchronize(clear_history=True)

    def break_coalescing(self) -> None:
        """次の変更を、直前とは別の Undo 単位にする。"""

        self._last_source = None
        self._last_change_at = None

    def _record_transition(
        self,
        *,
        before: ParameterAdjustmentSnapshot,
        after: ParameterAdjustmentSnapshot,
        source: Hashable,
        now: float,
    ) -> bool:
        # revision は label/ordinal など code-owned 構造でも進む。
        # GUI-owned 状態に実差分が無い場合は Undo を増やさない。
        if self._store.adjustment_snapshot_matches(before):
            self._current = after
            self._seen_revision = self._store.revision
            return False

        operation = _SnapshotTarget(snapshot=before)
        self._record_operation(
            operation=operation,
            source=source,
            now=now,
        )
        self._current = after
        return True

    def _record_operation(
        self,
        *,
        operation: _HistoryEntry,
        source: Hashable,
        now: float,
    ) -> None:
        """operation を追加し、同じ source の隣接操作を coalesce する。"""

        last_at = self._last_change_at
        should_coalesce = (
            bool(self._undo)
            and self._last_source == source
            and last_at is not None
            and 0.0 <= now - last_at <= self._coalesce_seconds
        )
        did_coalesce = False
        if should_coalesce:
            previous = self._undo[-1]
            if isinstance(previous, _PatchTarget) and isinstance(
                operation, _PatchTarget
            ):
                coalesced = previous.patch.coalesced_with(operation.patch)
                if coalesced is not None:
                    self._undo[-1] = _PatchTarget(
                        patch=coalesced,
                        after=False,
                    )
                    did_coalesce = True
            elif isinstance(previous, _SnapshotTarget) and isinstance(
                operation, _SnapshotTarget
            ):
                # 最初の変更前値を Undo target として維持する。
                did_coalesce = True

        if not did_coalesce:
            self._undo.append(operation)
        self._redo.clear()
        self._seen_revision = self._store.revision
        self._last_source = source
        self._last_change_at = now

    def _inverse_target(self, target: _HistoryEntry) -> _HistoryEntry:
        """現在状態へ戻すための反対向き target を返す。"""

        if isinstance(target, _PatchTarget):
            return _PatchTarget(patch=target.patch, after=not target.after)
        return _SnapshotTarget(snapshot=self._current)

    def _restore_target(self, target: _HistoryEntry) -> bool:
        if isinstance(target, _PatchTarget):
            return self._store._apply_adjustment_patch(
                target.patch,
                after=target.after,
            )
        return self._store.apply_adjustment_snapshot(target.snapshot)

    def _adopt_untracked_state(self) -> None:
        if self._store.revision == self._seen_revision:
            return
        # 履歴外の分岐後に古い redo を適用すると、新規に発見した
        # metadata まで消し得る。現在を基準にし、redo は捨てる。
        self._current = self._store.capture_adjustment_snapshot()
        self._seen_revision = self._store.revision
        self._redo.clear()
        self.break_coalescing()


class ParamSnapshotSlots:
    """A/B の 2 スロットに ParamStore の調整案を保存する。"""

    def __init__(self, store: ParamStore) -> None:
        if not isinstance(store, ParamStore):
            raise TypeError("store は ParamStore である必要があります")
        self._store = store
        self._slots: dict[SnapshotSlot, ParameterAdjustmentSnapshot] = {}

    @property
    def available_slots(self) -> tuple[SnapshotSlot, ...]:
        return tuple(slot for slot in _SNAPSHOT_SLOTS if slot in self._slots)

    def capture(self, slot: SnapshotSlot) -> None:
        """現在状態を slot へ保存する（既存値は上書き）。"""

        self._validate_slot(slot)
        self._slots[slot] = self._store.capture_adjustment_snapshot()

    def restore(self, slot: SnapshotSlot) -> bool:
        """slot を store へ merge 適用する。未保存/同一状態なら False。"""

        self._validate_slot(slot)
        snapshot = self._slots.get(slot)
        if snapshot is None:
            return False
        return self._store.apply_adjustment_snapshot(snapshot)

    def clear(self, slot: SnapshotSlot | None = None) -> None:
        """1 スロット、または A/B 両方を消去する。"""

        if slot is None:
            self._slots.clear()
            return
        self._validate_slot(slot)
        self._slots.pop(slot, None)

    @staticmethod
    def _validate_slot(slot: object) -> None:
        exact_string_choice(
            slot,
            name="snapshot slot",
            choices=("A", "B"),
        )


__all__ = ["ParamStoreHistory", "ParamSnapshotSlots", "SnapshotSlot"]
