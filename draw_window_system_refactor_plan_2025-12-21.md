# DrawWindowSystem 分割リファクタ計画 / 2025-12-21

## 背景（現状）

`src/grafix/interactive/runtime/draw_window_system.py` が以下の責務を抱えて肥大化しやすい。

- style（背景色/線色/線幅）の確定（ParamStore の特殊キー読み）
- 時刻 `t` の生成（リアルタイム/録画タイムライン）
- draw 実行（sync + `realize_scene` / mp-draw）
- GL 描画（`DrawRenderer` 呼び出し + indices 生成）
- キー入力（S: SVG / V: 録画）
- 出力（SVG / Video）
- ライフサイクル（window/renderer/midi/mp-draw の close）

結果として、機能追加（ショートカット追加、録画仕様変更、mp-draw 周りの調整）で更に太りやすい。

## 目的（この改善で得たい状態）

- `DrawWindowSystem` を「配線（orchestrator）」に寄せ、ロジックを小さな部品に分割する
- unit テスト可能な“純粋関数/小クラス”を増やし、仕様の置き場所を明確にする
- 動作は維持（特に: S/V ショートカット、録画タイムライン、mp-draw 無効化方針）

## 非目的（今回やらない）

- 仕様変更（出力ファイル形式、録画のタイムライン仕様、GUI との連携ルール）
- UI 変更（Parameter GUI の表/操作）
- レンダラ（`DrawRenderer`）の刷新

## 提案する分割（ファイル案）

### 1) `src/grafix/interactive/runtime/style_resolver.py`

- 何を: ParamStore + ベース値から、そのフレームの
  - `bg_color_rgb01`
  - `global_line_color_rgb01`
  - `global_thickness`
    を返す純粋関数。
- ねらい: `draw_frame()` 冒頭の style ロジックを切り出して見通しを良くする。

### 2) `src/grafix/interactive/runtime/frame_clock.py`

- 何を: `t` を供給する最小インターフェース。
  - `RealTimeClock(start_time)` : `t = perf_counter - start_time`
  - `RecordingClock(t0, fps)` : `t = t0 + frame_index/fps`（`tick()` で frame_index++）
- ねらい: 「録画中は実時間と切り離す」仕様を、時刻供給に閉じ込める。

### 3) `src/grafix/interactive/runtime/scene_runner.py`

- 何を: `t` と `cc_snapshot` と `ParamStore` を受けて、`realized_layers` を返す担当。
  - 通常: mp-draw（有効なら）or sync
  - 録画中: sync 固定（drop を避ける）
- ねらい: draw 実行戦略（mp/sync/録画中例外）を 1 箇所に固定する。

### 4) `src/grafix/interactive/runtime/recording_system.py`

- 何を: 録画の状態・開始/停止・フレーム書き込みを担当。
  - `start(window, renderer_ctx, output_path, fps)`
  - `write_frame(renderer_ctx)`（read + `VideoRecorder.write_frame_rgb24`）
  - `stop()`（frames/fps のログもここ）
- ねらい: `DrawWindowSystem` から Video の状態変数群（fps/frame_index/t0/size/recorder）を追い出す。

### 5) （任意）`src/grafix/interactive/runtime/shortcuts.py`

- 何を: `on_key_press(symbol, modifiers)` を受け、アクション（SVG/録画）を呼ぶ。
- ねらい: ショートカットを増やすときに `DrawWindowSystem` を太らせない。

## 実装チェックリスト（コード変更前提）

- [x] `DrawWindowSystem` の外へ出す責務を確定（上の 1〜4 を採用するか）
- [x] `style_resolver.py` を追加し、`draw_window_system.py` から style 決定を移植
- [x] `frame_clock.py` を追加し、`t` 算出（通常/録画）を移植
- [x] `scene_runner.py` を追加し、sync/mp-draw/録画中の分岐を移植
- [x] `recording_system.py` を追加し、録画状態/処理を移植
- [x] `DrawWindowSystem` を「組み立て + 1 フレームの順序制御」のみに整理
- [x] import 依存を整理（循環参照が出ないことを確認）
- [x] 既存 API を維持するか決める（`save_svg()`/`start_video_recording()` 等）
- [x] テスト追加/更新（最小）
  - [x] `tests/interactive/runtime/test_video_recorder.py` は維持
  - [x] 新規 unit テストを追加（`frame_clock` / `style_resolver`）
- [ ] 手動動作確認（描画 + S 保存 + V 録画 + 停止時ログ）

## 検証コマンド

- `PYTHONPATH=src pytest -q tests/interactive/runtime/test_video_recorder.py`
- `PYTHONPATH=src pytest -q tests/core/parameters/test_site_id.py`
- `ruff check .`（環境に ruff がある場合）
- `mypy src/grafix`（リポ全体は現状エラーが出やすいので、必要なら対象限定で）

## リスク / 注意点

- 分割後に import が循環しやすい（runtime 内の依存方向を固定する必要がある）
- 例外時の close 順序（録画中/GUI 例外時でも ffmpeg が残らない）を維持する

## 事前確認（あなたに確認したいこと）

- `DrawWindowSystem` の公開メソッド（`save_svg` / `start_video_recording` / `stop_video_recording`）は維持したい？（破壊的変更も可）；破壊的変更も可（クリーン優先）
- ファイル分割は上の 1〜4 で良い？（shortcuts 分離は後回しでも可）；はい
