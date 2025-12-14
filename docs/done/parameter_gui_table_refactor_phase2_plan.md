# どこで: `docs/parameter_gui_table_refactor_phase2_plan.md`
#
# 何を: `src/app/parameter_gui/table.py` の可読性/変更容易性を上げるための Phase 2 リファクタ計画（列描画の分割 + グルーピング判定の純粋化）。
#
# なぜ: ルール決定（`rules.py`）は集約できたが、`table.py` 側は依然として “描画フロー” と “グルーピング判定” が絡んで長く、見通しが悪いため。

## ゴール

- `render_parameter_row_4cols()` が一直線に読める（列ごとの詳細は別関数に閉じる）。
- `render_parameter_table()` の「この row はどのグループか / ヘッダ名は何か」を純粋関数に切り出し、分岐の見通しを上げる。
- 既存の見た目/挙動は変えない（設計変更はしない）。

## 非ゴール

- GUI の列構成や UI 要素の刷新。
- `widgets.py` の dispatch 方式変更（必要なら別タスク）。
- imgui 依存コードを完全に排除する（描画は当然 imgui 依存のまま）。

## 前提（現状）

- UI ルールは `src/app/parameter_gui/rules.py` の `ui_rules_for_row()` に集約済み。
- `table.py` の if の多くは「imgui 関数が違うので呼び分けが必要」という描画フロー由来。
- もう一つの if の塊は `render_parameter_table()` 内のグルーピング（Style/Primitive/Effect）判定。

## 方針

### A) 列描画を関数分割（素直で効果大）

`render_parameter_row_4cols()` を “列ごとの関数” に分割し、メイン関数を短くする。

例（イメージ）:

- `_render_label_cell(imgui, row, *, row_label: str) -> None`
- `_render_control_cell(imgui, row) -> tuple[changed, ui_value]`
- `_render_minmax_cell(imgui, row, rules, *, ui_min, ui_max) -> tuple[changed, ui_min, ui_max]`
- `_render_cc_cell(imgui, row, rules, *, cc_key, override) -> tuple[changed, cc_key, override]`

`render_parameter_row_4cols()` は「ローカル変数初期化→各セル関数呼び出し→ParameterRow 再構築」だけにする。

### B) グルーピング判定を純粋関数へ切り出す（テストしやすさも上がる）

`render_parameter_table()` 内の判定を “GroupInfo 生成” に分離する。

例（イメージ）:

- `@dataclass(frozen=True) class GroupInfo:`
  - `group_id: tuple[str, object]`
  - `header_id: str`
  - `header: str | None`
  - `visible_label: str`
  - `step_info: tuple[str, int] | None`（必要なら）
- `group_info_for_row(row, *, primitive_header_by_group, layer_style_name_by_site_id, effect_chain_header_by_id, step_info_by_site, effect_step_ordinal_by_site) -> GroupInfo`

`render_parameter_table()` は
- `info = group_info_for_row(...)`
- `group境界なら collapsing_header`
- `row を描画`
のループだけにする。

## 実装チェックリスト

- [x] 1) 列描画の関数分割
  - [x] `table.py` に列描画ヘルパ関数を追加する（private）
  - [x] `render_parameter_row_4cols()` をヘルパ呼び出し中心に書き換える
  - [x] `ui_rules_for_row()` の呼び出しは 1 回だけにする（現状維持）
- [x] 2) グルーピング判定の純粋化
  - [x] 新規: `src/app/parameter_gui/grouping.py`（または `table_grouping.py`）を追加する
  - [x] `group_info_for_row()` を実装する（imgui import 無し）
  - [x] `table.py` は `group_info_for_row()` の返り値を使うだけにする
- [x] 3) unit test（最小）
  - [x] 新規: `tests/app/test_parameter_gui_table_grouping.py`
  - [x] Style row / Layer style row / Primitive row / Effect row で `group_info_for_row()` の結果を検証する
  - [x] Effect のチェーン内採番（`scale#1` 等）が visible_label に反映されることを検証する
- [x] 4) 仕上げ
  - [x] `pytest -q` を通す

## 追加で確認したいこと

- `render_parameter_table()` の “Style グループ” は「常に 1 つ」固定のままで良い？
  - 現状: Style（global + layer style）を 1 collapsing header にまとめている
