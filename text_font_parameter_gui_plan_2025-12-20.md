# `src/grafix/core/primitives/text.py` の `font` を Parameter GUI で選択しやすくする計画（2025-12-20）

## 目的

- Parameter GUI（`src/grafix/interactive/parameter_gui/`）で `text` primitive の `font` を「手入力」ではなく選択で決められるようにする。
- フィルターキーワード入力ボックス + フィルター結果のプルダウン（ドロップダウン）でフォントを選べるようにする。

## 対象範囲（今回）

- 対象パラメータ: `op="text"` / `arg="font"`（`src/grafix/core/primitives/text.py`）
- 対象 UI: Parameter GUI の value widget（`src/grafix/interactive/parameter_gui/widgets.py`）
- 影響を最小化するため、`font` の型（ParamMeta.kind）は当面 `str` のまま扱う（UI 側で特殊表示）。

## 仕様（UI/UX）

- control 列に以下を縦に配置する。
  - フィルター入力（テキストボックス）
  - フィルター結果のフォント一覧（プルダウン）
- フィルターは「大文字小文字無視の部分一致」を基本にする。
  - マッチ対象は「相対パス（`data/input/font` からの相対）」と「ファイル名/ステム」。
  - 空文字のときは全件表示。
- プルダウンの選択が確定したときだけ `row.ui_value`（= `font`）を更新する。
  - フィルター文字列は UI のローカル状態として保持し、`font` 値そのものは変更しない。
- 表示ラベルは「相対パス」を基本にする（サブフォルダがある場合も識別できるため）。

## 実装方針（最小・シンプル）

- フォント候補の列挙:
  - `src/grafix/core/primitives/text.py` の `_list_font_files()` 相当を再利用するか、Parameter GUI 側に同等の小ユーティリティを置く。
  - 1フレームごとのフルスキャンは避け、モジュール内キャッシュ（現状の `_FONT_PATHS` のような形）を使う。
- フィルター UI 状態:
  - `row` の安定キー（例: `(row.op, row.site_id, row.arg)`）で `filter_text` を保持する `dict` を `widgets.py`（または専用小モジュール）に持つ。
  - GUI を閉じたら状態が消える程度の寿命でよい（永続化はしない）。
- ウィジェットの差し替え:
  - `render_value_widget()` に「`row.kind == "str"` かつ `row.op=="text"` かつ `row.arg=="font"` の場合だけ」専用 widget を割り当てる。
  - それ以外の `str` は現状の `input_text` のまま。

## 実装チェックリスト

### P0: 設計確定（先に合意する）

- [ ] フィルターのマッチ仕様を決める（部分一致 / スペース区切りAND / 正規表現なし）
- [ ] プルダウンの表示名を決める（相対パス / ファイル名のみ / ステムのみ）
- [ ] 現在値がフィルター外のときの扱いを決める（選択肢に出さない / 先頭に固定表示）

### P1: 実装（Parameter GUI）

- [ ] フォント列挙ユーティリティを用意する（`data/input/font` 配下の `*.ttf/*.otf/*.ttc`）
  - [ ] 表示用ラベル（相対パス）と値（`font` に入れる文字列）を決める
- [ ] フィルター処理（純粋関数）を作る
  - [ ] `query -> filtered_items`（case-insensitive、空は全件）
- [ ] `text.font` 専用 widget を追加する
  - [ ] フィルター入力（`imgui.input_text`）
  - [ ] フィルター結果のプルダウン（`imgui.begin_combo` か `imgui.combo`）
  - [ ] 選択時に `font` 値のみ更新（フィルター変更は更新扱いにしない）
  - [ ] 0件のときの表示（例: `"No match"`）を入れる
- [ ] `render_value_widget()` の振り分けを追加する（`text.font` → 専用 widget）

### P2: テスト（最低限・壊れにくい所だけ）

- [ ] フィルター関数の単体テストを追加する（UI 依存なし）
  - 例: `tests/interactive/parameter_gui/test_parameter_gui_font_filter.py`
  - ケース: 空クエリ、大小文字、部分一致、スペース除去の有無（採用した仕様次第）

### P3: 検証（変更後に回す）

- [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
- [ ] `ruff check src/grafix/interactive/parameter_gui/widgets.py`

## Done の定義（受け入れ条件）

- [ ] `text.font` が「フィルター入力 + ドロップダウン選択」で変更できる
- [ ] フィルター入力は `font` 値を汚さない（選択確定時だけ `font` が変わる）
- [ ] 既存の Parameter GUI テストが通る（少なくとも `tests/interactive/parameter_gui`）

## 事前確認したいこと（あなたに質問）

- [ ] フィルターは「スペース区切りを AND 扱い」にしたい？（例: `noto sans` → `noto` と `sans` を両方含む）
- [ ] ドロップダウンの表示は相対パスでOK？（例: `NotoSansJP-Regular.otf` / `jp/NotoSansJP-Regular.otf`）
- [ ] `.ttc` を選んだとき、`font_index` も同時に 0 へ戻す挙動にしたい？（別途 UI 追加は今回はしない想定）

