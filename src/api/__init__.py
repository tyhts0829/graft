# どこで: `src/api/__init__.py`。
# 何を: 公開 API パッケージのエントリポイントとして G/E/run を再エクスポートする。
# なぜ: ユーザーコードからシンプルに API を import できるようにするため。

from __future__ import annotations

from .api import E, G, L

__all__ = ["E", "G", "L", "run"]


def run(*args, **kwargs):
    """公開 run API へのラッパ（遅延インポートで GUI 依存を後回しにする）。"""

    from .run import run as _run

    return _run(*args, **kwargs)
