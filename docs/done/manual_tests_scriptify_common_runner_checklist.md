# どこで: docs/manual_tests_scriptify_common_runner_checklist.md

# 何を: `tests/manual` の pyglet/imgui 手動テスト用「共通ランナー」を導入し、各スクリプトの重複初期化・ループ・Retina 対応を集約する。

# なぜ: 手動 GUI テストを増やしてもボイラープレートが増えない形にして、差分を「UI の中身」に集中させるため。

## 対象

- `tests/manual/_runner.py`（新規）
- `tests/manual/test_parameter_gui_float_slider.py`
- `tests/manual/test_parameter_gui_int_slider.py`
- `tests/manual/test_parameter_gui_vec3_slider.py`
- `tests/manual/test_parameter_gui_bool_checkbox.py`
- `tests/manual/test_parameter_gui_choice_combo.py`
- `tests/manual/test_parameter_gui_string_input.py`
- `tests/manual/test_parameter_gui_multirow.py`
- `docs/manual_tests_scriptify_checklist.md`（チェック更新）

## 方針（案）

- ランナーは「ウィンドウ作成・イベントループ・Retina(IO)補正・後始末」を担当する。
- 各スクリプトは `draw_ui(ctx)`（UI を描く関数）を定義してランナーに渡す。
  - 状態はクロージャ（`nonlocal`）で保持する。
  - `Quit` は `ctx.stop()` を呼ぶ（無ければウィンドウを閉じて終了）。
  - `tests/manual/test_pyglet_imgui_windows.py` は要件により変更しない。

## チェックリスト（承認後に実施）

- [x] ランナー API を確定する
  - [x] `tests/manual/_runner.py` に公開関数（`run_pyglet_imgui(...)`）を 1 つ用意
  - [x] `draw_ui(ctx)` の `ctx` に含める要素を確定（`pyglet/imgui` モジュール、`dt`、`frame`、`stop`、`window`）
  - [x] 追加ウィンドウ（任意）は今回は非対応（必要になったら拡張）
- [x] `tests/manual/_runner.py` を実装する
  - [x] `pyglet/imgui` import（失敗時 `SystemExit`）
  - [x] display probe（最小ウィンドウ作成で早期終了）
  - [x] imgui context 作成 + current context 設定
  - [x] pyglet window 作成（GUI）
  - [x] imgui pyglet renderer 初期化 + フォントテクスチャ更新（存在する場合）
  - [x] ループ（tick / dispatch_events / new_frame / Retina 補正 / draw_ui / render / flip / sleep）
  - [x] 終了処理（renderer shutdown / destroy_context / window close）
- [x] 各 manual スクリプトをランナー使用に置換する
  - [x] `tests/manual/test_parameter_gui_float_slider.py`
  - [x] `tests/manual/test_parameter_gui_int_slider.py`
  - [x] `tests/manual/test_parameter_gui_vec3_slider.py`
  - [x] `tests/manual/test_parameter_gui_bool_checkbox.py`
  - [x] `tests/manual/test_parameter_gui_choice_combo.py`
  - [x] `tests/manual/test_parameter_gui_string_input.py`
  - [x] `tests/manual/test_parameter_gui_multirow.py`（`Quit` ボタンを `ctx.stop()` に接続）
- [x] チェックリストを更新する
  - [x] `docs/manual_tests_scriptify_checklist.md` の「共通ランナーの導入」を `[x]` にする
- [x] 最小検証を実行する
  - [x] `python -m py_compile tests/manual/*.py`

## 事前確認したい点

- [x] ランナー関数名は `run_pyglet_imgui(...)` のようなシンプル命名で良い？
- [x] `test_pyglet_imgui_windows.py` は変更しない（要件）
