# Parameter GUI MIDI ボタン配置改善計画（2026-08-01）

- 状態: **承認待ち**
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

- [ ] `src/grafix/interactive/parameter_gui/gui.py`
  - MIDI menu から右寄せ座標計算を削除する。
  - `Filters → same_line → MIDI` の配置を caller 側へ明示する。
  - 「主操作行の右端」という docstring / comment を新しい契約へ合わせる。

### Tests

- [ ] `tests/interactive/parameter_gui/test_parameter_table_toolbar.py`
  - Filters と MIDI の layout event を記録し、間に手動 cursor 移動がないことを検証する。
  - 右寄せ専用だった test double の残り幅・cursor API を削除する。
  - MIDI popup、Reconnect、Clear、Undo の既存テストを維持する。
- [ ] `tests/interactive/parameter_gui/test_toolbar_layout.py`
  - 既存 real pyimgui smoke を実行し、必要な場合のみ最小限の item rect 検証を追加する。
  - 複雑な recording proxy や production 用 geometry helper は追加しない。

### Plan

- [ ] 実装と検証の進捗を本ファイルへ反映し、未完了項目を明記する。

## 4. 実装フェーズ

### Phase 0 — 承認と作業前確認

- [x] 現行 GUI を 1100 px と 760 px で撮影し、空白量と非 clipping を確認した。
- [x] 根本原因を固定 56 px の右寄せ処理へ特定した。
- [x] 計画作成時の `git status --porcelain` が空であることを確認した。
- [ ] 本計画についてユーザーの承認を得る。
- [ ] 実装開始直前に `git status --porcelain` と対象ファイルの並行差分を再確認する。

### Phase 1 — 失敗する回帰テスト

- [ ] test double に button / `same_line` / cursor 移動の event 記録を追加する。
- [ ] `parameter_filter_menu → same_line → midi_menu` が連続し、途中に
  `set_cursor_pos_x` がないことを期待する test を追加する。
- [ ] 現行実装では固定右寄せにより test が失敗することを確認する。

### Phase 2 — 最小実装

- [ ] `_render_midi_mapping_menu()` から配置処理を除き、menu の描画と command 処理だけを残す。
- [ ] `_render_parameter_table_toolbar()` で Filters の直後に `same_line()` と MIDI menu を置く。
- [ ] 56 px の magic number、不要な局所変数、右端前提の説明を削除する。
- [ ] `content_region_available_width` と `_window_ui_coordinate_scale` は同ファイル内の別用途を
  維持し、誤って削除しない。

### Phase 3 — 機能回帰の確認

- [ ] MIDI session 無効時もボタンと `MIDI OFF` status が従来どおり表示される。
- [ ] assignment count、Reconnect、Clear frozen snapshot、Clear all mappings を維持する。
- [ ] Clear all 後の Undo notice と history transaction を維持する。
- [ ] filter state、検索、Show inactive、Expand / Collapse / Shortcuts の順序と機能を維持する。

### Phase 4 — 実機レイアウト確認

- [ ] repository の WorkspaceState を変更しないよう、一時 `output_dir` を注入して起動する。
- [ ] `parameter_persistence=False`、`midi_port_name=None`、`n_worker=0` で確認する。
- [ ] Inspector 幅 1100 px と 760 px の before / after screenshot を取得する。
- [ ] 両方の幅で Filters と MIDI が通常 item spacing で隣接することを確認する。
- [ ] 760 px で折り返し、重なり、clipping がないことを確認する。
- [ ] MIDI popup を開き、画面内表示と既存 menu item の操作性を確認する。
- [ ] table の固定 MIDI 列と各行の MIDI learn control が変化していないことを確認する。

### Phase 5 — 品質確認

- [ ] focused test を実行する。

  ```bash
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
    /opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
    tests/interactive/parameter_gui/test_parameter_table_toolbar.py \
    tests/interactive/parameter_gui/test_toolbar_layout.py
  ```

- [ ] 対象ファイルへ `ruff check` を実行する。
- [ ] `mypy src/grafix` を実行する。
- [ ] `PYTHONPATH=src pytest -q` で full test suite を実行する。
- [ ] `git diff --check` を実行する。
- [ ] 最終 `git status --porcelain` で依頼外差分がないことを確認する。

## 5. 受け入れ条件

- `PARAMETERS / Search / Show inactive / Filters / MIDI` が一つの連続した主操作群に見える。
- Filters と MIDI の間隔がウィンドウ幅に比例して増えず、通常 item spacing だけになる。
- 760 px と 1100 px の双方で主操作行が clipping しない。
- MIDI menu の位置決定に残り幅、手動 cursor 座標、固定 56 px を使わない。
- MIDI popup の status と全 command、Clear 後の Undo が退行しない。
- table の MIDI 列幅、行内 learn control、WorkspaceState、MIDI runtime 契約を変更しない。
- focused test、ruff、mypy、full pytest、`git diff --check` が成功する。
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

未実施。ユーザー承認後に Phase 1 から開始する。
