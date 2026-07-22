"""内部 catalog entry を公開 OperationInfo へ射影する。"""

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
