# どこで: `docs/parameter_gui_runner_integration_checklist.md`。

# 何を: `main.py` 実行時に Parameter GUI で ParamStore を編集できるよう、ランナー（`src/api/run.py`）へ統合する実装計画チェックリスト。

# なぜ: 手動スモークだけでなく、実際のプレビュー実行中にパラメータ調整できる経路を最短で用意するため。

## ゴール

- `python main.py` 実行でプレビュー描画と同時に Parameter GUI が使える。
- GUI での変更（値/override/cc_key/ui_min/ui_max）が次フレーム以降の描画に反映される。

## 方針

- `src/api/run.py` の `run()` が生成する `ParamStore` を GUI と描画で共有する。
- GUI は別ウィンドウ（pyglet window）として生成し、`MultiWindowLoop` で両ウィンドウを同一ループで回す。
  - `draw_frame()` は描画のみ（`flip()` はループ側で一元化）に寄せ、画面更新の競合を避ける。
- 公開 API の互換ラッパは増やさず、必要な最小の引数追加のみで済ませる。

## 方針（確定）

- [x] GUI 既定は ON（`run(..., parameter_gui=True)` を省略しても GUI が出る）
- [x] どちらかのウィンドウを閉じたら両方終了
- [x] GUI ウィンドウのサイズ/タイトルは固定値（引数化しない）

## 実装チェックリスト

- [x] `src/api/run.py` に `parameter_gui: bool`（既定 True）を追加する
- [x] `src/api/run.py` で `ParamStore()` を GUI と描画で共有する
- [x] `parameter_gui=True` のときだけ GUI を初期化する
  - `from src.app.runtime.parameter_gui_system import ParameterGUIWindowSystem`
  - `gui = ParameterGUIWindowSystem(store=param_store)`
- [x] `MultiWindowLoop` へ GUI を統合する
  - `tasks = [WindowTask(draw_window.window, draw_window.draw_frame), WindowTask(gui.window, gui.draw_frame)]`
  - `loop = MultiWindowLoop(tasks, fps=60.0)`
- [x] close/teardown を一元化する（`run()` の `finally`）
  - `for system in reversed(systems): system.close()`
- [x] `main.py` は更新しない（`parameter_gui` 既定 ON のため）
- [ ] 動作確認（手動）
  - `python main.py` を実行し、GUI で `circle.r` を動かして半径が変わる
  - `override` を OFF/ON して効き方が変わる（float/int/vec3 は override ON が必要）
  - `ui_min/ui_max` を GUI で変えた後、スライダー範囲が変わる
- [ ] 既存チェックリストを更新する
  - [x] `docs/parameter_gui_phase3_checklist.md` の「イベントループ統合/確認ポイント CP3」など
  - [ ] `docs/parameter_gui_impl_plan.md` フェーズ 4 に統合完了を反映（必要なら）

## 既知の注意点

- GUI は `ParamMeta` があるキーのみ表示するため、最初のフレームを描くまでは空になり得る（`store.store_frame_params()` がフレーム末尾で meta を確定するため）。
- float/int/vec3 は `override` が OFF の間は base 値が優先され、GUI 値が反映されない（仕様）。

## 完了定義

- `python main.py` で描画と GUI が同時に開き、GUI 操作が次フレームの描画へ反映される。
- どちらかのウィンドウを閉じたときに、例外なくプロセスが終了（または方針通りに継続）する。
