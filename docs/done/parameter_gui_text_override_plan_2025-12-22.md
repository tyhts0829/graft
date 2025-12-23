# どこで: `parameter_gui_text_override_plan_2025-12-22.md`

#

# 何を: Parameter GUI で text primitive の `text`（必要なら `font` / `text_align` も）を、

# コード側で明示指定していても描画中に GUI から上書きできるようにする実装改善計画。

#

# なぜ: `sketch/main.py` のように `G.text(text=..., font=...)` を明示すると、

# 現状は初期 `override=False` になり、かつ GUI に override トグルが出ないため

# `text` を GUI から変更しても描画へ反映できないため。

## ゴール

- `G.text(text="GRAFIX")` のように explicit 指定でも、Parameter GUI で `text` を変更して反映できる。
- override の意味を他パラメータと統一する（`override=True` → GUI 値、`override=False` → base 値）。
- （任意）`font` も override トグルで base ↔ GUI を切り替えられる。

## 非ゴール（今回やらない）

- GUI レイアウトの大幅変更（列構成の変更、別ウィンドウ追加など）。
- 文字列/choice の CC 駆動（CC→ 文字列/選択肢）や MIDI 学習 UI の拡張。
- フォント検索 UI の全面刷新（現状のフィルター + コンボを維持）。

## 現状整理（原因）

- `src/grafix/interactive/parameter_gui/rules.py`
  - `kind in {"bool","str","font","choice"}` は `show_override=False` かつ `cc_key="none"`。
- `src/grafix/interactive/parameter_gui/table.py`
  - Column 4 は `cc_key=="none"` で早期 return するため、override checkbox まで到達しない。
- `src/grafix/core/parameters/merge_ops.py`
  - explicit kwargs は初期 `override=False`（`initial_override = not explicit`）。
- `src/grafix/core/parameters/resolver.py`
  - `kind=="str"` は `override` に従うので、explicit の `text` は GUI 変更が効かない。
  - `kind=="font"` は例外で「常に GUI 値」を採用しており、override が意味を持たない。

## 方針（素直）

### 1) `str` の override トグルを表示できるようにする

- UI ルール側（`rules.py`）で `kind=="str"` を `show_override=True` にする。
- Column 4（`table.py`）で「CC 入力が無い行でも override checkbox は描画できる」ようにする。

### 2) `font` の override を “意味のある” ものにする（任意だが推奨）

トグルを出すだけだと挙動が変わらないため、resolver 側も他と統一する。

- `kind=="font"` も `override` に従って base/GUI を切り替える。

### 3) `choice`（例: `text_align`）も同様に扱うか決める（要確認）

`text` 以外でも explicit の choice は現状 GUI から変えられないため、必要ならここで一緒に直す。

- 追加案: `kind=="choice"` も `show_override=True`（CC 入力は引き続き無し）。

## 実装チェックリスト（コード変更は次ターンから）

- [x] 仕様確定（下の「事前確認」へ回答をもらう）
- [x] `src/grafix/interactive/parameter_gui/rules.py` を更新
  - [x] `kind=="str"` を `show_override=True`（`cc_key="none"` のまま）
  - [x] `kind=="font"` / `kind=="choice"` も `show_override=True`
- [x] `src/grafix/interactive/parameter_gui/table.py` を更新
  - [x] `_render_cc_cell()` の `cc_key=="none"` 早期 return を整理し、`show_override` が真なら checkbox を描く
  - [ ] 既存の CC 学習 UI（int/int3）とレイアウト崩れが無いことを確認
- [x] `src/grafix/interactive/parameter_gui/widgets.py` を更新
  - [x] kind=str を `imgui.input_text_multiline` に統一
- [x] `src/grafix/core/parameters/resolver.py` を更新（`font` のみ）
  - [x] `kind=="font"` を他と同様に `override` で base/GUI を切り替える
- [x] テスト更新
  - [x] `tests/interactive/parameter_gui/test_parameter_gui_table_rules.py` の期待値を更新（str/font/choice の show_override）
  - [x] `tests/core/parameters/test_resolver.py` に font の base/GUI 切替テストを追加
- [ ] 手動スモーク（任意だが推奨）
  - [ ] `sketch/main.py` を実行し、`text` 行の override を ON にして文字列変更が反映されることを確認
  - [ ] `font` の override ON/OFF で base/GUI が切り替わることを確認

## 検証コマンド案

- `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
- `ruff check .`
- `mypy src/grafix`

## 事前確認したいこと（Yes/No で OK）

1. `choice`（例: `text_align` や effect の mode 等）にも override トグルを出して良い？

   - Yes: kind=choice を全体で対応（一貫性重視）；こちらで
   - No: 今回は `text` primitive の `text`（必要なら `font`）だけに限定

2. `font` の解決を “他と同じ” に変えて良い？（`override=False` なら base 値を使う）

   - Yes: toggle が意味を持つよう resolver を変更；こちらで
   - No: UI だけ揃える（ただし toggle が実質無意味になる）

3. `text` 入力は単行 `input_text` のままで良い？（改行は `\\n` を打つ運用）
   - Yes: 最小実装で進める
   - No: multiline editor（行高の扱い）も今回入れる；こちらで。text 入力は imgui.input_text_multiline を使うように統一して。

回答（2025-12-22）
- 1) Yes（choice 全体で override 対応）
- 2) Yes（font も override で base/GUI 切替）
- 3) No（kind=str を `imgui.input_text_multiline` に統一）
