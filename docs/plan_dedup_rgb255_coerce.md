# RGB255 正規化ロジック重複の解消計画（未実施）

目的: `_coerce_rgb255`（GUI 由来の RGB 値を int 化 + 0..255 clamp する処理）の重複を解消し、仕様変更時の修正漏れリスクを下げる。

対象:
- `src/graft/core/pipeline.py`（Layer style の override）
- `src/graft/interactive/runtime/draw_window_system.py`（背景色/グローバル線色の override）

---

## 改善アクション（チェックリスト）

### 0) 事前合意（ここだけ先に確認）

- [ ] 置き場所は `graft.core.parameters.style` で良い
- [ ] 関数名は `coerce_rgb255`（候補: `as_rgb255`）で良い
- [ ] 例外方針は現状維持（長さ 3 でない場合 `ValueError`）
- [ ] 変換方針は現状維持（`int()` 化 + 0..255 clamp、float 混在も許容）

### 1) コアユーティリティを追加する

- [ ] `src/graft/core/parameters/style.py` に `coerce_rgb255(value: object) -> tuple[int, int, int]` を追加する
- [ ] NumPy スタイル docstring（日本語）を付ける

### 2) 呼び出し側の重複を除去する

- [ ] `src/graft/core/pipeline.py` のローカル `_coerce_rgb255` を削除し、`coerce_rgb255` を使う
- [ ] `src/graft/interactive/runtime/draw_window_system.py` のローカル `_coerce_rgb255` を削除し、`coerce_rgb255` を使う

### 3) テストを追加/更新する（最小）

- [ ] `tests/core/parameters/test_style_entries.py` に `coerce_rgb255` のテストを追加する
  - [ ] 正常系: `(0, 128, 255)` → `(0, 128, 255)`
  - [ ] clamp/int: `(-1, 256, 0.2)` → `(0, 255, 0)` など
  - [ ] 異常系: 長さ不正で `ValueError`
- [ ] 既存テストが通ることを確認する（対象限定）

### 4) 静的チェック（対象限定）

- [ ] `ruff check` を変更ファイルに限定して実行する
- [ ] `mypy` を必要なら実行する（できれば対象を絞る）

---

## 事前確認したいこと / 追加提案

- [ ] GUI 側にも同様の処理がある（`src/graft/interactive/parameter_gui/widgets.py` の `_as_rgb255`）。今回のスコープに含めるか？
  - A: 今回は pipeline/runtime の 2 箇所に限定する
  - B: 3 箇所まとめて `coerce_rgb255` に統一する
- [ ] 例外メッセージを 3 箇所で統一するか（現状は文言が少し違う）
