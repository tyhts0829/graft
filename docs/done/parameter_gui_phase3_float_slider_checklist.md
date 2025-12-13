# どこで: docs/parameter_gui_phase3_float_slider_checklist.md

# 何を: kind ディスパッチの最初の一歩として float スライダーを実装するためのチェックリスト。

# なぜ: kind ごとに関数を切り出し、`tests/manual` で 1 行ずつデバッグしながら確認できる進め方に寄せるため。

## スコープ

- 対象 kind: `float` のみ（他 kind は未対応のまま例外）
- UI backend: pyimgui（`imgui.slider_float`）
- テスト: `tests/manual` の手動スモークのみ（スクリプト実行）

## チェックリスト

- [x] 現状把握
  - [x] `src/app/parameter_gui.py` の有無と既存設計の確認（新規作成）
  - [x] `ParamMeta.kind` / `ParameterRow.kind` の値体系を確認（`float` 想定）
- [x] ウィジェット共通 I/F を確定
  - [x] kind → widget 関数のレジストリを `parameter_gui.py` に定義
  - [x] widget 関数の返り値を `(changed, value)` に統一
- [x] float スライダー（kind=`float`）の widget 関数を実装
  - [x] `imgui.slider_float` を使用し `ui_min/ui_max` を `min_value/max_value` に渡す
  - [x] meta 由来のデフォルトが `ui_min > ui_max` は例外にする（GUI の min-max 入力では例外にしない）
- [x] 手動スモーク（`tests/manual`）を追加/更新
  - [x] 単一ウィンドウで `ParameterRow(kind="float")` 1 行だけ描画する最小ループを用意
  - [x] `widget registry` 経由で float スライダーが動くことを確認
  - [x] pytest ではなく `python tests/manual/<file>.py` で実行
- [ ] ドキュメントを最小更新
  - [ ] `docs/parameter_gui_phase3_checklist.md` の該当項目の進捗に反映（必要な場合のみ）

## 事前に確認したい点（あなたへの質問）

- [x] `ui_min/ui_max` が `None` の float はどう扱う？
  - 決定: `-1.0..1.0` にフォールバック
- [x] スライダーの表示ラベルは何を使う？
  - 決定: 4列テーブルの label 列に `op#ordinal` を表示し、slider の visible label は空（`##id`）。
