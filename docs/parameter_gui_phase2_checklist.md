# どこで: docs/parameter_gui_phase2_checklist.md
# 何を: フェーズ2（ViewModel + 変換ユーティリティ）の実装タスク分解。
# なぜ: GUI 実装前に ParamStore から UI 行を生成し、入力を型変換する純粋関数を整備するため。

## 方針
- DPG 依存なしの純粋関数で行モデルと入力変換を実装し、単体テストでカバーする。
- Snapshot は (key -> (meta, state, ordinal)) を前提に、行構築も変換もこの形だけを受け取る。
- kind ごとの変換ロジックを一箇所に集約し、GUI 側から再利用できる形にする。

## チェックリスト（具体的な変更対象・内容）
- [ ] 現状確認  
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ2要件、既存の snapshot 形式とテスト状況。  
  - 内容: 必要な入力/出力形の確認メモを残す。
- [ ] データモデル設計  
  - 対象: 新規モジュール案 `src/parameters/view.py`（仮）。  
  - 内容: `ParameterRow` dataclass（label/op/arg/kind/ui_value/ui_min/ui_max/choices/cc_key/override/ordinal/last_seen）を定義。last_seen は snapshot 取得時の monotonic カウンタで付与するか要検討。
- [ ] Row 生成ロジック  
  - 対象: `src/parameters/view.py` に `rows_from_snapshot(snapshot: dict[ParameterKey, tuple[ParamMeta, ParamState, int]]) -> list[ParameterRow]` を実装。  
  - 内容: op+ordinal で並べ替え、欠損 meta はスキップ、choices 不整合時の補正方針を決定。  
  - テスト: `tests/parameters/test_parameter_rows.py` で並び順・値コピー・欠損時スキップを検証。
- [ ] 入力変換ユーティリティ  
  - 対象: `src/parameters/view.py` に kind 別変換関数 `coerce_input(value, meta)` を実装。  
  - 内容: int/float/vec3/bool/str/enum などへの型変換と検証、ui_min/ui_max が壊れている場合の扱い、choices 外の値の補正。  
  - テスト: `tests/parameters/test_parameter_coerce.py` で各 kind の変換・エラー補正を確認。
- [ ] ParamStore 連携ヘルパ  
  - 対象: `src/parameters/store.py` または `view.py` に `apply_ui_update(store, key, new_value, *, meta)` を実装。  
  - 内容: 変換後の値を `ParamState.ui_value` に保存し、override の切替と cc_key 設定の更新ロジックを定義。  
  - テスト: `tests/parameters/test_parameter_updates.py`（仮）で override 切替と値更新を確認。
- [ ] ドキュメント更新  
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ2項を完了に合わせて反映。  
  - 内容: 新設モジュール・テスト名を追記。
- [ ] 確認ポイント CP2  
  - 内容: 上記テスト結果とサンプル snapshot→rows の出力例を共有し、GUI 仕様に合致するか確認をもらう。
