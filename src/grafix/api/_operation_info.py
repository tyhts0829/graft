"""
Purpose:
    内部catalog entryを、実行capabilityのない公開inspection valueへ射影する。
Use when:
    `G/E.catalog()`や`describe()`が公開するmetadata境界を変更する場合。
Constraints:
    - evaluator、declaration owner、catalogへの参照を`OperationInfo`へ漏らさない。
    - 公開説明はentryのimmutable snapshotだけから構築する。
"""

from __future__ import annotations

from grafix.core.operation_catalog import OperationCatalogEntry

from .operation_info import OperationInfo


def operation_info(entry: OperationCatalogEntry) -> OperationInfo:
    """evaluator/owner を含まない公開 inspection value を返す。"""

    return OperationInfo(
        name=entry.name,
        kind=entry.kind,
        n_inputs=entry.n_inputs,
        accepted_args=entry.accepted_args,
        required_args=entry.required_args,
        defaults=entry.defaults,
        meta=entry.meta,
        description=entry.description,
        doc=entry.doc,
        source=entry.source,
        provenance=entry.provenance,
        accepts_var_kwargs=entry.accepts_var_kwargs,
    )


__all__ = ["operation_info"]
