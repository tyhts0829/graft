# Parameter GUI Snippet（リテラル出力）チェックリスト（2025-12-29）

## ゴール

- Parameter GUI で調整した値を **Python リテラルとしてコピペ**できる。
- `@component` の有無に関わらず動く（Style / Primitive / Effect / Layer style / その他）。
- 「どの呼び出しの値か」が分かるように、GUI のヘッダ/行情報（label, op#ordinal 等）を出力に含める。

## 非ゴール

- スケッチ `.py` の自動編集。
- “ユーザーの元コードを完全再構築” するコード生成（変数名や入力 Geometry までは推測しない）。
- 依存追加が必要なクリップボード実装（imgui 側に API が無ければ手コピーで良い）。

## 仕様案（UX）

### UI（トリガと出力先）

- v1: 各グループヘッダに `Snippet` ボタンを置く（= そのグループ単体を出力）。
- （任意）Parameter GUI 上部に `Snippet (All)` ボタンを置く（= 全グループを連結して出力）。
- 出力は **ファイル保存ではなくポップアップ**（modal）に表示する。
  - `input_text_multiline` の readonly に出し、**選択→コピー**で持ち帰れることを最優先。
  - ポップアップ表示直後にテキスト欄へ **フォーカス**を当て、`Ctrl/Cmd+A → Ctrl/Cmd+C` が即できるようにする。
  - 可能なら `Copy` ボタンも置く（clipboard API が使える場合はワンクリックでコピー）。
  - clipboard API が無い場合は「`Ctrl/Cmd+A → Ctrl/Cmd+C`」のヒントを添える。

### 出力単位

- v1: **グループ単位**（GUI のヘッダ単位）に `Snippet` を出せる。
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
# Style
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
# Layer style: layer#1 (label="outline")
dict(color=(0.0, 0.0, 0.0), thickness=0.002)
```

#### Primitive

- 出力例（call か kwargs のどちらか）:

```py
# circle#3 (label="dot")
G.circle(r=12.5, center=(50.0, 50.0, 0.0))
```

#### Effect chain

- 入力 Geometry は不明なので “ビルダ式”として出す（末尾の `(g)` は付けない）。
- 出力例:

```py
# Effect chain: effect#1 (label="line_eff")
E.affine(delta=(0.0, 0.0, 0.0)).dash(dash_length=(16.0, 4.0)).buffer().fill()
```

## 実装チェックリスト

### A. 仕様確定（最初に決める）

- [ ] デフォルトの値ソースを確定（推奨: `effective`）
- [ ] `Snippet` の UI 配置を確定（推奨: 各グループヘッダにボタン + 任意で `Snippet (All)`）
- [ ] 出力形式を確定（v1: `dict(...)` を基本、primitive/effect は `G./E.` 併記）

### B. Snippet 生成（純粋関数）

- [ ] `src/grafix/interactive/parameter_gui/snippet.py`（仮）を追加
- [ ] 入力:
  - [ ] `rows: list[ParameterRow]`
  - [ ] `step_info_by_site`（effect チェーン用）
  - [ ] `effect_chain_header_by_id` / `primitive_header_by_group`（表示名用、任意）
  - [ ] `last_effective_by_key: dict[ParameterKey, object]`（任意）
- [ ] 出力:
  - [ ] “グループ単位 snippet” を生成する API を用意（`group_id` → `str`）
  - [ ] RGB255 → RGB01 変換（Style / Layer style）
  - [ ] Python リテラル化（`repr()` 基本、tuple/float/int/str/bool）

### C. UI（Parameter GUI）

- [ ] `render_parameter_table(...)` の戻り値/引数を拡張し、snippet 要求イベントを返せるようにする
- [ ] `store_bridge.render_store_parameter_table(...)` 側でイベントを受け取り、snippet 文字列を作って UI に渡す
- [ ] ポップアップ（modal）で snippet を表示する
  - [ ] `input_text_multiline` で全文表示（readonly）
  - [ ] 表示直後にテキスト欄へフォーカス（`Ctrl/Cmd+A → Ctrl/Cmd+C` を即実行できる）
  - [ ] （可能なら）`Copy` ボタンでクリップボードへ送る
  - [ ] clipboard 不可の場合のコピー手順ヒントを表示する

### D. テスト

- [ ] `snippet.py` を unit test で担保（GUI 依存は持たせない）
- [ ] Style/layer の RGB 変換が正しいこと
- [ ] Effect chain のステップ順が `step_index` に従うこと
- [ ] 文字列出力が安定（辞書順/引数順）であること

## 事前に確認したいこと（返答ください）

- 1. Snippet のデフォルト値は `effective` で OK？（CC 割当中でも “その瞬間の値” を焼く）；はい
- 2. 出力形式は v1 ではこれで OK？；はい
  - Style/Layer: `dict(...)`
  - Primitive: `G.op(...)`
  - Effect: `E.op(...).op(...)`（ビルダ式）
- 3. UI はポップアップ表示（手コピー）をまず作って OK？（クリップボードは“あれば”対応）；はい（readonly + フォーカス + Copy ボタンがあれば入れる）
