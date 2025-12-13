# どこで: `docs/run_py_refactor_checklist.md`。
# 何を: `src/api/run.py` の肥大化を防ぐため、ランナー構造を「複数ウィンドウ + 複数サブシステム」を前提に再設計し、責務をモジュール分割するためのチェックリスト。
# なぜ: Parameter GUI などの機能追加で `run()` が巨大化しやすく、イベント処理/描画/終了処理の混線（点滅など）を再発しやすい構造のため。

## ゴール

- `src/api/run.py` の `run()` は「設定/配線/起動」中心で、数十行程度に保てる。
- 複数ウィンドウを扱っても、各ウィンドウの `clear → draw → flip` は 1 フレーム 1 回に統制される。
- 「イベント処理」「フレーム更新」「終了条件」「後始末」が 1 箇所に集約され、追加機能の差し込み点が明確になる。

## 非ゴール

- pyglet/imgui の backend を刷新しない（最小の責務分割に留める）。
- パラメータ解決ロジック（`ParamStore` / `parameter_context`）の仕様変更はしない。
- 高機能なスケジューラ/状態機械は作らない（複雑化しない）。

## 現状の課題（要点）

- `run()` が「初期化・イベント処理・描画・GUI・終了処理」を直列に保持しており、追加機能の置き場所が増えるほど読みにくくなる。
- “2 ウィンドウ” の前提が `run()` の制御フローへ直埋めされていて、将来ウィンドウ/サブシステムが増えると分岐が増えやすい。

## 方針（提案 / 大）

### 1) 「サブシステム」単位に分ける

各サブシステムは以下の最小 API を持つ（例）。

- `windows`: 管理対象の pyglet window（1 つ以上）
- `dispatch_events()`: 必要ならイベント処理（基本は共通ループで `wnd.dispatch_events()`）
- `draw_frame()`: そのサブシステムの描画（`flip()` まで責任を持つかは統一する）
- `close()`: 後始末（例外でも安全に呼べる）

候補サブシステム:

- DrawWindow（メイン描画）: `DrawRenderer` + `render_scene(...)`
- Parameter GUI: `ParameterGUI.draw_frame()`

### 2) ループを汎用化する

「複数ウィンドウを回して、各サブシステムの描画を順に呼ぶ」ループを `src/api/run.py` から切り出す。

- `src/app/runtime/window_loop.py` に `MultiWindowLoop` を作る
- `run()` は `MultiWindowLoop([...systems...]).run()` を呼ぶだけにする

### 3) `flip()` の責務を揃える

点滅/競合を避けるため、どちらかに統一する。

- A 案: 「共通ループが `flip()` する」= サブシステムは `clear/draw` まで（`flip` はループ一元化）
- B 案: 「各サブシステムが自分の window を `flip()` する」= 現状に近い

将来の見通しは A 案の方が強い（`flip()` の位置が 1 箇所に固定できる）。

## 実装チェックリスト

### 事前確認（このチェックが通るまでコード変更しない）

- [x] `flip()` の責務を A 案/B 案どちらにするか決める；A
- [x] 目標 FPS 制御をどうするか決める
  - `vsync` 優先で `sleep` 最小化（`time.sleep` を撤去/弱める）
  - 目標 60fps の簡易スロットリング維持（現状踏襲）；現状維持

### 実装（段階的に）

- [x] `src/app/runtime/` を新設し、ループ責務を移す
- [x] `MultiWindowLoop` を実装する
  - [x] 管理する window 群を list 化し、`None` 分岐を排除する
  - [x] `on_close` を 1 経路に束ね、どれかが閉じたら停止する
  - [x] 例外時も `close()` が必ず走るようにする（`src/api/run.py` の `finally` で close）
- [x] DrawWindow サブシステムを `src/api/run.py` から切り出す
  - [x] 初期化（window/renderer/defaults/param_store）
  - [x] `draw_frame()` 内で t を計算
  - [x] `close()`
- [x] Parameter GUI サブシステムを明示化する
  - [x] `parameter_gui=False` の場合は GUI 系 import/初期化を行わない（`run()` 内で遅延 import）
  - [x] `close()` の二重呼びが安全であること（現状踏襲）
- [x] `src/api/run.py` の `run()` を “配線” に縮退する
  - [x] `render_frame()` などの内部関数を減らし、責務をモジュールへ移す
  - [x] 引数（公開 API）は必要最小に留める（互換ラッパーは作らない）

### ドキュメント/確認

- [x] `docs/parameter_gui_runner_integration_checklist.md` の「構造」記述を新構成に合わせて更新する
- [ ] 手動スモーク確認
  - [ ] `python main.py` でプレビュー + GUI が起動する
  - [ ] GUI で値を動かすと次フレーム以降の描画に反映される
  - [ ] どちらかのウィンドウを閉じるとプロセスが終了する
  - [ ] 点滅がない（少なくとも以前の症状が再現しない）

## 追加で事前に確認したいこと（あなたに聞きたい）

- 決定: `flip()` は A 案（ループ一元化）
- 決定: FPS は現状維持（`sleep(1/60)` を残す）
- 保留: `run(..., parameter_gui=True)` の既定 ON は維持でよい？（現状は ON）

## 進め方

- このチェックリストに OK をもらったら、上から順に実装し、完了した項目へ [x] を入れて進捗を明確化する。
