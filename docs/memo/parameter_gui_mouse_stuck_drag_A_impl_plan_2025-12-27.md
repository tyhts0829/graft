# ParameterGUI: A（pyglet.app.run へ移行）実装改善計画（2025-12-27）

#

# どこで: `src/grafix/interactive/runtime/window_loop.py` / `src/grafix/api/run.py`（+ 関連サブシステム）。

#

# 何を: 手動 `Window.dispatch_events()` ループ（`MultiWindowLoop.run()`）を廃止し、`pyglet.app.run()` に寄せてイベント配送を安定化する。

#

# なぜ: macOS（arm64）で release 等の入力イベント取りこぼしが起き、ImGui 側の押下状態（`io.mouse_down`）が残留して

# 「クリック不発 / ドラッグ解除不能」になる可能性が高いため。

# 参照: `docs/memo/parameter_gui_mouse_stuck_drag_rootcause_report_2025-12-27.md` の対策候補 A。

#

## ゴール

- 手動ループ（`Window.dispatch_events()` を毎フレーム呼ぶ方式）をやめ、`pyglet.app.run()` でイベント処理を pyglet に委譲する。
- 2 ウィンドウ（描画 + Parameter GUI）構成のまま、どちらかを閉じたら確実に終了し、後始末（ParamStore 保存/close）が必ず走る。
- macOS（arm64）で「後半（しばらく操作した後）から効かなくなる」系の入力不具合が再発しない。

## 非ゴール（この計画では扱わない）

- `renderer.process_inputs()` を使わない/呼び方を見直す（対策候補 B）。
- GUI 外 release / フォーカス喪失時の強制解除（対策候補 C/D）。
- imgui/pyglet backend の fork/patch（対策候補 E）。

## 方針（A の設計メモ）

- `pyglet.app` のイベントループが `Window.invalid=True` のウィンドウに対して `on_draw` を dispatch する仕様に寄せる。
  - `on_draw` 内で描画して `window.invalid = False` に戻す（無限 redraw を防ぐ）。
  - FPS 制御は `pyglet.clock.schedule_interval()` で `window.invalid = True` を立てることで行う。
- 複数ウィンドウの描画は「各 window の `on_draw`」に寄せ、`switch_to()` / `flip()` の責務は pyglet に任せる。
  - 既存の `draw_frame()` は `flip()` しない前提なので、そのまま `on_draw` から呼べる。
- 終了条件は「どれか 1 つのウィンドウが閉じられたら `pyglet.app.exit()`」で統一する。

## 実装チェックリスト（A）

### 1) `MultiWindowLoop` を `pyglet.app.run()` ベースに置換

- [ ] `src/grafix/interactive/runtime/window_loop.py` の説明コメントを「手動ループ」前提から更新する
- [ ] `MultiWindowLoop.run()` を以下の構造に置き換える
  - [ ] 各 `WindowTask.window` に `on_draw` ハンドラを設定する（`task.draw_frame()` を呼ぶ）
  - [ ] `on_draw` の末尾で `window.invalid = False` にする（描画は「必要時のみ」へ）
  - [ ] `fps > 0` の場合
    - [ ] `pyglet.clock.schedule_interval(tick, 1 / fps)` を設定する
    - [ ] `tick` で `on_frame_start()` を呼び、全 window の `invalid = True` を立てる
  - [ ] `fps <= 0` の場合
    - [ ] `on_draw` で `invalid=False` にしない（= 可能な限り回す）か、
          または `pyglet.clock.schedule(tick)` を使い「毎 tick invalid=True」にする（どちらかに統一）
  - [ ] `on_close` をフックして `pyglet.app.exit()` を呼ぶ（どれか 1 つ閉じたら全体停止）
  - [ ] `pyglet.app.run()` を呼び、戻ったら `schedule` を解除する（次回 run へ副作用を残さない）

### 2) `run()` 側の配線を A の前提に合わせる

- [ ] `src/grafix/api/run.py` の「手動ループ」コメントを更新する（実態に合わせる）
- [ ] `pyglet.options["vsync"]` と Parameter GUI window の `vsync` が食い違わないように整理する
  - [ ] まずは変数を増やさないため、両 window とも明示的に `vsync=False` へ揃える（暫定）
  - [ ] そのうえで `vsync=True` を戻せるかは A の後で検討（まずは入力安定を優先）
- [ ] 「どちらかの window を閉じたら終了」時に `finally` が必ず通ることを確認する
  - [ ] ParamStore 保存 → closers の順序が維持される

### 3) 最小 QA（A の受け入れ条件）

- [ ] 通常操作: button / checkbox / slider が普通にクリック・ドラッグできる
- [ ] 再現操作: slider ドラッグ → GUI 外（描画 window 上）で release → 押下残留しない
- [ ] 再現操作: slider ドラッグ → アプリ外で release → 押下残留しない（可能なら）
- [ ] 時間依存: 5〜10 分程度操作しても「後半から効かなくなる」が再発しない

## 最小の検証コマンド（変更後）

- [ ] `ruff check src/grafix/interactive/runtime/window_loop.py src/grafix/api/run.py`
- [ ] `mypy src/grafix/api/run.py`

## 確認したい点（この計画で進めてよい？）

- `fps <= 0`（スロットリング無し）の扱いは「常に `invalid=True`（無限 redraw）」で良い？それとも「毎 tick で invalid を立てる」に寄せる？；あなたの推奨でいいよ。
- A は「`MultiWindowLoop` の中身を置き換える」方針で良い？（新規クラス/ファイル追加は最小にする前提）;はい
