# どこで: docs/parameter_gui_phase3_labels.md
# 何を: label 機能を ParamStore ～ GUI に統合する実装計画。（※このファイルは旧 `.label()` 案。現行方針は `docs/parameter_gui_phase3_label_namespace.md` を参照）
# なぜ: primitive/effect から付与したラベルをヘッダ表示・永続化し、重複/長さ制約を明確にするため。

## 方針概要
- ラベルは ParamStore で永続化し、snapshot に含めて GUI へ渡す。
- primitive/effect チェーンのどこからでも `label(name=...)` を 1 回だけ呼べる。2 回目は例外。
- 同名が複数ある場合は GUI 側で `name#1`, `name#2` … のように連番を付与して表示。store には素の name を保存。
- name に長さ上限を設け、超過時はトリム（記録時点で実施）。空/未指定時はデフォルト（primitive: op 名、effect: effect#N）。

## 変更タスクリスト（旧 `.label()` 案、現状は未着手・非推奨）
- [ ] ParamStore にラベル辞書を追加し、to_json/from_json/snapshot へ組み込む。  
- [ ] primitive 側で `.label()` 受付を実装。  
- [ ] effect チェーン側で `.label()` 受付を実装。  
- [ ] GUI ヘッダで重複名に連番付与。  
- [ ] 長さ・空文字処理（64 文字トリムなど）。  
- [ ] テスト追加（ラベル永続化・重複時の扱い）。  
- [ ] ドキュメント更新。

## デフォルト表示ポリシー
- primitive: name 未指定 → op 名。  
- effect: name 未指定 → `effect#N`（N は ordinal を流用）。  
- 同名が複数存在 → `name#1`, `name#2`, … を GUI 側で付与。

## 例外ポリシー
- `label` の複数回呼び出し（同一 (op, site_id) で 2 回以上）: 例外。
- name が空/None は例外ではなくデフォルト名に置換。
- 文字数超過はトリム＋警告ログ（例外にはしない）。
