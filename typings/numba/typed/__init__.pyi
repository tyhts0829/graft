"""
numba の `typed` を最小限にスタブ化する。

このプロジェクトで参照しているのは `typed.List.empty_list(...)` のみ。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

T = TypeVar("T")


class List(list[T], Generic[T]):
    @classmethod
    def empty_list(cls, item_type: Any) -> "List[Any]": ...

