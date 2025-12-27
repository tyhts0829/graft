"""
numba の `types` を最小限にスタブ化する。

このプロジェクトで参照しているのは:
- `types.float64[:, :]` / `types.int64[:, :]`
- `types.ListType(...)`
"""

from __future__ import annotations

from typing import Any


class _NumbaType:
    def __getitem__(self, item: Any, /) -> Any: ...


float64: _NumbaType
int64: _NumbaType


def ListType(*args: Any, **kwargs: Any) -> Any: ...

