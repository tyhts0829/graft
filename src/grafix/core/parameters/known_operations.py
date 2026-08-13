"""
Purpose:
    parameter finalizationが参照するoperation別argument集合をgeneration単位で固定する。
Use when:
    session終了時のpruneをcurrent catalog/reload generationへ結び付ける場合。
Constraints:
    - snapshotはimmutableに保ち、finalizationへ明示注入する。
    - operation moduleの探索やlive catalogへのfallbackをこの層へ持ち込まない。
    - 未知operationと、argumentを持たない既知operationを区別する。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .identity import identity_string


@dataclass(frozen=True, slots=True)
class KnownOperationSchemaSnapshot:
    """operation 名ごとの既知 argument 集合を generation 単位で固定する。"""

    args_by_op: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        canonical: dict[str, frozenset[str]] = {}
        for raw_op, raw_args in self.args_by_op.items():
            op = identity_string(raw_op, name="known operation")
            args = frozenset(
                identity_string(arg, name=f"known argument for {op}") for arg in raw_args
            )
            canonical[op] = args
        object.__setattr__(self, "args_by_op", MappingProxyType(canonical))

    @classmethod
    def empty(cls) -> KnownOperationSchemaSnapshot:
        """既知 operation を持たない snapshot を返す。"""

        return cls({})

    def args_for(self, op: str) -> frozenset[str] | None:
        """既知 operation の argument 集合、または未知なら ``None`` を返す。"""

        return self.args_by_op.get(op)


__all__ = ["KnownOperationSchemaSnapshot"]
