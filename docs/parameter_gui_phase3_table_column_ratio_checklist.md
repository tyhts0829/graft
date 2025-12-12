# どこで: docs/parameter_gui_phase3_table_column_ratio_checklist.md

# 何を: pyimgui の table の 3 列幅を「比率（weight）」で指定できるようにするチェックリスト。

# なぜ: 3 列（label / control / meta）の視認性を安定させ、レイアウト調整をコード上で明示できるようにするため。

## 方針

- 幅指定は「比率」で扱う（ImGui の stretch column + weight）。
- `render_parameter_table` に `column_weights=(w1, w2, w3)` を追加し、呼び出し側で調整できる形にする。
- 不正な weight（長さ不一致、0 以下）は `ValueError` で早期に検知する（過度な互換処理はしない）。

## チェックリスト

- [x] `src/app/parameter_gui.py` の API を更新
  - [x] `render_parameter_table(rows, *, column_weights=(...))` を追加
  - [x] `begin_table(..., flags=imgui.TABLE_SIZING_STRETCH_PROP)` を指定
  - [x] `table_setup_column(..., flags=imgui.TABLE_COLUMN_WIDTH_STRETCH, init_width_or_weight=weight)` を 3 列分設定
- [x] `tests/manual/test_parameter_gui_float_slider.py` を更新
  - [x] `render_parameter_table([row], column_weights=(0.20, 0.55, 0.25))` として明示
- [x] 実装後にチェックリストを更新

## 確認したい点（あなたに決めてほしい）

- [ ] 既定の比率をどうする？
  - 案A: `(0.20, 0.55, 0.25)`（control を太め） ← 採用
  - 案B: `(0.25, 0.50, 0.25)`（均等寄り）
  - 案C: あなた指定（数値3つ）
