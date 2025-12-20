# `src/grafix/core/primitives/text.py` の `font` を Parameter GUI で選択しやすくする計画（2025-12-20）

## 目的

- Parameter GUI（`src/grafix/interactive/parameter_gui/`）で `text` primitive の `font` を「手入力」ではなく選択で決められるようにする。
- フィルターキーワード入力ボックス + フィルター結果のプルダウン（ドロップダウン）でフォントを選べるようにする。

## 対象範囲（今回）

- 対象パラメータ: `op="text"` / `arg="font"`（`src/grafix/core/primitives/text.py`）
- 対象 UI: Parameter GUI の value widget（`src/grafix/interactive/parameter_gui/widgets.py`）
- 連動挙動: `.ttc` 選択時に `font_index=0` へ戻す（`src/grafix/interactive/parameter_gui/store_bridge.py`）
- 解決規則: `kind="font"` は `bool` 同様に「常に GUI 値を採用」（`src/grafix/core/parameters/resolver.py` / `src/grafix/interactive/parameter_gui/rules.py`）
- `font` の型は `ParamMeta(kind="font")` を新設して扱う（UI は kind 駆動で描画）。
  - `src/grafix/core/primitives/text.py` の `text_meta["font"]` を `kind="font"` へ変更する。
  - `src/grafix/core/parameters/view.py` の `normalize_input()` に `kind="font"` を追加し、値は `str` として正規化する。
  - Parameter GUI は `row.kind == "font"` を専用 widget に割り当てる（`op/arg` ハードコードを避ける）。

## 仕様（UI/UX）

- control 列に以下を縦に配置する。
  - フィルター入力（テキストボックス）
  - フィルター結果のフォント一覧（プルダウン）
- フィルターは「大文字小文字無視の部分一致」を基本にする（スペース区切り AND）。
  - マッチ対象は「相対パス（`data/input/font` からの相対）」と「ファイル名/ステム」。
  - 空文字のときは全件表示。
- プルダウンの選択が確定したときだけ `row.ui_value`（= `font`）を更新する。
  - フィルター文字列は UI のローカル状態として保持し、`font` 値そのものは変更しない。
- 表示ラベルは stem のみ（拡張子なし）を表示する。
  - 実値（`font` に入れる文字列）は「`data/input/font` からの相対パス（拡張子あり）」にする。
- `.ttc` を選んだ場合は `font_index` を 0 へ戻し、`override=True` にする。

## 実装方針（最小・シンプル）

- フォント候補の列挙:
  - `src/grafix/core/primitives/text.py` の `_list_font_files()` 相当を再利用するか、Parameter GUI 側に同等の小ユーティリティを置く。
  - 1 フレームごとのフルスキャンは避け、モジュール内キャッシュ（現状の `_FONT_PATHS` のような形）を使う。
- フィルター UI 状態:
  - `row` の安定キー（例: `(row.op, row.site_id, row.arg)`）で `filter_text` を保持する `dict` を `widgets.py`（または専用小モジュール）に持つ。
  - GUI を閉じたら状態が消える程度の寿命でよい（永続化はしない）。
- ウィジェットの差し替え:
  - `render_value_widget()` に `row.kind == "font"` の専用 widget を追加する。
  - それ以外の `str` は現状の `input_text` のまま。

## 実装チェックリスト

### P0: 設計確定（先に合意する）

- [x] フィルターのマッチ仕様を決める（部分一致 / スペース区切り AND / 正規表現なし）
- [x] プルダウンの表示名を決める（ステムのみ）
- [x] 現在値がフィルター外のときの扱いを決める（選択肢に出さない。preview は現在値の stem を表示）

### P1: 実装（Parameter GUI）

- [x] `kind="font"` を導入する（コア）
  - [x] `src/grafix/core/primitives/text.py` の `text_meta["font"]` を `ParamMeta(kind="font")` にする
  - [x] `src/grafix/core/parameters/view.py` の `normalize_input()` に `kind=="font"` を追加（`str` と同じ正規化）
  - [x] `src/grafix/core/parameters/resolver.py` で `kind="font"` を「常に GUI 値採用」にする
- [x] フォント列挙ユーティリティを用意する（`data/input/font` 配下の `*.ttf/*.otf/*.ttc`）
  - [x] 表示用ラベル（stem）と値（相対パス+拡張子）を決める
- [x] フィルター処理（純粋関数）を作る
  - [x] `query -> filtered_items`（case-insensitive、空は全件、スペース区切り AND）
- [x] `kind=font` 専用 widget を追加する
  - [x] フィルター入力（`imgui.input_text`）
  - [x] フィルター結果のプルダウン（`imgui.begin_combo` + `imgui.selectable`）
  - [x] 選択時に `font` 値のみ更新（フィルター変更は更新扱いにしない）
  - [x] 0 件のときの表示（`No match`）
- [x] `render_value_widget()` の振り分けを追加する（`row.kind=="font"` → 専用 widget）
- [x] `.ttc` 選択時に `font_index=0` へ戻す（`store_bridge` で連動更新）

### P2: テスト（最低限・壊れにくい所だけ）

- [x] フィルター関数の単体テストを追加する（UI 依存なし）
  - 例: `tests/interactive/parameter_gui/test_parameter_gui_font_filter.py`
  - ケース: 空クエリ、大小文字、部分一致、スペース除去の有無（採用した仕様次第）
- [x] `kind="font"` の正規化テストを追加する
  - 例: `tests/core/parameters/test_parameter_normalize.py` に `ParamMeta(kind="font")` のケースを追加
- [x] `.ttc` 選択時に `font_index` が 0 へ戻ることのテストを追加する
  - 例: `tests/interactive/parameter_gui/test_parameter_gui_text_font_ttc_resets_font_index.py`

### P3: 検証（変更後に回す）

- [x] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
- [x] `PYTHONPATH=src pytest -q tests/core/parameters/test_parameter_normalize.py`
- [ ] `ruff check src/grafix/interactive/parameter_gui/widgets.py`（この環境では `ruff` が見つからず未実行）

## Done の定義（受け入れ条件）

- [ ] `text.font` が「フィルター入力 + ドロップダウン選択」で変更できる（未手動確認）
- [ ] フィルター入力は `font` 値を汚さない（選択確定時だけ `font` が変わる）（未手動確認）
- [x] 既存の Parameter GUI テストが通る（`tests/interactive/parameter_gui`）

## 事前確認したいこと（あなたに質問）

- [x] フィルターは「スペース区切りを AND 扱い」にしたい？（例: `noto sans` → `noto` と `sans` を両方含む）；はい
- [x] ドロップダウンの表示は相対パスで OK？（例: `NotoSansJP-Regular.otf` / `jp/NotoSansJP-Regular.otf`）；stem だけでいいよ。
- [x] `.ttc` を選んだとき、`font_index` も同時に 0 へ戻す挙動にしたい？（別途 UI 追加は今回はしない想定）；はい
- [x] `kind="font"` は `bool` のように「常に GUI 値を採用」にしたい？（= override は不要、という扱い）；はい
