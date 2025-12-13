# どこで: `docs/parameter_gui_refactor_exec_checklist.md`。
# 何を: `src/app/parameter_gui.py` を package 分割でリファクタする実行用チェックリスト。
# なぜ: 途中経過と完了/未完了を常に明確化し、作業を反復可能にするため。

## 方針（確定）

- [x] file→package 置換で分割する（`src/app/parameter_gui.py` -> `src/app/parameter_gui/`）。
- [x] 公開 API は最小（`render_parameter_table`, `create_parameter_gui_window`, `ParameterGUI`）。
- [x] 依存方向は片方向（`widgets -> table -> store_bridge -> gui` + `pyglet_backend`）。

## 実施チェックリスト

- [x] import 元を調査し、公開 API を固定する
- [x] `src/app/parameter_gui/` を作成する
- [x] `src/app/parameter_gui/__init__.py` を追加し公開 API を集約する
- [x] `src/app/parameter_gui/widgets.py` を追加する（kind→widget）
- [x] `src/app/parameter_gui/table.py` を追加する（row/table 描画）
- [x] `src/app/parameter_gui/store_bridge.py` を追加する（store 反映。外部公開しない）
- [x] `src/app/parameter_gui/pyglet_backend.py` を追加する（window/renderer/io 同期）
- [x] `src/app/parameter_gui/gui.py` を追加する（`ParameterGUI`）
- [x] 旧 `src/app/parameter_gui.py` を削除する
- [x] `tests/manual/test_parameter_gui_*.py` の import が通ることを確認する
- [x] `python -m compileall` を対象限定で実行する（構文エラー検知）
- [ ] `ruff` を対象限定で実行する（環境に無ければスキップ）
- [x] `mypy` を対象限定で実行する
- [x] `docs/parameter_gui_refactor_checklist.md` の完了チェックを更新する
