# どこで: `docs/parameter_gui_table_rules_refactor_plan.md`
#
# 何を: `src/app/parameter_gui/table.py` に散っている「kind / op / arg に応じた UI ルール（min-max, cc, override など）」を 1 箇所に集約するためのリファクタ計画。
#
# なぜ: 例外条件（例: `global_thickness` / layer の `line_thickness` は min-max 無効、`rgb` は cc_key が int3 など）が分散していて、変更時に漏れ/重複が起きやすいため。

## ゴール

- `render_parameter_row_4cols()` から分岐（if/elif）を大幅に減らし、UI ルールを “単一の真実” に寄せる。
- ルールは純粋関数として切り出し、pyimgui を import しない unit test で担保できる状態にする。
- 見た目/挙動は（意図した差分を除き）変えない。

## 非ゴール（今回やらない）

- GUI レイアウトの大幅変更（列構成の変更、widget の置き換え等）。
- `widgets.py` の slider 表示（thickness の `%.6f` など）まで含めた全面統一（必要なら Phase 2 として別計画）。

## 現状の “ルール” の棚卸し（抜粋）

`src/app/parameter_gui/table.py` で分岐している主な観点:

- Column 3（min-max）
  - `kind in {"float","vec3"}` → `drag_float_range2`
  - `kind == "int"` → `drag_int_range2`
  - 例外: `STYLE_OP/global_thickness` は min-max 無効
  - 例外: `LAYER_STYLE_OP/line_thickness` は min-max 無効
- Column 4（cc + override）
  - `kind in {"bool","string","choice"}` → 何も出さない（cc/override なし）
  - `kind in {"vec3","rgb"}` → `input_int3` + override checkbox
  - その他（float/int など） → `input_int` + override checkbox

## 方針（素直）

### ルール集約モジュールを新設する

- 新規: `src/app/parameter_gui/rules.py`
- `ParameterRow` を入力に取り、UI の “描画方針” を返す純粋関数に寄せる。

例（イメージ）:

- `RowUiRules`
  - `minmax: Literal["none","float_range","int_range"]`
  - `cc_key: Literal["none","int","int3"]`
  - `show_override: bool`
  - （必要なら）`disable_minmax_reason: str | None`（デバッグ/説明用。無くても良い）
- `ui_rules_for_row(row: ParameterRow) -> RowUiRules`

### `table.py` は rules を “適用するだけ” にする

- Column 3/4 は `rules = ui_rules_for_row(row)` を見て描画を分岐する。
- `STYLE_OP/global_thickness` や `LAYER_STYLE_OP/line_thickness` の例外は `rules.py` に移す（`table.py` からは消す）。

## 実装チェックリスト

- [ ] 1) ルールの仕様を確定する（現状の挙動を “そのまま” とみなす範囲を明文化）
  - [ ] `STYLE_OP/global_thickness` の min-max 無効は維持
  - [ ] `LAYER_STYLE_OP/line_thickness` の min-max 無効は維持
  - [ ] `bool/string/choice` の cc/override 非表示は維持
  - [ ] `vec3/rgb` の cc_key は int3 + override は維持
- [ ] 2) `src/app/parameter_gui/rules.py` を追加する
  - [ ] `RowUiRules`（dataclass か NamedTuple）を定義する
  - [ ] `ui_rules_for_row(row)` を実装する
  - [ ] `STYLE_OP` / `LAYER_STYLE_OP` など必要な識別子はここで参照する
- [ ] 3) `src/app/parameter_gui/table.py` をリファクタする（挙動維持）
  - [ ] Column 3 の分岐を rules ベースに置換する
  - [ ] Column 4 の分岐を rules ベースに置換する
  - [ ] “例外 if” を `table.py` から削除し、`rules.py` に集約する
- [ ] 4) unit test を追加する（imgui 非依存）
  - [ ] 新規: `tests/app/test_parameter_gui_table_rules.py`
  - [ ] 代表行（float/int/vec3/rgb/bool/string/choice）で `ui_rules_for_row` の返り値を検証する
  - [ ] 例外ケース（`STYLE_OP/global_thickness`, `LAYER_STYLE_OP/line_thickness`）を検証する
- [ ] 5) 仕上げ
  - [ ] `pytest -q` を通す
  - [ ] 可能なら `python main.py` で手動確認（スクロール/列表示/override 動作）

## 追加で事前確認したいこと（分岐が出るポイント）

- min-max を無効化するのは「`arg.endswith("thickness")` 全般」ではなく、現状どおり “特定キーだけ” にする想定で良い？
  - 現状: global_thickness と layer line_thickness だけ無効（primitive の thickness 系は有効）
- `rgb` の cc_key 表示（int3）を維持して良い？（将来は色を CC で駆動したい前提か）

