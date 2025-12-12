# どこで: docs/parameter_gui_phase3_bool_checklist.md

# 何を: `src/app/parameter_gui.py` の kind ディスパッチに `bool` を追加し、手動スモークで確認できるようにする。

# なぜ: 3 列テーブル（label / control / meta）の骨格は維持したまま、kind ごとの差分を widget 関数に閉じ込めて拡張したいから。

## 決定事項

- bool の control は `imgui.checkbox("##value", state)` を使う
- checkbox の戻り値は `(clicked, state)` なので、`clicked` を `changed` として返す
- bool は meta（ui_min/ui_max/cc/override）を使わないので 3 列目は空

## チェックリスト

- [x] `src/app/parameter_gui.py` に `widget_bool_checkbox` を追加
- [x] `_KIND_TO_WIDGET` に `bool` を登録
- [x] 手動スモークを追加（`tests/manual`）
  - [x] `tests/manual/test_parameter_gui_bool_checkbox.py`
  - [x] `RUN_GUI_TEST=1` のときだけ実行
