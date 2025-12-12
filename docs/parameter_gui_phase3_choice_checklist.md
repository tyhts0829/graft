# どこで: docs/parameter_gui_phase3_choice_checklist.md

# 何を: `src/app/parameter_gui.py` の kind ディスパッチに `choice` を追加し、手動スモークで確認できるようにする。

# なぜ: 4 列テーブル（label / control / min-max / cc+override）の骨格は維持したまま、kind ごとの差分を widget 関数に閉じ込めて拡張したいから。

## 決定事項

- choice の control は `imgui.radio_button(label, active)` を choices 個ぶん並べて使う
- `choices` が `None` または空は例外（早期に検知）
- `ui_value` は文字列として保持し、表示用に index へ変換する
  - `ui_value` が choices に無い場合は先頭に丸めて `(changed=True, value=choices[0])` を返す
- choice の min-max 列は空（`ui_min/ui_max` は使用しない）
- choice の cc / override 列は `cc_key` と `override` を表示する

## チェックリスト

- [x] `src/app/parameter_gui.py` に `widget_choice_radio` を追加
- [x] `_KIND_TO_WIDGET` に `choice` を登録
- [x] `render_parameter_row_4cols` で `choice` の min-max 列を空にする
- [x] 手動スモークを追加（`tests/manual`）
  - [x] `tests/manual/test_parameter_gui_choice_combo.py`
  - [x] pytest ではなくスクリプトとして実行
- [x] チェックリスト更新（完了チェック）
