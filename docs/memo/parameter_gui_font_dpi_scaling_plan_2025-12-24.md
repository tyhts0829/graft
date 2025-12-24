# どこで: `src/grafix/interactive/parameter_gui/gui.py` / `src/grafix/interactive/parameter_gui/pyglet_backend.py`。

# 何を: Parameter GUI のフォントサイズをモニタ（Retina/外部）に追従させる。

# なぜ: Retina で「読みやすい/クッキリ」を満たしつつ、外部モニタで文字が大きくなりすぎるのを避けるため。

## 背景 / 現状

- 現状は `src/grafix/interactive/parameter_gui/gui.py` の `_DEFAULT_GUI_FONT_SIZE_PX = 24.0` を固定で使用している。
- `src/grafix/interactive/parameter_gui/pyglet_backend.py` で `io.display_fb_scale` を毎フレーム計算しているが、フォントサイズや UI スケールには反映していない。
- その結果、「Retina 側でいい感じ」に合わせると、外部モニタでは文字が大きすぎる状態になる。

## ゴール

- Retina（高 DPI）で文字が滲まずクッキリ表示される。
- 外部モニタでも文字が過大にならない（基準サイズは外部モニタ寄りで決める）。
- ウィンドウをモニタ間で移動したときに自動で追従する（必要時のみフォント再生成）。
- 実装は必要十分にシンプル（毎フレーム重い処理をしない）。

## 実装方針（案）

### 1) 「見た目サイズ」と「クッキリ化」を分離する（推奨）

目的: “同じ見た目サイズ”のまま、Retina ではフォントアトラスだけ高解像度にする。

- `fb_scale = max(io.display_fb_scale)` を「backing scale（Retina 倍率）」として扱う。
- フォントアトラスの生成サイズを `atlas_px = base_px * fb_scale` にする。
- 表示上の倍率を `io.font_global_scale = 1 / fb_scale` にする。
- これで表示上の文字サイズは `base_px` のまま、Retina では `atlas_px` が上がるので滲みにくい。

### 2) `base_px` は外部モニタ基準で小さめに再調整する

- いま `24px` が外部で大きすぎるので、候補は `16px` or `18px`。
- まず 1) の「クッキリ化」を入れてから base を再調整する（クッキリになると小さめでも読める可能性が高い）。

## 実装チェックリスト

- [ ] 事前確認: `base_px` の暫定値を決める（候補: 16 / 18）。
- [ ] `ParameterGUI` に「直近の `fb_scale`」を保持するフィールドを追加する。
- [ ] `draw_frame()` の冒頭（`imgui.new_frame()` より前）で `fb_scale` を計算する。
- [ ] `fb_scale` が変化したときだけフォントを再生成する（`io.fonts.clear()` → `add_font_from_file_ttf(...)` → `refresh_font_texture()`）。
- [ ] `io.font_global_scale = 1 / fb_scale` を常に反映する（フォント再生成の有無に関係なく）。
- [ ] フォントファイルが無い環境でも動く（デフォルトフォント + `font_global_scale` だけでも破綻しない）。
- [ ] 手動確認: 内蔵 Retina / 外部モニタ / ウィンドウ移動（Retina↔ 外部）で「クッキリ」「過大にならない」を確認する。
- [ ] 微調整: `base_px` を確定し、定数名も “base” が伝わるものに変更する（例: `_GUI_FONT_SIZE_BASE_PX`）。

## 既知のトレードオフ

- モニタ移動直後など、`fb_scale` 変化タイミングでフォントテクスチャ更新が走り、軽いヒッチが起きる可能性がある（毎フレーム再生成はしない）。
- `display_fb_scale` は “Retina 倍率” の検知には効くが、「同じ 1.0 でも PPI が違う外部モニタ差」までは完全には吸収できない。
  - 必要なら将来案: 物理 DPI の取得、または GUI 内でのユーザー調整（スライダー/ショートカット）を追加する。

## 相談したい点（ユーザー確認）

- [ ] `base_px` は 16 と 18 のどちらで開始したい？（ひとまず 16 で進めて、必要なら上げるでも OK）；まずは 16 で
- [ ] 「外部基準でサイズ固定」で十分？ それとも「Retina では少しだけ大きく」も欲しい？；まずはシンプルに外部基準でサイズ固定で
