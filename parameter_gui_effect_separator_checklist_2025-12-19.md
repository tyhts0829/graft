# Parameter GUI: effect ごとの separator_text 導入チェックリスト（2025-12-19）

対象: `src/grafix/interactive/parameter_gui/`

## 目的

- effect のパラメータ行で **effect 名が毎行繰り返されるノイズ**を消す。
- effect 種類（= step）ごとに `imgui.separator_text()` で見出しを入れ、視線移動コストを下げる。
- 既存の grouping / labeling の「純粋関数 + unit test」方針を崩さない（描画ロジックを肥大化させない）。

## 前提（現状）

- effect 行の表示ラベルは `format_param_row_label()` で `"{op}#{ordinal} {arg}"`（例: `scale#1 auto_center`）。
- GUI は
  - チェーン単位: `collapsing_header`（例: `xf`, `effect#1`）
  - チェーン内: 4 列 table（label / control / min-max / cc）
  で縦に並ぶ。

## 目標 UI（イメージ）

チェーン（例: `xf`）の中で、step ごとに見出しを挿入してから、その step のパラメータ行を並べる。

- `separator_text("scale#1")`（または `"scale"`）
  - label: `auto_center`
  - label: `s`
- `separator_text("rotate#1")`（または `"rotate"`）
  - label: `deg`
- `separator_text("scale#2")`（または `"scale"`)
  - label: `auto_center`
  - ...

## 0) 事前に決める（あなたの確認が必要）

- [ ] `separator_text()` に表示する “effect 名” の仕様
  - 候補 A: `op` のみ（例: `scale`）
  - 候補 B: `op#N`（例: `scale#1` / `scale#2`）
  - 判断軸: 同一チェーン内で同じ op が複数回出るケース（`scale().rotate().scale()`）の見分けやすさ
- [ ] `separator_text()` を table 内で描く方式の許容
  - 案 A: table の「区切り行」を追加し、label 列で `separator_text()` を描く（最小変更）
  - 案 B: 4 列すべてで line を繋ぐ（label 列は `separator_text`、他列は `separator`）
  - 案 C: step ごとに table を分割し、table の外で `separator_text()`（見た目は良いが構造変更が大きい）

## 実装 TODO（チェックリスト）

### 1) 表示ラベルから effect 名を除去（見出しへ移動）

- [ ] `grouping.group_info_for_row()` の effect 行 `visible_label` を `row.arg` に変更
  - 影響: `tests/interactive/parameter_gui/test_parameter_gui_table_grouping.py` 等の期待値更新
- [ ] 既存の Primitive/Style の表示ラベルは変更しない（今回のスコープ外）

### 2) step 境界（見出し挿入位置）を “純粋に” 決定できるようにする

狙い: `table.py` に「effect の状態推測ロジック」を増やさない。

- [ ] `GroupBlockItem` に `separator_text: str | None` を追加（見出しが必要な行だけ埋める）
- [ ] `group_blocks_from_rows()` で effect_chain ブロック内の step 変化を検出し、先頭行に `separator_text` を付与
  - step 判定キー: `(row.op, row.site_id)`（同一 step 内は arg だけが変わる前提）
  - 表示文字列: 0) の決定（A/B）に従い `op` or `op#N` を生成
    - `N` は `effect_step_ordinal_by_site[(op, site_id)]` を使用（存在しない場合のフォールバックも決める）

### 3) 描画で `separator_text()` を挿入

- [ ] `table.render_parameter_table()` の effect_chain ブロック描画で、`separator_text` がある行の前に “区切り行” を描く
  - [ ] table row を 1 行進め、label 列で `imgui.separator_text(text)` を呼ぶ
  - [ ] 0) の決定に従い、他列にも line を描く/描かないを揃える
  - [ ] 追加描画は `rows_after` の 1:1 対応を壊さない（描画だけ増やし、row モデルは増やさない）

### 4) テスト更新（表示ラベル/グルーピングの後方互換は不要）

- [ ] `tests/interactive/parameter_gui/test_parameter_gui_table_grouping.py`
  - [ ] effect 行の `visible_label` が `arg` になる期待値へ更新
- [ ] `tests/interactive/parameter_gui/test_parameter_gui_group_blocks.py`
  - [ ] effect 行の `visible_label` 更新
  - [ ] `separator_text` が step の先頭行だけに入ることを追加で検証
- [ ] 追加テスト（必要なら）
  - [ ] `scale().rotate().scale()` で `scale#1` / `rotate#1` / `scale#2` の順に見出しが付く（候補 B の場合）

### 5) 検証（対象限定で実行）

- [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_table_grouping.py`
- [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_parameter_gui_group_blocks.py`
- [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`（時間が許せば）
- [ ] `mypy src/grafix/interactive/parameter_gui`
- [ ] `ruff check src/grafix/interactive/parameter_gui tests/interactive/parameter_gui`

## 受け入れ条件（Done の定義）

- effect_chain（例: `xf`）の中で、step ごとに `separator_text()` の見出しが出る。
- effect の各パラメータ行の label 列から effect 名が消え、パラメータ名（arg）のみになる。
- unit test が更新され、`PYTHONPATH=src pytest -q tests/interactive/parameter_gui` が通る。

## 事前確認したいこと（追加）

- 使っている pyimgui のバージョンで `separator_text()` が存在するか（無い場合のフォールバック方針を決めたい）。
