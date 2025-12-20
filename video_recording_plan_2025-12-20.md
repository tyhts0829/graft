# Video録画機能（Vキーで録画開始/終了） 実装計画

目的: interactive 描画中に `V` キーで録画をトグルし、`data/output/video/` に動画ファイルを保存できるようにする。

## Requirements（要件）
- 描画中に `V` キーで録画開始、もう一度 `V` キーで録画終了
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
- `src/grafix/interactive/runtime/window_loop.py`（基本は触らない想定。必要なら「flip前キャプチャ」の調整）
- `src/grafix/api/run.py`（必要なら GUI ウィンドウ側にも V トグル配線）
- 追加: `src/grafix/interactive/runtime/video_recorder.py`（録画器: ffmpeg 起動/フレーム投入/終了処理）

## Design（方針）
- 実装は「ffmpeg に raw RGB を stdin で流す」方式を第一候補にする（中間 PNG 書き出しを避けてシンプルに）。
- 出力拡張子はまず `.mp4`（H.264）を既定とし、必要なら変更できるようにする。
- キャプチャ解像度は `window.get_framebuffer_size()` を基準にする（Retina での実ピクセルを優先）。
- OpenGL の上下反転問題は、まず ffmpeg 側の `vflip` で吸収する（必要なら実機で確認して調整）。

## Action items（チェックリスト）
- [ ] 出力仕様の確定（拡張子/コーデック/固定FPSの値）
  - 既定案: `{script_stem}.mp4` / 60fps / H.264（`libx264`）
- [ ] `VideoRecorder`（新規）を実装
  - [ ] `start(output_path, size, fps)` で `ffmpeg` を `subprocess.Popen` で起動（stdin=PIPE）
  - [ ] `write_frame_rgb24(frame_bytes)` で 1 フレーム投入（不足時は例外で止める）
  - [ ] `stop()` で stdin close + wait、終了コード異常時は例外/メッセージ
  - [ ] 出力先 `data/output/video/` を `mkdir(parents=True, exist_ok=True)`
- [ ] `DrawWindowSystem` に録画状態を追加して V トグルを実装
  - [ ] `on_key_press` で `key.V` を検知し、未録画→start / 録画中→stop
  - [ ] `draw_frame()` の末尾で「録画中なら framebuffer を read して 1 フレーム書く」
  - [ ] `close()` で録画中なら必ず stop（例外でもプロセスが残らないように）
- [ ] （任意）Parameter GUI 側でも V を有効化するか判断し、必要なら `run.py` へ配線追加
- [ ] 最小テスト追加（ユニット中心）
  - [ ] 出力パス生成（`script_stem` / `data/output/video` / 拡張子）のテスト
  - [ ] ffmpeg コマンド組み立てのテスト（起動自体は integration 扱いでも良い）
- [ ] 手動検証
  - [ ] `python sketch/...py` を起動 → `V` → 数秒待つ → `V` で停止
  - [ ] `data/output/video/{script_stem}.mp4` が生成され、再生できること

## Testing / Validation（確認コマンド）
- `PYTHONPATH=src pytest -q`
- `ruff check .`
- `mypy src/grafix`

## Risks / Edge cases（注意点）
- `ffmpeg` が未インストールの場合の挙動（起動失敗をどう扱うか）
- 録画中の負荷（フレーム read が重いと FPS が落ちる可能性）
- Retina 等での解像度/上下反転（`get_framebuffer_size` と `vflip` の整合）

## Open questions（要確認）
- 出力拡張子は `.mp4` で固定で良い？（`.webm` 等が良いなら変更）
- 録画 FPS は 60 固定で良い？（`MultiWindowLoop(fps=60.0)` に追従）
- `V` トグルは「描画ウィンドウのみ」か「GUI ウィンドウからも」必要？

