# config.yaml 管理候補（run 引数以外の定数）調査メモ（2025-12-28）

目的: `grafix.api.run()` の引数で指定できないが、コード内で **定数として固定されている値**のうち、`config.yaml` で管理したほうがよさそうなものを洗い出す。

前提:

- `run()` で指定できる値（例: `canvas_size`, `render_scale`, `fps` など）は対象外。
- 「アルゴリズムの安全柵（EPS / MAX\_\* など）」は、基本は config 化しない（再現性・品質・保守が落ちやすい）。

調査方法（機械的な抽出）:

- `src/grafix/` で `^[A-Z][A-Z0-9_]* = ...` に一致する定数定義を抽出して目視分類。
- 追加で、`run()` 以外の「固定値（定数化されていないが固定されている）」も参考として列挙。

## 結論（config.yaml 化の優先候補）

### A. 置き場所/環境依存が強いもの（config 向き）

1. ウィンドウ位置

- 定数: `DRAW_WINDOW_POS = (25, 25)`, `PARAMETER_GUI_POS = (950, 25)`
- 場所: `src/grafix/api/runner.py`
- 理由: モニタ構成や解像度で「最適値」が変わる。スケッチごとにコードへ埋めるより user config の方が自然。
- キー案:
  - `ui.window_positions.draw: [25, 25]`
  - `ui.window_positions.parameter_gui: [950, 25]`

2. Parameter GUI ウィンドウサイズ

- 定数: `DEFAULT_WINDOW_WIDTH = 800`, `DEFAULT_WINDOW_HEIGHT = 1000`
- 場所: `src/grafix/interactive/parameter_gui/pyglet_backend.py`
- 理由: 画面サイズ/スケールや好みで変わる。特にノート PC/外部モニタで “見切れ” が起きやすい。
- キー案:
  - `ui.parameter_gui.window_size: [800, 1000]`

### B. 出力品質/重さのトレードオフ（好みが出るので config 候補）

3. PNG 変換スケール

- 定数: `PNG_SCALE = 8.0`
- 場所: `src/grafix/export/image.py`
- 理由: PNG のサイズ/生成コストを決める重要パラメータ。用途（プレビュー用/印刷用）で変えたい。
- キー案:
  - `export.png.scale: 8.0`
