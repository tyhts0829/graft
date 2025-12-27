# ParameterGUI: A（pyglet.app.run へ移行）実装改善計画（2025-12-27）
#
# どこで: `src/grafix/interactive/runtime/window_loop.py` / `src/grafix/api/run.py`（+ 関連サブシステム）。
# 何を: 手動 `Window.dispatch_events()` ループ（`MultiWindowLoop.run()`）を廃止し、`pyglet.app.run()` に寄せてイベント配送を安定化する。
# なぜ: macOS（arm64）で release 等の入力イベント取りこぼしが起き、ImGui 側の押下状態（`io.mouse_down`）が残留して「クリック不発 / ドラッグ解除不能」になる可能性が高いため。
#      参照: `docs/memo/parameter_gui_mouse_stuck_drag_rootcause_report_2025-12-27.md` の対策候補 A。
#

## ゴール

- 手動ループ（`Window.dispatch_events()` を毎フレーム呼ぶ方式）をやめ、`pyglet.app.run()` でイベント処理を pyglet に委譲する。
- 2 ウィンドウ（描画 + Parameter GUI）構成のまま、どちらかを閉じたら確実に終了し、後始末（ParamStore 保存/close）が必ず走る。
- macOS（arm64）で「後半（しばらく操作した後）から効かなくなる」系の入力不具合が再発しない。

## 非ゴール（この計画では扱わない）

- GUI 外 release / フォーカス喪失時の強制解除（対策候補 C/D）。
- imgui/pyglet backend の fork/patch（対策候補 E）。
- ImGui IO 同期順序の再設計など、対策候補 B の本格対応。

## 方針（A の設計メモ）

- `pyglet.app.run(interval=None)` を使い、描画タイミングは `pyglet.clock.schedule(_interval)` で制御する。
- 各 window の `on_draw` に `task.draw_frame()` をぶら下げ、`Window.draw(dt)` が `switch_to()` / `flip()` を担当する。
- 複数 window の描画は「1つの scheduled 関数で tasks を順に `Window.draw(dt)`」として揃える。
- 終了条件は「どれか 1 つのウィンドウが閉じられたら `pyglet.app.exit()`」で統一する。

## 実装チェックリスト（A）

### 1) `MultiWindowLoop` を `pyglet.app.run()` ベースに置換

- [x] `src/grafix/interactive/runtime/window_loop.py` の説明コメントを「手動ループ」前提から更新する
- [x] `MultiWindowLoop.run()` を以下の構造に置き換える
  - [x] 各 `WindowTask.window` に `on_draw` ハンドラを設定する（`task.draw_frame()` を呼ぶ）
  - [x] `fps > 0` の場合、`pyglet.clock.schedule_interval(draw_all, 1 / fps)` を設定する
  - [x] `fps <= 0` の場合、`pyglet.clock.schedule(draw_all)` を設定する（可能な限り回す）
  - [x] `on_close` をフックして `pyglet.app.exit()` を呼ぶ（どれか 1 つ閉じたら全体停止）
  - [x] `pyglet.app.run(interval=None)` を呼び、戻ったら `unschedule` する（次回 run へ副作用を残さない）

### 2) `run()` 側の配線を A の前提に合わせる

- [x] `src/grafix/api/run.py` の「手動ループ」コメントを更新する（実態に合わせる）
- [x] `pyglet.options["vsync"]` と Parameter GUI window の `vsync` が食い違わないように整理する（両 window を `vsync=False` へ）
- [x] 「どちらかの window を閉じたら終了」時に `finally` が必ず通る（ParamStore 保存 → closers）構造を維持する

### 2.1) A 実装中に見つけた、最小の追加修正（A を壊さない範囲）

- [x] `renderer.process_inputs()` は内部で `pyglet.clock.tick()` を呼ぶため、`pyglet.app.run()` と相性が悪いので呼ばない（`src/grafix/interactive/parameter_gui/gui.py`）

### 3) 最小 QA（A の受け入れ条件）

- [ ] 通常操作: button / checkbox / slider が普通にクリック・ドラッグできる
- [ ] 再現操作: slider ドラッグ → GUI 外（描画 window 上）で release → 押下残留しない
- [ ] 再現操作: slider ドラッグ → アプリ外で release → 押下残留しない（可能なら）
- [ ] 時間依存: 5〜10 分程度操作しても「後半から効かなくなる」が再発しない

## 最小の検証コマンド（変更後）

- [x] `python -m py_compile src/grafix/interactive/runtime/window_loop.py src/grafix/api/run.py src/grafix/interactive/runtime/parameter_gui_system.py src/grafix/interactive/parameter_gui/gui.py src/grafix/interactive/runtime/draw_window_system.py`
- [ ] `ruff check src/grafix/interactive/runtime/window_loop.py src/grafix/api/run.py src/grafix/interactive/runtime/parameter_gui_system.py src/grafix/interactive/parameter_gui/gui.py src/grafix/interactive/runtime/draw_window_system.py`（ruff 未導入）
- [ ] `mypy src/grafix/api/run.py`（既存のプロジェクト全体エラーにより未完了）
