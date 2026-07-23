# どこで: `src/grafix/core/parameters/meta_ops.py`。
# 何を: ParamStore の ParamMeta 更新手続きを提供する。
# なぜ: 書き込み経路を ops に固定し、呼び出し側が直に辞書を触らないようにするため。

from __future__ import annotations

from .key import ParameterKey
from .meta import ParamMeta
from .store import ParamStore


def set_meta(store: ParamStore, key: ParameterKey, meta: ParamMeta) -> None:
    """ParamMeta を上書き保存する。"""

    base_revision = store.revision
    read = store._read()
    if read.meta(key) == meta:
        return
    next_meta = read.all_meta()
    next_meta[key] = meta
    mutation = store._mutation()
    mutation.prepare_history(expected_revision=base_revision, keys=(key,))
    mutation.commit_meta(
        expected_revision=base_revision,
        meta=next_meta,
    )


__all__ = ["set_meta"]
