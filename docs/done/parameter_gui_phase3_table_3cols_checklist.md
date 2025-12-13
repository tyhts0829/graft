# どこで: docs/parameter_gui_phase3_table_3cols_checklist.md

# 何を: pyimgui で「1 key = 1 行、4 列テーブル」を描画する骨格を追加するチェックリスト。

# なぜ: label / control / min-max / cc+override を分離し、1 行ずつデバッグ確認できる形で進めるため。

## スコープ

- 対象 kind: `float` / `int` / `vec3` / `bool` / `string` / `choice`（未知 kind は例外）
- レイアウト: 4 列テーブル
  - 1: label
  - 2: control（kind に応じたウィジェット）
  - 3: min-max（`ui_min` / `ui_max`。`float/int/vec3` のみ。`bool/string/choice` は空）
  - 4: cc / override（`cc_key` / `override`。`float/int/vec3` のみ。`bool/string/choice` は空）
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
    - [x] `bool/string/choice`: 描画しない（列を空にする）
    - [x] `ui_min/ui_max` が `None` の場合は既定レンジへフォールバック
    - [x] meta 由来のデフォルトが `ui_min > ui_max` の場合は例外（GUI の min-max 入力では例外にしない）
  - [x] column 4: cc / override を描画
    - [x] `float/int`: `cc_key` は `imgui.input_int`（`None` は `-1` 表示、`<0` を `None` とみなす）
    - [x] `vec3`: `cc_key` は `imgui.input_int3`（負数は `None` 扱い、全成分 `None` は `cc_key=None`）
    - [x] `override`: checkbox（clicked を changed として扱う）
    - [x] `bool/string/choice`: 描画しない（列を空にする）
- [x] `tests/manual` の手動スモークを更新
  - [x] `tests/manual/test_parameter_gui_float_slider.py` を「4 列テーブル」で描画するように変更
  - [x] 1 行だけ（float）で動作確認できる状態を維持
  - [x] pytest ではなくスクリプトとして実行

## 追加で確認したい点（あなたに最終確認）

- [x] label 列の表示は `op#ordinal` のみ
- [x] control（slider）側の visible label は空（`##id` で隠す）
