# どこで: `docs/memo/parammeta_kind_str_unify_checklist.md`。
# 何を: `ParamMeta.kind` の `"string"` 表記を廃止し、`"str"` 表記へ統一するための変更箇所チェックリストをまとめる。
# なぜ: kind 名が揺れると GUI ディスパッチ/正規化/テストが分散して壊れやすいため（仕様語彙を 1 つに固定したい）。

# `ParamMeta.kind` を `"str"` に統一する変更箇所（洗い出し）

## 目標

- `ParamMeta.kind` の「文字列型」を **`"string"` ではなく `"str"`** に統一する（コメント/実装/テスト含む）。

## 前提（現状の検出結果）

- `src/parameters/meta.py` が `"string"` を生成する（`infer_meta_from_value()`）。
- GUI/正規化/ルールは `"string"` を前提に分岐している箇所がある。
- `docs/` の一部（done/memo）にも `kind=string` の言及が残っている。

## 変更対象（必須：コードとして意味がある箇所）

### `src/`（実装）

- `src/parameters/meta.py:18`  
  - `ParamMeta.kind` のコメント列挙を `"str"` に変更（現状 `"string"`）。
- `src/parameters/meta.py:36,43,51`  
  - `infer_meta_from_value()` の戻り値 `ParamMeta(kind="string")` を `ParamMeta(kind="str")` に変更。
- `src/parameters/view.py:97`  
  - `normalize_input()` の `if kind == "string":` を `if kind == "str":` に変更。
- `src/parameters/view.py:104`  
  - コメント `choice は string として扱い...` を `choice は str として扱い...` に変更。
- `src/app/parameter_gui/rules.py:51`  
  - `if row.kind in {"bool", "string", "choice"}:` を `{"bool", "str", "choice"}` に変更。
- `src/app/parameter_gui/widgets.py:159`  
  - docstring の `kind=string` を `kind=str` に変更。
- `src/app/parameter_gui/widgets.py:202`  
  - `_KIND_TO_WIDGET` のキー `"string"` を `"str"` に変更（`widget_string_input` のままでも可）。

### `tests/`（テストが落ちる/意味が変わる箇所）

- `tests/app/test_parameter_gui_table_rules.py:37-38`  
  - テストデータ `kind="string"` を `kind="str"` に変更。
- `tests/manual/test_parameter_gui_string_input.py:4,22`  
  - docstring の `kind=string` を `kind=str` に変更。
  - テストデータ `kind="string"` を `kind="str"` に変更。
- `tests/manual/test_parameter_gui_multirow.py:3,51`  
  - docstring の `... / string / ...` を `... / str / ...` に変更（表記統一）。
  - テストデータ `kind="string"` を `kind="str"` に変更。

## 変更対象（任意：ドキュメント/メモの表記）

※ 実装とは独立だが、repo 内の語彙統一のため更新候補。

- `docs/memo/src_code_review_2025-12-14.md:95-96`（今回指摘の引用元）
- `docs/done/parameter_gui_phase3_table_3cols_checklist.md:9,13,14,29,36`
- `docs/done/parameter_gui_impl_plan.md:30,34,50`
- `docs/done/parameter_gui_phase3_style_impl_plan.md:17`
- `docs/done/parameter_gui_phase2_checklist.md:23`
- `docs/done/parameter_gui_table_rules_refactor_plan.md:28,41,70,82`
- `docs/done/parameter_gui_phase3_int_vec3_checklist.md:25`
- `docs/done/parameter_gui_phase3_checklist.md:28,35,88`

## 実装チェックリスト（このファイルは “洗い出し” 用）

- [ ] `src/parameters/meta.py` の kind 列挙コメントを `"str"` に統一する
- [ ] `src/parameters/meta.py` の `infer_meta_from_value()` が返す kind を `"str"` に統一する
- [ ] `src/parameters/view.py` の `"string"` 分岐を `"str"` に置換する（コメント含む）
- [ ] `src/app/parameter_gui/*` の `"string"` ディスパッチを `"str"` に置換する（rules/widgets）
- [ ] `tests/` の `kind="string"` を `"str"` に置換する（manual/app）
- [ ] （任意）`docs/` の `kind=string` 表記を `"str"` に置換する

