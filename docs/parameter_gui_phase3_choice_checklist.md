# どこで: docs/parameter_gui_phase3_choice_checklist.md

# 何を: `src/app/parameter_gui.py` の kind ディスパッチに `choice` を追加し、手動スモークで確認できるようにする。

# なぜ: 3 列テーブル（label / control / meta）の骨格は維持したまま、kind ごとの差分を widget 関数に閉じ込めて拡張したいから。

## 決定事項

- choice の control は `imgui.radio_button(label, active)` を choices 個ぶん並べて使う
- `choices` が `None` または空は例外（早期に検知）
- `ui_value` は文字列として保持し、表示用に index へ変換する
  - `ui_value` が choices に無い場合は先頭に丸めて `(changed=True, value=choices[0])` を返す
- choice の meta 列は `override` のみ表示（`ui_min/ui_max/cc_key` は非表示）

## チェックリスト

- [x] `src/app/parameter_gui.py` に `widget_choice_combo` を追加
- [x] `_KIND_TO_WIDGET` に `choice` を登録
- [x] `render_parameter_row_3cols` の meta 列を `choice` 用に分岐（override のみ）
- [x] 手動スモークを追加（`tests/manual`）
  - [x] `tests/manual/test_parameter_gui_choice_combo.py`
  - [x] `RUN_GUI_TEST=1` のときだけ実行
- [x] チェックリスト更新（完了チェック）
