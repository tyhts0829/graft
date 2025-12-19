# drop effect 引数改善チェックリスト（2025-12-19）

対象: `src/grafix/core/effects/drop.py`

## 目的

- `drop` の引数を「分かりやすく」「意図しない挙動をしにくく」する。
- 旧仕様（OR 条件 + keep/drop）を維持しつつ、曖昧さ・誤用を減らす。

## 前提（現状）

- `min_length/max_length` は 0 未満を無効扱い（sentinel は -1.0 だが「0 未満」全般が無効）。
- `keep_mode/by` は `ParamMeta(kind="choice")` だが、未知文字列でもそのまま effect 実装に渡り得る。
- `offset` は interval の位相だが、名前が「距離オフセット」と紛らわしい。

## 方針（提案）

- 例外で落とすより、「無効値は no-op（=入力を返す）」に寄せる（既存 effect の流儀に合わせる）。
- 引数名・doc を優先して改善し、ロジックの複雑化はしない。

## 変更候補（要確認）

- `offset` をリネームする（破壊的変更）
  - 候補: `interval_offset` / `phase` / `index_offset`；index_offset で
  - 目的: 「インデックス位相」だと一目で分かるようにする
- `probability` の異常値ポリシーを決める
  - 候補 A: 非有限（NaN/inf）や範囲外は no-op
  - 候補 B: <0 は 0、>1 は 1 に丸める、非有限は no-op；こちらで
- `keep_mode/by` の未知値ポリシーを決める
  - 候補 A: 未知値なら no-op；こちらで。
  - 候補 B: 未知値ならデフォルト（keep_mode="drop", by="line"）に丸める

## 実装 TODO（チェックリスト）

### 仕様確定（先に確認）

- [ ] `offset` の新しい名前を決める（破壊的変更）
- [ ] `probability` の異常値ポリシーを確定する
- [ ] `keep_mode/by` の未知値ポリシーを確定する

### 実装（drop 本体）

- [ ] `offset` を新名称へ変更（`drop_meta` と関数シグネチャ）
- [ ] docstring を新名称に合わせて更新（API スタブに反映される）
- [ ] `probability` を検証/正規化（非有限の扱いを確定方針どおりに）
- [ ] `keep_mode` を検証/正規化（未知値時の扱いを確定方針どおりに）
- [ ] `by` を検証/正規化（未知値時の扱いを確定方針どおりに）

### テスト

- [ ] `tests/core/effects/test_drop.py` を更新（リネーム追従）
- [ ] 異常値テストを追加
  - [ ] `probability` が NaN/inf/範囲外のときの挙動
  - [ ] `keep_mode/by` が未知値のときの挙動

### スタブ同期（公開 API 変更があるため）

- [ ] `python tools/gen_g_stubs.py > src/grafix/api/__init__.pyi`（生成結果で上書き）
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`

### 検証（対象限定で実行）

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_drop.py`
- [ ] `mypy src/grafix/core/effects/drop.py`
- [ ] `ruff check src/grafix/core/effects/drop.py tests/core/effects/test_drop.py`（ruff が無い場合は要承認で導入）
