# どこで: `src/grafix/interactive/parameter_gui/gui.py` / `src/grafix/interactive/parameter_gui/pyglet_backend.py`。

# 何を: Parameter GUI のフォントサイズをモニタ（Retina/外部）に追従させる。

# なぜ: Retina で「読みやすい/クッキリ」を満たしつつ、外部モニタで文字が大きくなりすぎるのを避けるため。

## 背景 / 現状

- 現状は `src/grafix/interactive/parameter_gui/gui.py` のフォントサイズを固定値で使用している。
- pyglet（特に macOS / dpi_scaling="platform"）では、ウィンドウサイズやマウス座標が「backing pixel」基準になり得る。
  - その場合、外部モニタ基準の `base_px` をそのまま使うと Retina では物理サイズが小さく見える。
- その結果、「Retina 側でいい感じ」に合わせると外部モニタでは文字が大きすぎる、逆に外部に合わせると Retina で小さすぎる。

## ゴール

- Retina（高 DPI）で文字が滲まずクッキリ表示される。
- 外部モニタでも文字が過大にならない（基準サイズは外部モニタ寄りで決める）。
- ウィンドウをモニタ間で移動したときに自動で追従する（必要時のみフォント再生成）。
- 実装は必要十分にシンプル（毎フレーム重い処理をしない）。

## 実装方針（案）

### 1) 外部基準 `base_px` を「DPI スケール」で補正してフォントを作る（採用）

目的: 外部モニタ基準のサイズ感を維持しつつ、Retina では物理サイズが小さくならないようにする。

- `dpi_scale = window.scale`（もしくは `window.get_pixel_ratio()`）を「backing scale」として扱う。
- フォント生成サイズを `font_px = base_px * dpi_scale` にする（Retina では大きい px でアトラスを作る）。
- `io.font_global_scale` は 1.0 固定にする（座標系が backing pixel の前提）。

### 2) `base_px` は外部モニタ基準で決める

- 外部モニタで「ちょうど良い」値を `base_px` とする。
- 初期値は 12px（外部基準。Retina は `window.scale` で補正する前提）。

## 実装チェックリスト

- [x] 事前確認: `base_px` の暫定値を決める → 12（外部基準）。
- [x] フォントパスを `cwd` 依存から `__file__` 基準へ変更する（起動場所によらず見つかる）。
- [x] `fb_scale` の算出を `window.scale`（または `get_pixel_ratio`）優先にする。
- [x] `ParameterGUI` に「直近の `fb_scale`」を保持するフィールドを追加する。
- [x] `draw_frame()` の冒頭（`imgui.new_frame()` より前）で `fb_scale` を計算する。
- [x] `fb_scale` が変化したときだけフォントを再生成する（`io.fonts.clear()` → `add_font_from_file_ttf(...)` → `refresh_font_texture()`）。
- [x] `io.font_global_scale` は 1.0 に固定する。
- [x] フォントファイルが無い環境でも動く（その場合は自動スケールは無効）。
- [x] 手動確認: 内蔵 Retina / 外部モニタ / ウィンドウ移動（Retina↔ 外部）で「クッキリ」「過大にならない」を確認する → OK
- [x] 微調整: `base_px` を確定し、定数名も “base” が伝わるものに変更する（例: `_GUI_FONT_SIZE_BASE_PX`）。

## 既知のトレードオフ

- モニタ移動直後など、`fb_scale` 変化タイミングでフォントテクスチャ更新が走り、軽いヒッチが起きる可能性がある（毎フレーム再生成はしない）。
- `display_fb_scale` は “Retina 倍率” の検知には効くが、「同じ 1.0 でも PPI が違う外部モニタ差」までは完全には吸収できない。
  - 必要なら将来案: 物理 DPI の取得、または GUI 内でのユーザー調整（スライダー/ショートカット）を追加する。

## 相談したい点（ユーザー確認）

- [x] `base_px` は 12 で開始する（外部基準）。
- [x] 「外部基準でサイズ固定」で進める。
