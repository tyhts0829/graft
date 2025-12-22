# どこで: `src/grafix/core/parameters/labels_ops.py`。
# 何を: ParamStore の label（(op, site_id) -> label）更新手続きを提供する。
# なぜ: 書き込み経路を ops に固定し、呼び出し側が直に辞書を触らないようにするため。

from __future__ import annotations

from .frame_params import FrameLabelRecord
from .store import ParamStore


def set_label(store: ParamStore, *, op: str, site_id: str, label: str) -> None:
    """(op, site_id) のラベルを上書きする。"""

    store._labels_ref().set(op, site_id, label)


def merge_frame_labels(store: ParamStore, labels: list[FrameLabelRecord]) -> None:
    """フレーム内で観測したラベル設定をストアへ反映する。"""

    store_labels = store._labels_ref()
    for rec in labels:
        store_labels.set(rec.op, rec.site_id, rec.label)


__all__ = ["set_label", "merge_frame_labels"]

