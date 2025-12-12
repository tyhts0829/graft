# どこで: `src/__init__.py`。
# 何を: ルート `src` パッケージを定義する。
# なぜ: namespace package と通常 package の混在を避け、import を安定させるため。

from __future__ import annotations

__all__ = []

