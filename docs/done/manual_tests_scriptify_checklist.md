# どこで: docs/manual_tests_scriptify_checklist.md

# 何を: `tests/manual` 配下の手動 GUI テストを「pytest ではなく通常実行できるスクリプト」に作り替える。

# なぜ: `RUN_GUI_TEST=1` などの条件なしに手元で即起動でき、pytest を介さずに `python` で実行できる形にしたいから。

## 対象ファイル（全て）

- `tests/manual/test_pyglet_imgui_windows.py`
- `tests/manual/test_parameter_gui_float_slider.py`
- `tests/manual/test_parameter_gui_int_slider.py`
- `tests/manual/test_parameter_gui_vec3_slider.py`
- `tests/manual/test_parameter_gui_bool_checkbox.py`
- `tests/manual/test_parameter_gui_choice_combo.py`
- `tests/manual/test_parameter_gui_multirow.py`

## 決定

- 実行方法: `python tests/manual/<file>.py`
- 終了方法: ウィンドウのクローズ（タイムアウト自動終了はしない）

## チェックリスト

- [x] pytest 依存を除去
  - [x] `import pytest` と `pytest.mark.*` を削除
  - [x] `test_*` 関数を廃止し `main()` へ置換（pytest が実行しない形）
- [x] `RUN_GUI_TEST` ゲートを除去
  - [x] `RUN_GUI_TEST=1` がなくても実行されるようにする
- [x] 通常実行用のエントリポイントを追加
  - [x] 各ファイルに `if __name__ == "__main__": main()` を追加
- [x] 共通ランナーの導入（任意）
  - [x] 重複している pyglet/imgui 初期化・ループ・Retina 対応コードを `tests/manual/_runner.py` に集約
  - [x] 各スクリプトは「UI を描く関数」だけを渡す構造にする
- [x] 最小検証
  - [x] `python -m py_compile` で全ファイルが構文的に壊れていないことを確認

## 追加メモ（実装中に気づいたら追記）

- ここに追記する
