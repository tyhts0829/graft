# src/api/__init__.py
# 公開 API パッケージのエントリポイント。G/E 名前空間を再エクスポートする。

from __future__ import annotations

from .api import E, G, EffectBuilder

__all__ = ["E", "G", "EffectBuilder"]

