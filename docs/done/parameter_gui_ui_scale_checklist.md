# どこで: `docs/parameter_gui_ui_scale_checklist.md`。
# 何を: Parameter GUI の「フォントサイズ」をお試しで変更するチェックリスト。
# なぜ: Retina/DPI とは別に、まずは文字だけ大きくして見やすさを上げるため。

## ゴール

- Parameter GUI の既定フォントを `io.fonts.add_font_from_file_ttf(...)` で差し替えられる。
- ウィンドウサイズは固定のまま（文字だけが大きくなる）。
- 起動側（`run()` 引数）からの指定は不要（コード内の定数で調整）。

## 実装方針（案）

- `src/app/parameter_gui/gui.py` の `ParameterGUI.__init__()` で:
  - `io.fonts.clear()` で既定フォント群を消す
  - `io.fonts.add_font_from_file_ttf(path, size_px)` でフォントを追加（これが既定になる）
  - `renderer.refresh_font_texture()` を呼び、GPU 側のフォントテクスチャを更新

## チェックリスト

- [x] `src/app/parameter_gui/gui.py`: `fonts.clear()` + `add_font_from_file_ttf(...)` で既定フォントを差し替える
- [ ] 手動確認: Parameter GUI を起動し、文字サイズが変わることを確認する
- [ ] 微調整: `src/app/parameter_gui/gui.py` の `_DEFAULT_GUI_FONT_SIZE_PX` を好みに合わせて調整する

## 既知のトレードオフ

- フォントファイルのパスは環境依存のため、存在しない環境ではデフォルトフォントのままになる。
- より正攻法にするなら「起動側からフォント/サイズを渡す」「同梱フォントを使う」などの設計が必要。

## 相談したい点（事前確認）

- [ ] このまま「文字だけ」運用で十分？ それとも次は `style.scale_all_sizes()` も入れて全体ズームに寄せる？
