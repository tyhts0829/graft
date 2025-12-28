# ParamStore: 未登録引数の自己修復（無視 + 終了時に永続化から削除）チェックリスト（2025-12-28）

目的: primitive/effect の引数名変更で ParamStore 永続化データと現行スキーマがズレたときに、**GUI を落とさず**、かつ **次回保存でゴミを自動的に消す**。

背景:

- 引数名をリネームすると、過去の `param_store/*.json` に旧 arg が残り得る。
- 現状は GUI 側の表示順計算が strict で、未登録 arg があると例外で落ちる。
  - 例: `ValueError: primitive 引数が未登録です: op='text' arg='tolerance'`

方針（今回の決定）:

- **整合検査**（現行の primitive/effect レジストリが持つ `param_order` と照合）で「未登録 arg」を検出する。
- 検出した未登録 arg は **実行中は無視**（GUI の行として扱わない / 落とさない）。
- 未登録 arg は **終了時の保存（`save_param_store`）で永続化から削除**する（自己修復）。

非目的:

- 旧引数名 → 新引数名の自動移行（rename map/互換ラッパー/シム）は作らない
- `op` 自体が不明なデータを消す（プラグイン未ロード等の可能性があるため）
- 例外処理を厚くして本質を見えなくする（最小限の実装に留める）

## 0) 事前に決める（あなたの確認が必要）

- [ ] 「未登録」の定義は `param_order` 基準（= 現行の `primitive_registry.get_param_order(op)` / `effect_registry.get_param_order(op)` に含まれない arg）でよい
- [ ] 対象は `op in primitive_registry` と `op in effect_registry` のみ（`op` 不明は保持）でよい
- [ ] GUI の挙動: 未登録 arg は **表示しない**（完全に無視）でよい
- [ ] ログ: 未登録 arg を検出/削除したら、終了時保存で **1 回だけ** warning を出す（出力先は `logging`）でよい

## 1) 受け入れ条件（完了の定義）

- [ ] 引数名変更後でも Parameter GUI が落ちない（少なくとも `_order_rows_for_display` 由来で例外にならない）
- [ ] 未登録 arg は GUI に出ない（= 無視される）
- [ ] 終了時に `save_param_store()` が未登録 arg を削除し、次回起動で再出現しない
- [ ] 追加テストが通る:
  - [ ] `PYTHONPATH=src pytest -q tests/core/parameters/test_persistence.py`
  - [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/`（必要な範囲だけ）
- [ ] `ruff check .`
- [ ] `mypy src/grafix`

## 2) 実装方針（最小）

### A) 実行中（GUI）: 未登録 arg を “行として扱わない”

- 対象: `src/grafix/interactive/parameter_gui/store_bridge.py`
- `render_store_parameter_table()` 内で `rows_before_raw` を作った直後に、未登録 arg 行をフィルタする。
  - 判定は「op が primitive/effect で、arg がレジストリの `param_order` に無い」。
  - フィルタした結果は `rows_before`（描画対象）にのみ反映し、store 自体はその場では触らない（保存時に消す）。
- 追加の安全柵として、`_primitive_arg_index()` / `_effect_arg_index()` は未登録 arg を例外にせず末尾扱い（巨大 index）にする（将来の呼び出し経路変化で落ちないため）。

### B) 終了時（永続化）: 未登録 arg を ParamStore から削除する

- 対象: `src/grafix/core/parameters/prune_ops.py`
  - [ ] `prune_unknown_args_in_known_ops(store)`（仮名）を追加
  - [ ] 削除対象は **key 単位**（`(op, site_id, arg)`）で、以下の全てから消す:
    - [ ] `store._meta`
    - [ ] `store._states`
    - [ ] `store._explicit_by_key`（`encode_param_store` が explicit を無条件に永続化するため）
  - [ ] `op` が primitive/effect として登録済みのときだけ arg を検査する（`op` 不明は保持）
- 対象: `src/grafix/core/parameters/persistence.py`
  - [ ] `save_param_store()` 内で `prune_unknown_args_in_known_ops(store)` を呼ぶ（`prune_stale_loaded_groups(store)` と同列の「保存前掃除」）
  - [ ] 何か削除した場合だけ `logging.warning` で `(op, site_id, arg)` の要約を出す（長ければ件数 + 先頭 N 件）

## 3) 変更箇所（ファイル単位）

- [ ] `src/grafix/interactive/parameter_gui/store_bridge.py`
  - [ ] 未登録 arg 行のフィルタ（無視）
  - [ ] `_primitive_arg_index` / `_effect_arg_index` を「例外→末尾」へ変更
- [ ] `src/grafix/core/parameters/prune_ops.py`
  - [ ] 未登録 arg の key 単位 prune を追加
  - [ ] `__all__` 更新
- [ ] `src/grafix/core/parameters/persistence.py`
  - [ ] 保存前 prune の呼び出し追加 + warning
- [ ] テスト追加/更新
  - [ ] `tests/core/parameters/test_persistence.py` に「未登録 arg が save で消える」ケースを追加
  - [ ] `tests/interactive/parameter_gui/` に「未登録 arg があっても order が落ちない」最小テストを追加（imgui 依存は避け、pure 関数を直接叩く）
- [ ] （任意）`src/grafix/core/parameters/architecture.md` に「保存前に unknown arg も prune」追記

## 4) 実装手順（順序）

- [ ] 事前確認: `git status --porcelain` で依頼範囲外の差分/未追跡を把握（触らない）
- [ ] テスト追加（先に落ちる再現を最小化）
- [ ] GUI 側のフィルタ + index fallback を実装
- [ ] 永続化前 prune を実装（prune_ops + persistence）
- [ ] 追加テストを通す
- [ ] `ruff check .` / `mypy src/grafix`

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] 「未登録 arg」を GUI に “警告として見せる” かは要検討（今回は「無視」を優先）
- [ ] `op` 不明データの扱い（保持で良いが、ファイル肥大化が問題なら別タスクで prune 方針を決める）
