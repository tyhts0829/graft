"""
Purpose:
    MIDI Range Editのbegin/preview/commit/cancelと入力change追跡を所有する。
Use when:
    Range Edit mode遷移、CC delta処理、またはcommitの発火条件を変更する場合。
Constraints:
    - 同じMIDI changeを複数frameで再適用しない。
    - preview中はstoreを変更せず、明示commit時だけdomain operationへ渡す。
"""

from __future__ import annotations

from grafix.core.parameters.history import ParamStoreHistory
from grafix.core.parameters.key import ParameterKey
from grafix.core.parameters.store import ParamStore

from .range_edit import (
    RangeEditMode,
    RangeEditSession,
    apply_range_edit_session,
    preview_range_edit,
    range_edit_session_for_store,
)


class RangeEditController:
    """Range Edit の開始、preview、commit、cancel を所有する。

    ImGui や window を参照しない。MIDI の change sequence と CC ごとの直前値を
    controller 内に保持するため、同じ入力を複数 frame で再適用しない。
    """

    def __init__(
        self,
        store: ParamStore,
        *,
        history: ParamStoreHistory | None = None,
    ) -> None:
        if not isinstance(store, ParamStore):
            raise TypeError("store must be a ParamStore")
        self._store = store
        self._history = history
        self._mode: RangeEditMode | None = None
        self._session: RangeEditSession | None = None
        self._last_seen_cc_sequence = 0
        self._previous_value_by_cc: dict[int, float] = {}

    @property
    def mode(self) -> RangeEditMode | None:
        """現在の明示 edit mode を返す。"""

        return self._mode

    @property
    def session(self) -> RangeEditSession | None:
        """store 未反映の preview session を返す。"""

        return self._session

    def begin(self, mode: RangeEditMode) -> None:
        """新しい edit transaction を開始し、以前の preview を破棄する。"""

        if mode not in ("shift", "min", "max"):
            raise ValueError(f"unknown range edit mode: {mode!r}")
        self._mode = mode
        self._session = None

    def cancel(self) -> None:
        """未commit previewを破棄して通常 mode へ戻る。"""

        self._mode = None
        self._session = None

    def preview_midi_change(
        self,
        *,
        sequence: int,
        cc: int,
        value: float | None,
        blocked: bool = False,
    ) -> bool:
        """新しい MIDI CC 値を観測し、必要なら preview へ差分を適用する。

        ``blocked`` は MIDI learn 中など、入力値の追跡だけを進めて Range Edit に
        適用しない場合に使う。最初の観測値は基準値となるため preview を変えない。
        """

        sequence_i = int(sequence)
        if sequence_i <= self._last_seen_cc_sequence:
            return False
        self._last_seen_cc_sequence = sequence_i

        if value is None:
            return False
        cc_i = int(cc)
        current = float(value)
        previous = self._previous_value_by_cc.get(cc_i, current)
        self._previous_value_by_cc[cc_i] = current
        delta = current - previous
        if delta == 0.0 or blocked or self._mode is None:
            return False

        session = self._session
        if session is None:
            session = range_edit_session_for_store(
                self._store,
                cc=cc_i,
                mode=self._mode,
            )
            if session is None:
                return False
        elif session.cc != cc_i:
            return False

        updated = preview_range_edit(session, delta=delta)
        if updated == session:
            return False
        self._session = updated
        return True

    def commit(self) -> tuple[ParameterKey, ...]:
        """preview 差分を一つの履歴単位で反映し、transaction を終了する。"""

        session = self._session
        if session is None:
            return ()
        changed = apply_range_edit_session(
            self._store,
            session,
            history=self._history,
        )
        self.cancel()
        return changed


__all__ = ["RangeEditController"]
