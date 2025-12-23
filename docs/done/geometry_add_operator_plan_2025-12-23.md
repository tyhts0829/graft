# Geometry `+` 演算子追加（concat）チェックリスト（2025-12-23）

目的: `g1 + g2` を `concat` として扱い、複数 Geometry を直感的に結合できるようにする。

## チェックリスト

- [x] 仕様確定: `+` は `concat` ノード生成（`L([..])` の暗黙 concat と同等）
- [x] 実装: `Geometry.__add__` / `__radd__`（`sum()` 対応）+ `concat` のフラット化
- [x] テスト追加: `+` / `sum()` / フラット化 / 型違いで `TypeError`
- [x] 最小検証: `pytest -q tests/core/test_geometry_add.py`
- [ ] 最小検証: `ruff check src/grafix/core/geometry.py tests/core/test_geometry_add.py`（ruff が環境に無く未実行）

## メモ/確認事項

- `concat` は内部予約 op として扱う前提（ユーザー定義の primitive/effect と衝突させない）。
