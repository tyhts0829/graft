"""
Purpose:
    authoring codeへ、現在のframeに固定されたMIDI CC値のread-only viewを公開する。
Use when:
    `cc[...]`のlookup、未設定値、またはframe snapshotとの接続を変更する場合。
Constraints:
    - live controllerやprocess-global mutable stateではなく、current contextのsnapshotだけを読む。
    - CC番号を0..127に限定し、未設定値は0.0として扱う。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TypeVar, overload

from grafix.core.parameters.context import current_cc_snapshot
from grafix.core.value_validation import exact_integer

_T = TypeVar("_T")


def _cc_number(value: object) -> int:
    cc_number = exact_integer(value, name="CC number")
    if cc_number < 0 or cc_number > 127:
        raise ValueError("CC number は0..127である必要があります")
    return cc_number


class CcView(Mapping[int, float]):
    """CC 値スナップショットへの読み取り専用ビュー。

    Notes
    -----
    - 値は 0.0–1.0 の正規化済みを想定する。
    - 未設定キーは 0.0 を返す（`KeyError` を出さない）。
    - 実体は `parameter_context(..., cc_snapshot=...)` が供給するフレーム内スナップショット。
    """

    def __getitem__(self, cc_number: int) -> float:
        key = _cc_number(cc_number)
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return 0.0
        value = snapshot.get(key)
        return 0.0 if value is None else value

    @overload
    def get(self, cc_number: int, default: None = None) -> float | None: ...

    @overload
    def get(self, cc_number: int, default: _T = ...) -> float | _T: ...

    def get(self, cc_number: int, default: object | None = None) -> object:
        key = _cc_number(cc_number)
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return default
        if key not in snapshot:
            return default
        return snapshot[key]

    def __contains__(self, cc_number: object) -> bool:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return False
        if isinstance(cc_number, bool) or not isinstance(cc_number, int):
            return False
        return cc_number in snapshot

    def __iter__(self) -> Iterator[int]:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return iter(())
        return iter(snapshot)

    def __len__(self) -> int:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return 0
        return len(snapshot)

    def __repr__(self) -> str:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return "CcView(snapshot=None, default=0.0)"
        if not snapshot:
            return "CcView({})"

        items = list(snapshot.entries)

        max_items = 16
        head = items[:max_items]
        body = ", ".join(f"{k}: {v}" for k, v in head)
        tail = "" if len(items) <= max_items else f", ... (+{len(items) - max_items})"
        return f"CcView({{{body}{tail}}})"


cc = CcView()


__all__ = ["CcView", "cc"]
