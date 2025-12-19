# どこで: `src/grafix/cc.py`。
# 何を: `from grafix import cc` で参照できる CC 辞書ビューを提供する。
# なぜ: mp-draw でもフレーム内の CC 値を `cc[1]` の形で読めるようにするため。

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TypeVar, overload

from grafix.core.parameters.context import current_cc_snapshot

_T = TypeVar("_T")


class CcView(Mapping[int, float]):
    """CC 値スナップショットへの読み取り専用ビュー。

    Notes
    -----
    - 値は 0.0–1.0 の正規化済みを想定する。
    - 未設定キーは 0.0 を返す（`KeyError` を出さない）。
    - 実体は `parameter_context(..., cc_snapshot=...)` が供給するフレーム内スナップショット。
    """

    def __getitem__(self, cc_number: int) -> float:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return 0.0
        try:
            value = snapshot.get(int(cc_number))
        except Exception:
            return 0.0
        if value is None:
            return 0.0
        try:
            return float(value)
        except Exception:
            return 0.0

    @overload
    def get(self, cc_number: int, default: None = None) -> float | None: ...

    @overload
    def get(self, cc_number: int, default: _T = ...) -> float | _T: ...

    def get(self, cc_number: int, default: object | None = None) -> object:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return default
        try:
            key = int(cc_number)
        except Exception:
            return default
        if key not in snapshot:
            return default
        try:
            return float(snapshot[key])
        except Exception:
            return default

    def __contains__(self, cc_number: object) -> bool:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return False
        if not isinstance(cc_number, int):
            return False
        return int(cc_number) in snapshot

    def __iter__(self) -> Iterator[int]:
        snapshot = current_cc_snapshot()
        if snapshot is None:
            return iter(())
        return iter(snapshot.keys())  # type: ignore[return-value]

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

        items: list[tuple[int, float]] = []
        for key, value in snapshot.items():
            try:
                items.append((int(key), float(value)))
            except Exception:
                continue
        items.sort(key=lambda kv: kv[0])

        max_items = 16
        head = items[:max_items]
        body = ", ".join(f"{k}: {v}" for k, v in head)
        tail = "" if len(items) <= max_items else f", ... (+{len(items) - max_items})"
        return f"CcView({{{body}{tail}}})"


cc = CcView()
