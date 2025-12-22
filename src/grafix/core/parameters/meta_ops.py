# どこで: `src/grafix/core/parameters/meta_ops.py`。
# 何を: ParamStore の ParamMeta 更新手続きを提供する。
# なぜ: 書き込み経路を ops に固定し、呼び出し側が直に辞書を触らないようにするため。

from __future__ import annotations

from .key import ParameterKey
from .meta import ParamMeta
from .store import ParamStore


def set_meta(store: ParamStore, key: ParameterKey, meta: ParamMeta) -> None:
    """ParamMeta を上書き保存する。"""

    store._set_meta(key, meta)


__all__ = ["set_meta"]

