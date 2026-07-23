# どこで: `src/grafix/core/parameters/labels_ops.py`。
# 何を: ParamStore の label（(op, site_id) -> label）更新手続きを提供する。
# なぜ: 書き込み経路を ops に固定し、呼び出し側が直に辞書を触らないようにするため。

from __future__ import annotations

from .frame_params import FrameLabelRecord
from .store import ParamStore


def set_label(store: ParamStore, *, op: str, site_id: str, label: str) -> None:
    """(op, site_id) のラベルを上書きする。"""

    base_revision = store.revision
    labels = store._read().labels()
    before = labels.get(op, site_id)
    labels.set(op, site_id, label)
    if labels.get(op, site_id) != before:
        store._mutation().commit_labels(
            expected_revision=base_revision,
            labels=labels,
        )


def merge_frame_labels(store: ParamStore, labels: list[FrameLabelRecord]) -> None:
    """フレーム内で観測したラベル設定をストアへ反映する。"""

    base_revision = store.revision
    store_labels = store._read().labels()
    before = store_labels.as_dict()
    for rec in labels:
        store_labels.set(rec.op, rec.site_id, rec.label)
    if store_labels.as_dict() != before:
        store._mutation().commit_labels(
            expected_revision=base_revision,
            labels=store_labels,
        )


__all__ = ["set_label", "merge_frame_labels"]
