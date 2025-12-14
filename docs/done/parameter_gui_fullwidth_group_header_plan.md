# どこで: `docs/parameter_gui_fullwidth_group_header_plan.md`
#
# 何を: `collapsing_header` を “テーブルの 1 列目セル内” ではなく “テーブルの外” に出し、
#       グループ（Style / Primitive / Effect chain）ごとにテーブルを分割して「ヘッダが全幅に見える」表示へ移行する実装計画。
#
# なぜ: 行背景色で見た目だけ全幅化する案は、ヘッダの色味/見た目が `collapsing_header` 本体と一致しづらく、意図どおりにならないため。
#       クリック領域も含めて素直に “全幅のヘッダ” を得るには、ヘッダをテーブル外に出すのが最短で確実。

## ゴール

- グループ見出し（`collapsing_header`）がウィンドウ全幅に表示される（見た目とクリック領域が全幅）。
- グループを閉じると、その配下の行（テーブル）が描画されない。
- `store_bridge` の差分適用要件（`rows_before` と `rows_after` の長さ一致）を維持する。

## 非ゴール

- 列構成（label/control/min-max/cc）の変更。
- 既存の “行ラベル” 規約（例: `polygon#1 n_sides`, `scale#1 auto_center`）の変更。
- グルーピング/採番ロジックの刷新（既存の `group_info_for_row()` 等を使う）。

## 方針（案3: テーブル分割）

### 基本アイデア

- いま: 1 つの `begin_table()` の中で、グループ切替時に “ヘッダ行（1列目に collapsing_header）” を差し込む。
- これから: グループごとに
  1) `imgui.collapsing_header(...)` を **テーブル外**で描く（= 全幅）
  2) open のときだけ `begin_table()` を作り、当該グループの行を描く

### 実装上のポイント

- **ID 衝突回避**
  - 同一ウィンドウ内で複数 `begin_table("##parameters", ...)` を呼ぶと衝突し得る。
  - 対策: グループごとに `imgui.push_id(info.header_id)` したスコープ内で `begin_table("##parameters", ...)` を呼ぶ（またはテーブル ID に group_id を混ぜる）。
- **列幅の一貫性**
  - 全テーブルで `TABLE_SIZING_STRETCH_PROP` と同じ `column_weights` を使う（見た目を揃える）。
- **差分適用の要件**
  - グループが閉じている場合も、配下の `ParameterRow` は `updated_rows` に “変更なし” で必ず追加する。

## 実装チェックリスト

- [x] 1) “グループ単位のブロック列” を作れるようにする（純粋関数）
  - [x] 新規（候補）: `src/app/parameter_gui/group_blocks.py`
    - `group_blocks_from_rows(rows, *, primitive_header_by_group, layer_style_name_by_site_id, effect_chain_header_by_id, step_info_by_site, effect_step_ordinal_by_site) -> list[GroupBlock]`
    - `GroupBlock` には `header/header_id/group_id/items`（items は `ParameterRow` と `visible_label`）を入れる
  - [x] `group_info_for_row()` を内部利用し、境界判定（`group_id` の変化）でブロック化する
  - [x] 既存テストとの整合性を優先し、imgui import はしない（純粋）

- [x] 2) `render_parameter_table()` を “ブロック描画” に置き換える
  - [x] 対象: `src/app/parameter_gui/table.py`
  - [x] ループ構造を変更する
    - [x] for block in blocks:
      - [x] `imgui.push_id(block.header_id)`（衝突回避）
      - [x] `group_open = collapsing_header(...)`（block.header がある場合）
      - [x] open のときだけ `begin_table()` → `table_setup_column()` → `table_headers_row()` → 行描画
      - [x] closed のときは行描画しないが、`updated_rows` には元の row を積む
      - [x] `imgui.pop_id()`
  - [x] “ヘッダ行をテーブルへ差し込む” 現行コードは削除する

- [x] 3) 表示の細部（最小）
  - [x] `collapsing_header` の既定 open は現状どおり `TREE_NODE_DEFAULT_OPEN`
  - [x] ヘッダとテーブルの間隔は最小（必要なら `imgui.spacing()` を 1 回）
  - [x] 列ヘッダ（label/control/min-max/cc）は最初の 1 回だけ表示する（ノイズ削減）
    - 実装: 最初に開いたグループのテーブルで 1 回だけ `table_headers_row()` を描画する

- [x] 4) テスト（最小）
  - [x] 追加: ブロック化関数のユニットテスト
    - [x] 同一 `group_id` が連続する場合は 1 ブロックになる
    - [x] `group_id` が変わるとブロックが分割される
    - [x] `visible_label` が各 row に紐づいて保持される
  - [x] `pytest -q` を通す

## 完了条件（受け入れ）

- Style / Primitive / Effect chain の見出しが “全幅の collapsing_header” として表示される。
- 各グループの配下行は 4 列テーブルとして表示され、グループを閉じると消える。
- GUI 操作で値を変えたとき、従来どおり store へ差分反映できる（`rows_after` の長さが崩れない）。

## 追加で確認したいこと（実装後のメモ）

- 列ヘッダは 1 回だけにしたため、全グループを閉じている間は列名が表示されない。
  - 必要なら「列ヘッダだけ固定表示（別 child/window など）」を別タスクで検討する。
