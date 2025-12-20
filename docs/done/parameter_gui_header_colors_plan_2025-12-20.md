# Parameter GUI: ヘッダ色を種類（Style / Primitive / Effect）ごとに変える計画（2025-12-20）

## 目的

- Parameter GUI（`src/grafix/interactive/parameter_gui/`）のグループヘッダ（折りたたみヘッダ）の視認性を上げる。
- どのセクションのパラメータか（Style / Primitive / Effect）を一目で判別できるようにする。

## 対象範囲（今回）

- 対象 UI: `imgui.collapsing_header(...)` で描画している「グループヘッダ行」だけ。
  - Style ヘッダ（`header="Style"`）
  - Primitive ヘッダ（例: `polygon`, `circle` など）
  - Effect チェーンヘッダ（例: `effect#1`, `xf` など）
- 行（テーブル）の配色・ウィジェットの配色・背景色は変更しない。

## 仕様（UI/UX）

- ヘッダ背景色を種類ごとに変える（hover / active も同系色で差分を付ける）。
- 種類判定は「グルーピング結果」に従う。
  - `GroupBlock.group_id[0]` が `"style"` / `"primitive"` / `"effect_chain"` のどれかで分岐する想定。
- 色は “濃すぎず・派手すぎず” を優先（ダークテーマ前提で、識別できる程度の色差）。

## 実装方針（最小・シンプル）

- 実装位置: `src/grafix/interactive/parameter_gui/table.py`
  - `for block in blocks:` 内で `imgui.collapsing_header(...)` を呼ぶ直前に `push_style_color` を行い、直後に `pop_style_color` する。
  - `imgui.COLOR_HEADER`, `imgui.COLOR_HEADER_HOVERED`, `imgui.COLOR_HEADER_ACTIVE` を種類ごとの RGBA へ差し替える。
- 色定義は「後から調整しやすい」形で 1 箇所にまとめる。
  - 例: `GROUP_HEADER_COLORS: dict[str, tuple[rgba, rgba, rgba]]` のような定数 or 小さな純粋関数。
- hover / active はベース色から自動導出する（例: 白方向へ補間 + alpha を少し増やす）。
- 例外や早期 continue でも `pop_style_color` が漏れないように、ヘッダ描画部分だけ小さく `try/finally` で囲う。
  - ただし、過剰な抽象化（コンテキストマネージャ化等）はしない。

## 実装チェックリスト

### P0: 仕様決め（先に合意する）

- [x] 3 色（Style / Primitive / Effect）のベース色を決める（RGBA の具体値）
  - 実装: `src/grafix/interactive/parameter_gui/table.py` の `GROUP_HEADER_BASE_COLORS_RGBA`
- [x] テキスト色も変えるか決める（基本は背景だけ、必要なら `imgui.COLOR_TEXT` をヘッダ描画の間だけ変更）
- [x] `effect_chain` を “Effect” と見なす扱いで OK か確認する

### P1: 実装（Parameter GUI）

- [x] `table.py` に「種類 → ヘッダ色」マッピング（定数 or 純粋関数）を追加する
- [x] `imgui.collapsing_header` の前後に `push_style_color` / `pop_style_color` を追加する
- [ ] Style / Primitive / Effect それぞれで色が切り替わることを確認する（目視）

### P2: テスト（壊れにくい所だけ）

- [ ] （任意）「種類 → 色」マッピングの単体テストを追加する（imgui 非依存）
  - 例: `tests/interactive/parameter_gui/test_parameter_gui_header_colors.py`

### P3: 検証（変更後に回す）

- [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
- [ ] `ruff check src/grafix/interactive/parameter_gui/table.py`（この環境では `ruff` コマンドが無い）
- [ ] 目視確認: `python sketch/main.py`（`parameter_gui=True`）で Style / Primitive / Effect のヘッダ色が分かれている

## Done の定義（受け入れ条件）

- [ ] Style / Primitive / Effect のヘッダ背景色が明確に異なる
- [ ] hover / active の見た目が破綻しない（押下時に読める）
- [x] 既存の Parameter GUI テストが通る（少なくとも `tests/interactive/parameter_gui`）

## 事前確認したいこと（あなたに質問）

- [x] それぞれの色味の希望（例: Style=青系 / Primitive=緑系 / Effect=紫 or オレンジ系 など）はある？；ない。でも、種類と色の定数辞書みたいなのをモジュール先頭の見やすいところに定義しておいてほしい。あとから調整するから。hover, active とかの反応色は、それらから自動で定まるようにしておいて。
- [x] 「Style（global + layer_style）」は同じ色でまとめて OK？；はい
