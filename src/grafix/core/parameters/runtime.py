# どこで: `src/grafix/core/parameters/runtime.py`。
# 何を: ParamStore の実行時情報（loaded/observed/reconcile-applied）を保持する。
# なぜ: 永続データと混ぜずに、reconcile/prune の判断材料を分離するため。

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ParamStoreRuntime:
    """ParamStore の実行時情報。"""

    loaded_groups: set[tuple[str, str]] = field(default_factory=set)
    observed_groups: set[tuple[str, str]] = field(default_factory=set)
    reconcile_applied: set[tuple[tuple[str, str], tuple[str, str]]] = field(
        default_factory=set
    )


__all__ = ["ParamStoreRuntime"]

