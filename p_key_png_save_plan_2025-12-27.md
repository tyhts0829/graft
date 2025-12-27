# P キーで PNG 保存（`data/output/png`）実装計画（2025-12-27）

## 目的

interactive プレビュー中に **P キー**で「現在フレーム」を **PNG** として `data/output/png/` に保存できるようにする。

## 現状（関連実装）

- `src/grafix/interactive/runtime/draw_window_system.py`
  - **S キー**: SVG 保存（`data/output/svg/{script_stem}.svg`）
  - **V キー**: 動画録画トグル（ffmpeg、`data/output/video/{script_stem}.mp4`）
- `data/output/png/` ディレクトリは既に存在する。

## 仕様案（先に確定したい）

- 保存先: `data/output/png/`
- ファイル名規則（候補）
  - A) 複数枚を残す: `{script_stem}_{YYYYmmdd_HHMMSS}_{time_ns}.png`
  - B) 毎回上書き: `{script_stem}.png`；こちらで
- 解像度: **framebuffer size**（retina 対応で `get_framebuffer_size()` があればそれを優先）
- 画素: RGB8（背景込み）
- 画像の向き: **上下反転して保存**（動画録画は ffmpeg の `vflip` を前提にしているため、同様に補正する）

## 実装方針

- P キー押下時は **保存要求フラグ**（例: `_pending_png_save`）を立てるだけにし、実際の `screen.read()` は **`draw_frame()` の末尾（描画後〜flip 前）**で行う。
  - キーハンドラ内で即 `read()` すると front/back buffer のタイミング差で「古いフレーム」を掴む可能性があるため。
- PNG 書き込みの実装（どちらにするか要確認）
  - 方針 A（推奨）: **追加依存なし**で最小 PNG writer を実装（標準ライブラリの `zlib` / `struct` / `crc32`）。
  - 方針 B: **ffmpeg** を 1 回起動して 1 フレーム PNG 化（動画録画と同じ依存だが、起動オーバーヘッドあり）。

## 変更予定ファイル（案）

- `src/grafix/interactive/runtime/draw_window_system.py`（P キー追加 + 保存フラグ処理）
- （新規）PNG 書き込み
  - 案 1: `src/grafix/export/png.py`（他 export と同列で再利用しやすい）
  - 案 2: `src/grafix/interactive/runtime/png_capture.py`（interactive 専用に閉じる）
- テスト
  - `tests/export/test_png.py`（PNG writer を純粋関数化する場合）
  - もしくは `tests/interactive/runtime/test_png_capture.py`
- （任意）`README.md` にキー操作（S/V/P）を追記

## TODO（チェックリスト）

- [ ] 仕様確定（ファイル名規則、PNG 実装方針 A/B、上下反転の扱い）
- [ ] 出力パス生成（`script_stem` は ParamStore と同じ規則で算出）
- [ ] PNG 保存関数の実装（RGB24 → PNG、`mkdir(parents=True, exist_ok=True)`）
- [ ] `DrawWindowSystem` に `_pending_png_save`（必要なら `_pending_png_path`）を追加
- [ ] `_on_key_press` に **P キー**を追加（フラグだけ立てる）
- [ ] `draw_frame()` 末尾で pending を処理し、`Saved PNG: {path}` を表示
- [ ] テスト追加（最小ケースで PNG 構造と復号後 scanline を検証。vflip も含める）
- [ ] 手動確認（実行 →P→ 生成ファイルを開き、向き/解像度/色を確認）

## 事前に確認したい点（あなた向け質問）

- [ ] ファイル名は「毎回上書き」か「都度増える」どちらが良い？
- [ ] PNG 保存は ffmpeg 依存でも良い？（「PNG だけは ffmpeg 無しでも動く」必要がある？）
- [ ] 透過（alpha）は当面不要で OK？（今は RGB 固定で進める）
