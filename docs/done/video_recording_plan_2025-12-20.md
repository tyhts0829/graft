# Video 録画機能（V キーで録画開始/終了） 実装計画

目的: interactive 描画中に `V` キーで録画をトグルし、`data/output/video/` に動画ファイルを保存できるようにする。

## Requirements（要件）

- 描画中に `V` キーで録画開始、もう一度 `V` キーで録画終了
- ショートカット（`S`: SVG 保存 / `V`: 録画トグル）は「描画ウィンドウにフォーカスがあるときのみ」有効（Parameter GUI では無効）
- 録画データは指定 FPS の固定フレームレート（CFR）で、各フレームは必ず再レンダリングする（不足分を重複フレームで穴埋めしない）
  - 録画中は描画/操作が遅くなっても良い（録画生成が実時間より長くかかっても良い）
  - 結果として「動画時間」は `frames / fps` で決まり、壁時計の経過時間とは一致しない
- 出力フォルダ: `data/output/video`
- ファイル名: SVG と同じく `{script_stem}.{拡張子}`
  - `script_stem` は `default_param_store_path(draw).stem` と同一の算出規則

## Scope（範囲）

- In:
  - `grafix.api.run()` で起動する interactive 描画ウィンドウの録画
- Out:
  - 音声録音
  - ヘッドレス export API への動画追加
  - 可変 FPS / タイムスタンプ同期（当面は固定 FPS）

## Files / Entry points（触る場所）

- `src/grafix/interactive/runtime/draw_window_system.py`（キー入力 + フレームキャプチャの呼び出し）
- `src/grafix/interactive/runtime/window_loop.py`（基本は触らない想定。必要なら「flip 前キャプチャ」の調整）
- `src/grafix/api/run.py`（Parameter GUI 側の `S` ショートカット配線を撤去し、誤爆を防ぐ）
- 追加: `src/grafix/interactive/runtime/video_recorder.py`（録画器: ffmpeg 起動/フレーム投入/終了処理）

## Design（方針）

- 実装は「ffmpeg に raw RGB を stdin で流す」方式を第一候補にする（中間 PNG 書き出しを避けてシンプルに）。
- 出力拡張子はまず `.mp4`（H.264）を既定とし、必要なら変更できるようにする。
- キャプチャ解像度は `window.get_framebuffer_size()` を基準にする（Retina での実ピクセルを優先）。
- OpenGL の上下反転問題は、まず ffmpeg 側の `vflip` で吸収する（必要なら実機で確認して調整）。
- CFR 維持のため、録画中は `draw(t)` に渡す `t` を「録画タイムライン」に切り替える。
  - `t = t0 + frame_index / fps` を毎フレーム進めて必ず再レンダリングし、その結果を 1 フレームとして書き込む。
  - 描画が重い場合は生成に時間が掛かるが、出力動画は重複なしで滑らかに進む。
- 録画中は `mp-draw`（multiprocessing）を使わず同期実行する（drop による欠落/不規則な t を避ける）。

## Action items（チェックリスト）

- [x] ショートカット方針を反映（保存/録画は描画ウィンドウのみで発火）
  - [x] Parameter GUI 側の `S`（SVG 保存）配線を削除し、描画ウィンドウ `S` のみに統一
  - [x] `V`（録画トグル）は描画ウィンドウ側のみで実装（GUI には配線しない）
- [x] 出力仕様の確定（拡張子/コーデック/固定 FPS の値）
  - 既定案: `{script_stem}.mp4` / 60fps / H.264（`libx264`）
- [x] `VideoRecorder`（新規）を実装
  - [x] `ffmpeg` を `subprocess.Popen` で起動（stdin=PIPE）
  - [x] `write_frame_rgb24(frame_bytes)` で 1 フレーム投入
  - [x] `close()` で stdin close + wait、終了コード異常時は例外
  - [x] 出力先 `data/output/video/` を `mkdir(parents=True, exist_ok=True)`
- [x] `DrawWindowSystem` に録画状態を追加して V トグルを実装
  - [x] `on_key_press` で `key.V` を検知し、未録画 →start / 録画中 →stop
  - [x] 録画中の `t` は録画タイムラインへ切り替える（`t = t0 + frame_index / fps`）
  - [x] `draw_frame()` の末尾で「録画中なら framebuffer を read して 1 フレーム書く」→ `frame_index += 1`
  - [x] start/stop 時に、録画 fps / 書き込んだ frames / 動画秒数（`frames/fps`）をログ出力
  - [x] `close()` で録画中なら必ず stop
- [x] 最小テスト追加（ユニット中心）
  - [x] 出力パス生成（`script_stem` / `data/output/video` / 拡張子）のテスト
  - [x] ffmpeg コマンド組み立てのテスト
- [ ] 手動検証
  - [ ] `python sketch/...py` を起動 → `V` → 目的の動画秒数になるまで待つ（ログで `frames/fps` を確認）→ `V` で停止
  - [ ] `data/output/video/{script_stem}.mp4` が生成され、再生できること

## Testing / Validation（確認コマンド）

- `PYTHONPATH=src pytest -q`
- `ruff check .`
- `mypy src/grafix`

## Risks / Edge cases（注意点）

- `ffmpeg` が未インストールの場合の挙動（起動失敗をどう扱うか）
- 録画中の負荷（フレーム read / エンコードが重いと描画 FPS が落ちる可能性）
- 録画中はプレビューの進行が実時間より遅く見える（動画は滑らかだが、生成に時間が掛かる）
- 録画中は mp-draw を無効化するため、重いスケッチだとさらに遅くなる可能性がある
- Retina 等での解像度/上下反転（`get_framebuffer_size` と `vflip` の整合）
- Parameter GUI にフォーカスがある間はショートカットが効かない（誤爆防止の仕様）

## Open questions（要確認）

- 出力拡張子は `.mp4` で固定で良い？（`.webm` 等が良いなら変更）；.mp4 で OK
- 録画 FPS は 60 固定で良い？（`MultiWindowLoop(fps=60.0)` に追従）；run 関数の引数で指定した値で制御されてほしい。
- `V` トグルは「描画ウィンドウのみ」か「GUI ウィンドウからも」必要？；GUI のテキスト入力誤爆を避けるため、描画ウィンドウのみで固定する。
- 録画中は「動画時間 ≠ 実時間」で良い？；滑らかさ優先のため OK（オフライン生成扱い）。
