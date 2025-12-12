# どこで: docs/parameter_gui_phase3_table_3cols_checklist.md

# 何を: pyimgui で「1 key = 1 行、4 列テーブル」を描画する骨格を追加するチェックリスト。

# なぜ: label / control / min-max / cc+override を分離し、1 行ずつデバッグ確認できる形で進めるため。

## スコープ

- 対象 kind: `float` / `int` / `vec3` / `bool` / `choice`（未知 kind は例外）
- レイアウト: 4 列テーブル
  - 1: label
  - 2: control（kind に応じたウィジェット）
  - 3: min-max（`ui_min` / `ui_max`）
  - 4: cc / override（`cc_key` / `override`）
- 手動確認: `tests/manual` のみ（スクリプトとして通常実行）

## チェックリスト

- [x] `src/app/parameter_gui.py` に「4 列テーブル描画」を追加
  - [x] `render_parameter_table(rows)` を追加（更新後 rows を返す）
  - [x] 1 row = 1 key（`ParameterRow`）のループにする
- [x] 1 行ぶんの描画関数を追加（デバッグしやすくする）
  - [x] `render_parameter_row_4cols(row)` に分離
  - [x] column 1: label を描画（表示は `op#ordinal`）
  - [x] column 2: 既存の kind→widget 関数を呼ぶ（現状は float のみ）
  - [x] column 3: min-max を描画
    - [x] `float/vec3`: `imgui.drag_float_range2`
    - [x] `int`: `imgui.drag_int_range2`
    - [x] `ui_min/ui_max` が `None` の場合は既定レンジへフォールバック
    - [x] `ui_min >= ui_max` は `ValueError`
  - [x] column 4: cc / override を描画
    - [x] `cc_key`: int 入力（`None` は `-1` 表示、`<0` を `None` とみなす）
    - [x] `override`: checkbox（clicked を changed として扱う）
- [x] `tests/manual` の手動スモークを更新
  - [x] `tests/manual/test_parameter_gui_float_slider.py` を「4 列テーブル」で描画するように変更
  - [x] 1 行だけ（float）で動作確認できる状態を維持
  - [x] pytest ではなくスクリプトとして実行

## 追加で確認したい点（あなたに最終確認）

- [x] label 列の表示は `op#ordinal` のみ
- [x] control（slider）側の visible label は空（`##id` で隠す）
