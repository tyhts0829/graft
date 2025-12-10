# どこで: ルート/ui_min_max_rename_plan.md。
# 何を: ParamState/ParamStore 周辺の min/max を ui_min/ui_max 命名に揃えるための作業計画を整理する。
# なぜ: フィールドの意味が UI レンジであることを明示し、混乱を防ぐため。

- [x] 現状確認: ParamState フィールドと JSON 永続化のキー、参照箇所（store/resolver/tests）を洗い出す。
- [x] 命名変更方針決定: フィールド名・JSON キーともに ui_min/ui_max へ統一し、後方互換は持たせない。
- [x] 横断確認: UI 用レンジを示す min/max 命名が他モジュールに残っていないか `rg` で調査し、必要に応じて対象に追加。
- [x] 実装修正: store.py/state.py/resolver.py などでフィールド・変数名を ui_min/ui_max へ統一。
- [x] 永続化更新: to_json/from_json のキーを ui_min/ui_max に変更（旧キー読み込み不要）。
- [x] テスト修正・追加: 既存テスト更新と必要なら互換テストを追加し、対象限定の pytest を実行。
- [x] ドキュメント反映: parameter_spec.md や関連ドキュメントの用語を ui_min/ui_max に更新。
- [x] 動作確認: 影響範囲の手動/自動チェック結果を記録。
