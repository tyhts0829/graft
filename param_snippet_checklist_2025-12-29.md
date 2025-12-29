# Parameter GUI Code（リテラル出力）チェックリスト（2025-12-29）

## ゴール

- Parameter GUI で調整した値を **Python リテラルとしてコピペ**できる。
- `@component` の有無に関わらず動く（Style / Primitive / Effect / Layer style / その他）。
- 出力は **純粋な Python コードのみ**（`#` コメント無し）で、貼り付け先の文脈に乗せやすいこと。
- 出力は「関数内に貼る」用途を優先し、**全行を 4 スペースでインデント**する。

## 非ゴール

- スケッチ `.py` の自動編集。
- “ユーザーの元コードを完全再構築” するコード生成（変数名や入力 Geometry までは推測しない）。
- 依存追加が必要なクリップボード実装（imgui 側に API が無ければ手コピーで良い）。
- 全グループ一括のスニペット出力（必要なら別機能として検討）。

## 仕様案（UX）

### UI（トリガと出力先）

- v1: 各グループヘッダに `Code` ボタンを置く（= そのグループ単体を出力）。
- 出力は **ファイル保存ではなくポップアップ**（modal）に表示する。
  - `input_text_multiline` の readonly に出し、**選択→コピー**で持ち帰れることを最優先。
  - ポップアップ表示直後にテキスト欄へ **フォーカス**を当て、`Ctrl/Cmd+A → Ctrl/Cmd+C` が即できるようにする。
  - 可能なら `Copy` ボタンも置く（clipboard API が使える場合はワンクリックでコピー）。
  - clipboard API が無い場合は「`Ctrl/Cmd+A → Ctrl/Cmd+C`」のヒントを添える。

### 出力単位

- v1: **グループ単位**（GUI のヘッダ単位）に `Code` を出せる。
  - Primitive: 1 呼び出し（= `(op, site_id)` グループ）
  - Effect: 1 チェーン（= `chain_id`）
  - Style: global（= `__style__/__global__`）
  - Layer style: 1 layer（= `__layer_style__/site_id`）
  - Other: `(op, site_id)` 単位

### 値のソース（どれを“焼く”か）

- デフォルト: `effective`（GUI/CC/base を統合した “いまの実効値”）
  - 実装上は `store._runtime_ref().last_effective_by_key` を優先
  - 無い場合は `row.ui_value` を fallback
- 代替（必要なら）:
  - `override-only`（`override=True` の行だけ ui_value を出す）
  - `ui_value-all`（全行 ui_value を出す）

### 出力フォーマット

- 原則「貼れる」こと優先で、**kwargs / dict** を基本にする。
- ただし effect はチェーンなので `E.op(...).op(...)` の“ビルダ式”を併記すると便利。

#### Style（global）

- store は RGB255 を持つが、ユーザー API（`run`）は RGB01 を期待するため変換する。
- 出力例:

```py
    dict(
        background_color=(1.0, 1.0, 1.0),
        line_thickness=0.001,
        line_color=(0.0, 0.0, 0.0),
    )
```

#### Layer style

- `__layer_style__` の `line_color/line_thickness` を `L(..., color=..., thickness=...)` に寄せて出す。
- RGB255 → RGB01 変換。
- 出力例:

```py
    dict(color=(0.0, 0.0, 0.0), thickness=0.002)
```

#### Primitive

- 出力例（call か kwargs のどちらか）:

```py
    G.circle(r=12.5, center=(50.0, 50.0, 0.0))
```

#### Effect chain

- 入力 Geometry は不明なので “ビルダ式”として出す（末尾の `(g)` は付けない）。
- 出力例:

```py
    E.affine(
        delta=(0.0, 0.0, 0.0),
    ).dash(
        dash_length=(16.0, 4.0),
    ).buffer().fill()
```

## 実装チェックリスト

### A. 仕様確定（最初に決める）

- [x] デフォルトの値ソースを確定（推奨: `effective`）
- [x] `Code` の UI 配置を確定（推奨: 各グループヘッダにボタン）
- [x] 出力形式を確定（v1: `dict(...)` を基本、primitive/effect は `G./E.` 併記）

### B. Code 生成（純粋関数）

- [x] `src/grafix/interactive/parameter_gui/snippet.py` を追加
- [ ] 入力:
  - [x] `blocks: Sequence[GroupBlock]`（GUI 表示順）
  - [x] `step_info_by_site`（effect チェーン用、任意）
  - [x] `layer_style_name_by_site_id`（layer 名用、任意）
  - [x] `last_effective_by_key: dict[ParameterKey, object]`（任意）
- [ ] 出力:
  - [x] “グループ単位 code” を生成する API（`GroupBlock -> str`）を用意
  - [x] RGB255 → RGB01 変換（Style / Layer style）
  - [x] Python リテラル化（`repr()` 基本、tuple/float/int/str/bool）

### C. UI（Parameter GUI）

- [x] `render_parameter_table(...)` に `last_effective_by_key` を渡せるようにする
- [x] 各グループヘッダに `Code` ボタンを追加する
- [x] ポップアップ（modal）で code を表示する
  - [x] `input_text_multiline` で全文表示（readonly）
  - [x] 表示直後にテキスト欄へフォーカス（`Ctrl/Cmd+A → Ctrl/Cmd+C` を即実行できる）
  - [x] `Copy` ボタンでクリップボードへ送る（imgui の clipboard API を使用）
  - [x] コピー手順ヒントを表示する

### D. テスト

- [x] `snippet.py` を unit test で担保（GUI 依存は持たせない）
- [x] Style/layer の RGB 変換が正しいこと
- [x] Effect chain のステップ順が `step_index` に従うこと
- [x] component が display_op（関数名）で出ること

## 事前に確認したいこと（返答ください）

- 1. Code のデフォルト値は `effective` で OK？（CC 割当中でも “その瞬間の値” を焼く）；はい
- 2. 出力形式は v1 ではこれで OK？；はい
  - Style/Layer: `dict(...)`
  - Primitive: `G.op(...)`
  - Effect: `E.op(...).op(...)`（ビルダ式）
- 3. UI はポップアップ表示（手コピー）をまず作って OK？（クリップボードは“あれば”対応）；はい（readonly + フォーカス + Copy ボタンがあれば入れる）
