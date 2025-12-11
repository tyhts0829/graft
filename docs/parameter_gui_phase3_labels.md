# どこで: docs/parameter_gui_phase3_labels.md
# 何を: ラベル機能の現状と残タスクをまとめる（name=… 方式が現行方針）。
# なぜ: 旧 `.label()` 案は廃止し、実装済み/未実装を明確にするため。

## 方針（現行）
- ラベル付与は `G(name=...)` / `E(name=...)` で行う（`.label()` は廃止）。
- ラベルは ParamStore に保存・永続化され、snapshot に (meta, state, ordinal, label) として含める。
- ラベルは上書き可（最終値採用）。空/None はデフォルト名に置換。長さ上限 64 文字でトリム。
- GUI では snapshot の label をヘッダ表示し、重複名には `name#1` など連番を付与する（未実装）。

## 実装状況
- [x] ParamStore: ラベル保存・JSON 永続化・snapshot 拡張。
- [x] Primitive: `G(name=...).<primitive>()` でラベル設定。
- [x] Effect: `E(name=...).<effect>()...` でチェーンラベル設定（`.label()` 廃止）。
- [x] テスト: `tests/parameters/test_label_namespace.py` 追加。
- [ ] GUI ヘッダ: label 表示と重複連番付与。
- [ ] ドキュメント反映: impl_plan / checklist への記載更新。

## 残タスク（フェーズ3で実装）
- snapshot から label を取り出し、ヘッダ生成時に表示。重複名は `name#1` 形式に加工。
- impl_plan / checklist に name=… の使い方とラベル表示フローを追記。
