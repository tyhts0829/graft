# Parameter GUI MIDI ボタン配置改善計画（2026-08-01）

- 状態: **実装完了（2026-08-01、既存失敗 2 件を除き検証済み）**
- 対象: Parameter GUI の `PARAMETERS` 主操作行にある MIDI assignment menu
- 計画作成時 HEAD: `3bbd71f`
- 計画作成時 branch: `main`

本計画は、GUI 上部の MIDI ボタンが `Filters` から大きく離れて見える問題を、
レイアウト責務を単純化しながら改善する。production code と test は、本計画への
明示承認後に変更する。

## 1. 現象と原因

現行 GUI を実機で確認すると、MIDI ボタンは `PARAMETERS` 主操作行の右端に置かれる。

- Inspector 幅 1100 px: `Filters` の右端から MIDI の左端まで約 485 logical px
- Inspector 最小幅 760 px: 同じ間隔が約 145 logical px
- 重なりや clipping はないが、ウィンドウを広げるほど MIDI だけが操作群から離れる

`src/grafix/interactive/parameter_gui/gui.py` の `_render_midi_mapping_menu()` は、
`same_line()` の後に残り幅を取得し、次の固定値で cursor を右端へ移動している。

```python
available_width - 56.0 * coordinate_scale
```

この `56.0` は MIDI ボタンの実幅、theme の frame padding、table の MIDI 列幅の
いずれとも共有されていない。結果として、ボタンは右端の MIDI 列内には収まるものの、
主操作群との関係が弱く、列内でも右へ偏って見える。

これは最近の回帰ではなく、MIDI menu 導入時からの意図的な右寄せである。一方、既存
test は描画順と menu 機能だけを確認し、cursor の強制移動や item 間隔を検証していない。

## 2. 採用するレイアウト契約

MIDI は table 全体に対する global command として、`Filters` の直後へ通常の
ImGui item spacing で配置する。

```text
PARAMETERS  Search  Show inactive  Filters  MIDI
```

- `_render_parameter_table_toolbar()` が `Filters` と MIDI の横並びを所有する。
- Filters popup の描画後に `imgui.same_line()` を呼び、続けて
  `_render_midi_mapping_menu()` を呼ぶ。
- `_render_midi_mapping_menu()` は menu の内容と command 処理だけを所有し、
  `same_line()`、残り幅計算、cursor 座標変更を行わない。
- ウィンドウを広げた余白は MIDI の後ろへ残し、関連する操作の間へ挿入しない。
- 760 px の最小幅でも主操作行を維持し、新しい breakpoint や折り返し規則は追加しない。
- 新しい layout helper、固定幅定数、互換 wrapper は追加しない。

右端への正確な再整列は孤立感を解消しないため採用しない。MIDI 列 header への統合は、
複数 table、空結果、scroll、GUI command の callback 配線をまたぐため、この小さな改善には
過剰であり採用しない。

## 3. 変更対象

### Production

- [x] `src/grafix/interactive/parameter_gui/gui.py`
  - MIDI menu から右寄せ座標計算を削除する。
  - `Filters → same_line → MIDI` の配置を caller 側へ明示する。
  - 「主操作行の右端」という docstring / comment を新しい契約へ合わせる。

### Tests

- [x] `tests/interactive/parameter_gui/test_parameter_table_toolbar.py`
  - Filters と MIDI の layout event を記録し、間に手動 cursor 移動がないことを検証する。
  - 右寄せ専用だった test double の残り幅・cursor API を削除する。
  - MIDI popup、Reconnect、Clear、Undo の既存テストを維持する。
- [x] `tests/interactive/parameter_gui/test_toolbar_layout.py`
  - 既存 real pyimgui smoke を実行し、必要な場合のみ最小限の item rect 検証を追加する。
  - 複雑な recording proxy や production 用 geometry helper は追加しない。

### Plan

- [x] 実装と検証の進捗を本ファイルへ反映し、未完了項目を明記する。

## 4. 実装フェーズ

### Phase 0 — 承認と作業前確認

- [x] 現行 GUI を 1100 px と 760 px で撮影し、空白量と非 clipping を確認した。
- [x] 根本原因を固定 56 px の右寄せ処理へ特定した。
- [x] 計画作成時の `git status --porcelain` が空であることを確認した。
- [x] 本計画についてユーザーの承認を得る。
- [x] 実装開始直前に `git status --porcelain` と対象ファイルの並行差分を再確認する。

### Phase 1 — 失敗する回帰テスト

- [x] test double に button / `same_line` / cursor 移動の event 記録を追加する。
- [x] `parameter_filter_menu → same_line → midi_menu` が連続し、途中に
  `set_cursor_pos_x` がないことを期待する test を追加する。
- [x] 現行実装では固定右寄せにより test が失敗することを確認する。

### Phase 2 — 最小実装

- [x] `_render_midi_mapping_menu()` から配置処理を除き、menu の描画と command 処理だけを残す。
- [x] `_render_parameter_table_toolbar()` で Filters の直後に `same_line()` と MIDI menu を置く。
- [x] 56 px の magic number、不要な局所変数、右端前提の説明を削除する。
- [x] `content_region_available_width` と `_window_ui_coordinate_scale` は同ファイル内の別用途を
  維持し、誤って削除しない。

