# どこで: `docs/parameter_gui_refactor_checklist.md`。
# 何を: `src/app/parameter_gui.py` の肥大化に対するリファクタリング案と、実施手順チェックリストをまとめる。
# なぜ: kind→widget / テーブル描画 / store 反映 / pyglet+imgui ライフサイクルが同居し、変更時の見通しが落ちてきたため。

## 現状メモ（観測）

- `src/app/parameter_gui.py`（約 600 行）に以下が同居している。
  - kind→widget（`widget_*` と `_KIND_TO_WIDGET`）
  - 1 行描画（`render_parameter_row_4cols`）と 4 列テーブル（`render_parameter_table`）
  - ParamStore への反映（`render_store_parameter_table`, `_apply_updated_rows_to_store`）
  - pyglet + imgui の初期化・1 フレーム描画・破棄（`create_parameter_gui_window`, `ParameterGUI`）
- 手動スモークは `tests/manual/test_parameter_gui_*.py` が `from src.app.parameter_gui import render_parameter_table` を利用。

## リファクタリングのゴール

- 責務ごとに分割し、読みやすさと変更の局所性を上げる。
- `src/parameters/view.py`（純粋関数）との境界を維持し、GUI 依存部を閉じ込める。
- 依存方向を単純化（例: `widgets -> table -> store_bridge -> gui` の片方向）。

## 非ゴール

- GUI 仕様変更（UI レイアウトや挙動の変更、機能追加）。
- パフォーマンス最適化。

## 叩き台の案

### 案A（採用）: `src/app/parameter_gui/` をパッケージ化して分割

- 目的: 「同名の単一モジュール」を「小さなモジュール群」にして責務を明確化する。
- 影響: `src/app/parameter_gui.py` は消える（file → package へ置換）。import パス `src.app.parameter_gui` 自体は維持できる。
- 公開 API は最小に絞る（手動スモーク + GUI 運用に必要なもののみ）。
- 例の構成:
  - `src/app/parameter_gui/__init__.py`
    - 公開 API をここに集約（`ParameterGUI`, `create_parameter_gui_window`, `render_parameter_table`）。
  - `src/app/parameter_gui/widgets.py`
    - `WidgetFn` / `widget_*` / kind→widget registry / `render_value_widget`。
  - `src/app/parameter_gui/table.py`
    - `render_parameter_row_4cols` / `render_parameter_table` / 行 ID 生成などの UI レイアウト。
  - `src/app/parameter_gui/store_bridge.py`
    - snapshot と `rows_before/after` の突き合わせ、`update_state_from_ui` 経由で store 反映（内部 I/F）。
  - `src/app/parameter_gui/pyglet_backend.py`
    - `create_parameter_gui_window` / renderer 作成 / `io.display_fb_scale` 同期など。
  - `src/app/parameter_gui/gui.py`
    - `ParameterGUI`（1 フレーム描画・close）本体。

## 方針（確定）

- [x] 採用案: 案A（package 分割）
- [x] 公開 API: `render_parameter_table`, `create_parameter_gui_window`, `ParameterGUI`
  - `render_store_parameter_table` は現状 `ParameterGUI` からしか使われていないため、`store_bridge` 側の内部 I/F に寄せる。
  - それ以外（`widget_registry` 等）は内部扱いに寄せる。

## 実施チェックリスト（案A 前提）

- [x] `src/app/parameter_gui.py` の公開/内部 I/F を棚卸し（import 元の調査）
- [x] `src/app/parameter_gui/` を新設し、モジュール分割方針を確定
- [x] `widgets.py` へ移動（kind→widget / range 変換 / `render_value_widget`）
- [x] `table.py` へ移動（row/table 描画、列ごとの UI）
- [x] `store_bridge.py` へ移動（store 反映ロジック。外部公開しない）
- [x] `pyglet_backend.py` へ移動（renderer 作成、IO 同期、window 生成）
- [x] `gui.py` へ移動（`ParameterGUI`）
- [x] `src/app/parameter_gui/__init__.py` に公開 API を集約（`ParameterGUI`, `create_parameter_gui_window`, `render_parameter_table`）
- [x] `tests/manual/test_parameter_gui_*.py` の import が通ることを確認（必要なら更新）
- [ ] `ruff` を対象限定で実行して崩れを直す（環境に無ければスキップ）
- [x] `mypy` を対象限定で実行して崩れを直す

## ついでに直すと良さそう（任意・事前確認したい）

- [ ] docstring と実装の齟齬整理（例: `*_slider_range` が `ValueError` を投げない/検知しない問題）
- [ ] `CC_KEY_WIDTH` 等の UI 定数の置き場を決める（table 側へ寄せる）
- [ ] `cc_key` 入力ロジックの分岐を小関数へ分離（読みやすさ優先）

---

状態: 実施チェックリストは完了。任意項目は未着手。
