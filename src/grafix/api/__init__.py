# どこで: `src/grafix/api/__init__.py`。
# 何を: 公開 API パッケージのエントリポイントとして G/E/L/run と、ユーザー定義登録用の primitive/effect を再エクスポートする。
# なぜ: ユーザーコードからシンプルに API を import できるようにするため。

from __future__ import annotations

from .export import Export
from .effects import E
from .layers import L
from .primitives import G
from grafix.core.effect_registry import effect
from grafix.core.primitive_registry import primitive

__all__ = ["E", "Export", "G", "L", "effect", "primitive", "run"]


def run(*args, **kwargs):
    """公開 run API へのラッパ（遅延インポートで GUI 依存を後回しにする）。"""

    from .runner import run as _run

    return _run(*args, **kwargs)