### Phase 3 — 機能回帰の確認

- [x] MIDI session 無効時もボタンと `MIDI OFF` status が従来どおり表示される。
- [x] assignment count、Reconnect、Clear frozen snapshot、Clear all mappings を維持する。
- [x] Clear all 後の Undo notice と history transaction を維持する。
- [x] filter state、検索、Show inactive、Expand / Collapse / Shortcuts の順序と機能を維持する。

### Phase 4 — 実機レイアウト確認

- [x] repository の WorkspaceState を変更しないよう、一時 `output_dir` を注入して起動する。
- [x] `parameter_persistence=False`、`midi_port_name=None`、`n_worker=0` で確認する。
- [x] Inspector 幅 1100 px と 760 px の before / after screenshot を取得する。
- [x] 両方の幅で Filters と MIDI が通常 item spacing で隣接することを確認する。
- [x] 760 px で折り返し、重なり、clipping がないことを確認する。
- [x] MIDI popup を開き、画面内表示と既存 menu item の操作性を確認する。
- [x] table の固定 MIDI 列と各行の MIDI learn control が変化していないことを確認する。

### Phase 5 — 品質確認

- [x] focused test を実行する。

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
    /opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
    tests/interactive/parameter_gui/test_parameter_table_toolbar.py \
    tests/interactive/parameter_gui/test_toolbar_layout.py
  ```

- [x] 対象ファイルへ `ruff check` を実行する。
- [x] `mypy src/grafix` を実行する。
- [x] `PYTHONPATH=src pytest -q` で full test suite を実行する。
  - 4118 passed、2 failed。2 件はいずれも変更前 HEAD の archive でも再現した既存失敗。
- [x] `git diff --check` を実行する。
- [x] 最終 `git status --porcelain` で依頼外差分がないことを確認する。

## 5. 受け入れ条件

- `PARAMETERS / Search / Show inactive / Filters / MIDI` が一つの連続した主操作群に見える。
- Filters と MIDI の間隔がウィンドウ幅に比例して増えず、通常 item spacing だけになる。
- 760 px と 1100 px の双方で主操作行が clipping しない。
- MIDI menu の位置決定に残り幅、手動 cursor 座標、固定 56 px を使わない。
- MIDI popup の status と全 command、Clear 後の Undo が退行しない。
- table の MIDI 列幅、行内 learn control、WorkspaceState、MIDI runtime 契約を変更しない。
- focused test、ruff、mypy、`git diff --check` が成功する。full pytest は実行し、
  失敗がある場合は変更前 HEAD との比較で本変更による新規失敗がないことを確認する。
- before / after screenshot と本計画の完了チェックから結果を追跡できる。

## 6. 対象外

- Parameter table の Source / Value / Range / MIDI 列幅変更
- 行内 MIDI learn の X / Y / Z、R / G / B、V control の再設計
- MIDI session、controller、snapshot、mapping 優先順位の変更
- MIDI popup の label、項目、command semantics の変更
- toolbar 全体の新しい responsive breakpoint や複数行再設計
- WorkspaceState、RuntimeConfig、保存 schema の変更
- 過去のレイアウト計画文書や既存 screenshot の書き換え

## 7. 実施結果

2026-08-01 に実装した。

- `_render_midi_mapping_menu()` から残り幅取得、coordinate scale、固定 56 px、
  `set_cursor_pos_x()` を削除し、menu の機能だけを残した。
- `_render_parameter_table_toolbar()` が Filters の直後で `same_line()` を呼び、
  MIDI を同じ主操作群へ配置するようにした。
- 修正前に追加した layout event test は、Filters と MIDI の間の
  `set_cursor_pos_x` を検出して失敗した。修正後は期待する 3 event の並びで成功した。
- 右寄せ専用だった test double の content width / cursor API を削除した。
- Inspector 幅 1100 px と最小幅 760 px の双方で、通常 spacing、非 clipping、
  table MIDI 列の不変を実機確認した。
- MIDI popup を実際に開き、`MIDI OFF · 0 mappings`、Reconnect、
  Clear frozen snapshot、Clear all mappings が画面内へ表示されることを確認した。
- repository の WorkspaceState は変更せず、実機確認用の出力は一時 directory に隔離した。

検証結果:

- focused GUI tests: 21 passed
- 対象 Ruff: success
- `mypy src/grafix`: success（292 source files）
- full pytest: 4118 passed、2 failed
- `git diff --check`: success

full pytest の失敗 2 件は、変更前 HEAD `c89778b` を `/tmp` へ展開した baseline でも
同じ内容で再現した。

- `test_gui_construction_failure_closes_completed_draw_system_and_midi_once`:
  test double の `capture_service` に `_export_owned` がなく、GUI construction より前に失敗する。
- `test_active_sketch_inventory_is_not_empty`:
  test の期待値 52 に対して tracked entrypoint が 53 件ある。

この 2 件は本変更の対象外であり、対象 production / test の未完了項目はない。
