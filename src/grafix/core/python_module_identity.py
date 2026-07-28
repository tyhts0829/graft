"""Python の実行経路だけで変わる authoring module 名を正規化する。"""

from __future__ import annotations


def canonical_authoring_module_name(name: str) -> str:
    """spawn が直接実行 module に付ける別名を親 process の名前へ揃える。"""

    return "__main__" if name == "__mp_main__" else name
