# src コード改善チェックリスト（items()型注釈 / utils.pyヘッダ）（2025-12-12）

## 目的

- `src/core/*_registry.py` の `items()` 戻り値注釈から `type: ignore` を除去し、素直な型で表現する。
- `src/render/utils.py` のモジュール先頭コメントを、他ファイルと同じ形式（どこで/何を/なぜ）に揃える。

## 対象

- `src/core/effect_registry.py`
- `src/core/primitive_registry.py`
- `src/render/utils.py`

## 変更チェックリスト

- [x] `src/core/effect_registry.py` の `items()` の戻り値注釈を `collections.abc.ItemsView[str, EffectFunc]` に変更し、`type: ignore` を削除する
- [x] `src/core/primitive_registry.py` の `items()` の戻り値注釈を `collections.abc.ItemsView[str, PrimitiveFunc]` に変更し、`type: ignore` を削除する
- [x] `src/render/utils.py` の先頭に「どこで/何を/なぜ」ヘッダ（コメント or モジュールドック）を追加して他ファイルの形式に統一する（ロジック変更なし）
- [x] 変更後に `python -m py_compile` で上記 3 ファイルのみ構文チェックする

## 確認したいこと（事前）

- `ItemsView` の import は `collections.abc` で統一してよい？（`typing` 由来を避ける方針に合わせる想定）
- `src/render/utils.py` のヘッダは「`#` 3行コメント」形式で統一してよい？（既存は docstring 形式と `#` 形式が混在）

## 実装中に気づいたこと（追記欄）

- 構文チェックは `PYTHONDONTWRITEBYTECODE=1 python -m py_compile ...` とし、`__pycache__` を増やさない形で実施。
