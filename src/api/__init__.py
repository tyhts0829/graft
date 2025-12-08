# どこで: `src/api/__init__.py`。
# 何を: 公開 API パッケージのエントリポイントとして G/E/run を再エクスポートする。
# なぜ: ユーザーコードからシンプルに API を import できるようにするため。

from __future__ import annotations

from .api import E, G
from .run import run

__all__ = ["E", "G", "run"]
