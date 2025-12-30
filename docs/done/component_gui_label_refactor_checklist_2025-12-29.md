# component GUI ラベル/分類の筋を良くする改善計画（2025-12-29）

## 背景

現状の Parameter GUI は、component を「`op` が `component.` で始まる」という文字列規約で特別扱いしている。
（例: 行ラベルの `component.` 除去、component 判定の prefix チェック）

この実装は小さく動く一方で、将来の拡張に対して筋が悪い（ルールが散在・`op=` を変えると壊れる・並び順/未知引数の扱いが primitive/effect と非対称）。

## 目的（ゴール）

- GUI が `component.` という文字列規約に依存しない（表示名・分類・並び替え）。
- `op=` をカスタムしても “component として” 正しく扱える。
- component の引数順が「シグネチャ順（meta 対象のみ）」で安定する（現状はアルファベット順）。
- 未知/旧引数（過去の永続化で残ったキー）を primitive/effect と同様に無視できる。

## 非ゴール

- snippet 出力機能（別計画）。
- UI の大幅変更（色やレイアウト刷新など）。必要なら後で追加。

## 方針（決定）

- Core に `component_registry` を導入し、`@component` が「component op の情報」を登録する。
  - registry に寄せて GUI 側の “推測” をやめる（prefix 判定を廃止）。
- GUI 側は `component_registry` の membership を見て分類する。
- 表示用の `op` 名（例: `logo`）も registry 側で定義し、行ラベルはそれを参照する。
  - ここでは「row label は関数名ベース（= display_op）」を維持し、`name=` はヘッダだけに効く（現在の設計を踏襲）。
- component は GUI 上で primitive と別カテゴリとして扱い、ヘッダ色も別にする（色は暫定）。

## 受け入れ条件（Definition of Done）

- `src/grafix/interactive/parameter_gui/` から `startswith("component.")` が消える。
- component の分類が registry ベースになる（`op` の prefix に依存しない）。
- component の引数順が signature/meta 順で安定する。
- component でも未知引数が GUI に出ず、warning が 1 回だけ出る（primitive/effect と同等）。
- component のヘッダ色が primitive と異なる（暫定色で OK、後で調整可能）。
- 既存テストが通り、component 関連のテストが必要分更新される。

## チェックリスト

- [x] 1. `src/grafix/core/component_registry.py` を追加する
  - [x] `ComponentRegistry`（`__contains__`, `get_meta`, `get_param_order`, `get_display_op` など最小限）
  - [x] グローバル `component_registry` を提供する
- [x] 2. `src/grafix/api/component.py` で decorator 作成時に registry 登録する
  - [x] `op`（ストアキー）と `display_op`（表示用: 関数名）を登録
  - [x] `meta`（公開引数）を登録
  - [x] `param_order`（シグネチャ順）を登録
  - [x] `overwrite=True` 前提で再登録可能にする（リロード耐性）
- [x] 3. GUI 側の component 判定を registry ベースへ寄せる
  - [x] `src/grafix/interactive/parameter_gui/store_bridge.py` から `_COMPONENT_OP_PREFIX` / `_is_component_op` を削除
  - [x] `row.op in component_registry` で component_rows を分類
  - [x] `primitive_header_display_names_from_snapshot()` の `is_primitive_op` を「primitive + component」に変更（prefix 依存を撤去）
  - [x] component ブロック内の arg 並びを `component_registry.get_param_order()` ベースへ変更（fallback はアルファベット）
  - [x] `rows_before_raw` の未知引数フィルタに component 分岐を追加（known_args は registry.meta から）
- [x] 4. 行ラベルの `component.` 除去を registry 参照へ置換する
  - [x] `src/grafix/interactive/parameter_gui/labeling.py` の prefix strip を削除
  - [x] `display_op` を引ける経路を決める
    - [ ] 案 A: labeling が `component_registry` を参照（最小変更）
    - [x] 案 B: grouping/table が “表示 op” を渡す（依存方向をより明確に）
- [x] 5. component を独自カテゴリとして扱い、ヘッダ色を分離する
  - [x] `src/grafix/interactive/parameter_gui/grouping.py` で component 行は `group_id=("component", (op, ordinal))` にする
  - [x] `src/grafix/interactive/parameter_gui/table.py` に `GROUP_HEADER_BASE_COLORS_RGBA["component"]` を追加する（暫定色）
  - [x] `src/grafix/interactive/parameter_gui/table.py` の `_header_kind_for_group_id()` に component を追加する
  - [x] 折りたたみキーの衝突を避ける（`primitive:` と `component:` を分ける等）
- [x] 6. テストを更新/追加する（差分が出る箇所だけ）
  - [x] `tests/interactive/parameter_gui/test_parameter_gui_labeling_phase1.py`（prefix strip 前提を修正）
  - [x] component の arg 並び順テストを追加（signature/meta 順が効くこと）
  - [x] component の未知引数フィルタ（旧永続化キーが GUI に出ない）テストを追加
- [x] 7. 検証
  - [x] `PYTHONPATH=src pytest -q tests/api/test_component.py`
  - [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
  - [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`

## 事前確認（方針のすり合わせ）

- [x] Q1. component は当面 “primitive と同カテゴリ/同色” のままで良い？（group_type を増やさない）；いいえ（component 専用色を入れる・暫定色で OK）
- [x] Q2. row label は「関数名ベース（display_op）」で固定し、`name=` はヘッダだけに効く、で継続して良い？；はい
