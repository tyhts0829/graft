"""
どこで: `typings/numba/__init__.pyi`。
何を: mypy 用の最小限の numba stub を提供する。
なぜ: 環境差で numba の型情報が欠けても、プロジェクト側の型検査を安定させるため。
"""

from __future__ import annotations

from typing import Any, Callable, ParamSpec, TypeVar, overload

from . import types as types
from . import typed as typed

P = ParamSpec("P")
R = TypeVar("R")


@overload
def njit(func: Callable[P, R], /, *args: Any, **kwargs: Any) -> Callable[P, R]: ...


@overload
def njit(*args: Any, **kwargs: Any) -> Callable[[Callable[P, R]], Callable[P, R]]: ...

