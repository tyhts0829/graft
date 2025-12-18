# Phase 2（mp-draw）実装チェックリスト（2025-12-17）

目的: `draw(t)` が支配的なスケッチで、メイン（イベント処理 + GL）を詰まらせずに FPS を改善する。

前提:

- 通信は `multiprocessing.Queue`（pickle）を使う。
- `spawn` 前提（macOS/Windows）。
  - 子プロセスに渡す `draw` は picklable（モジュールトップレベル定義）。
  - スケッチ側は `if __name__ == "__main__":` ガード必須。
- ワーカーは CPU 計算のみ（pyglet/pyimgui/OpenGL は触らない）。

## 実装

- [x] `run(..., n_worker=...)` を追加する（`<=1` は無効、`>=2` で mp-draw を有効化）
- [x] ワーカー用に `parameter_context(snapshot)` 相当を追加する（`ParamStore` を持たずに `FrameParamsBuffer` を回収する）
- [x] API の `set_api_label` を「store が無い場合は frame_params に記録」へ拡張する（ワーカーから label を返せるように）
- [x] ワーカーが返した `FrameParamRecord`/label をメインでマージし、Parameter GUI を壊さない
- [x] result queue を drain して “最新結果優先” で採用する（古い結果は捨てる）
- [x] 例外をメインへ伝播する（traceback を含める）
- [x] 終了時にワーカーを確実に停止する（close で join）

## 最小検証（手元計測）

- [ ] `cpu_draw` でメインフレーム時間が改善する（`GRAFT_SKETCH_CASE=cpu_draw ... GRAFT_SKETCH_N_WORKER=4 ...`）
- [ ] 既存の単プロセス（`n_worker<=1`）挙動が壊れていない
