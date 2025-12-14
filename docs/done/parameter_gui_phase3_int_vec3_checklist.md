# どこで: docs/parameter_gui_phase3_int_vec3_checklist.md

# 何を: `src/app/parameter_gui.py` の kind ディスパッチに `int` と `vec3` を追加し、手動スモークで確認できるようにする。

# なぜ: 4 列テーブル（label / control / min-max / cc+override）の骨格は維持したまま、kind ごとの差分を widget 関数に閉じ込めて拡張したいから。

## 決定事項

- int の `ui_min/ui_max` が `None` のときは `-10..10` にフォールバック
- vec3 の `ui_min/ui_max` が `None` のときは `-1.0..1.0` にフォールバック（float と同じ）
- slider の visible label は空（`##value`）。label 列に `op#ordinal` を表示

## チェックリスト

- [x] `src/app/parameter_gui.py` の widget を追加
  - [x] `widget_int_slider`（`imgui.slider_int("##value", ...)`）
  - [x] `widget_vec3_slider`（`imgui.slider_float3("##value", ...)`）
  - [x] `_KIND_TO_WIDGET` に `int` / `vec3` を登録
- [x] `src/app/parameter_gui.py` の range/validation を追加
  - [x] `int` 用レンジ関数（meta 由来のデフォルトが `ui_min > ui_max` は例外。GUI の min-max 入力では例外にしない）
  - [x] `vec3` は float と同一レンジで扱う（meta 由来のデフォルトが `ui_min > ui_max` は例外。GUI の min-max 入力では例外にしない）
- [x] `render_parameter_row_4cols` の min-max 入力を kind で分岐
  - [x] `int`: `imgui.drag_int_range2`
  - [x] `float/vec3`: `imgui.drag_float_range2`
  - [x] `cc_key` と `override` は `float/int/vec3` のみ表示し、`bool/str/choice` は空
- [x] 手動スモークを追加（`tests/manual`）
  - [x] `tests/manual/test_parameter_gui_int_slider.py`
  - [x] `tests/manual/test_parameter_gui_vec3_slider.py`
  - [x] pytest ではなくスクリプトとして実行
- [x] チェックリスト更新（完了チェック）
