# P キーで PNG 保存（SVG→PNG）（`data/output/png`）実装計画（2025-12-27）

## 目的

interactive プレビュー中に **P キー**で「現在フレーム」を **PNG** として `data/output/png/` に保存できるようにする。
PNG は **SVG を正（ソース）**として「SVG→PNG のラスタライズ」で生成する（方式 2）。

## 現状（関連実装）

- `src/grafix/interactive/runtime/draw_window_system.py`
  - **S キー**: SVG 保存（`data/output/svg/{script_stem}.svg`）
  - **V キー**: 動画録画トグル（ffmpeg、`data/output/video/{script_stem}.mp4`）
- `data/output/png/` ディレクトリは既に存在する。

## 仕様（確定）

- 保存先: `data/output/png/`
- ファイル名: **毎回上書き**で `{script_stem}.png`
- 画像サイズ: `canvas_size` を基準に **倍率で決める**
  - 既定: `png_scale=4.0`
  - 例: `canvas_size=(300, 300)` → `png_size=(1200, 1200)`
  - 今回の制御方法: 適切なモジュール先頭に **定数**として `PNG_SCALE: float = 4.0` を定義する
- 背景: 白（SVG 側で rect を入れる or ラスタライザ側で背景指定）

## 実装方針（SVG→PNG）

- P キー押下時は **保存要求フラグ**（例: `_pending_png_save`）を立てるだけにする。
- 実際の保存処理は **`draw_frame()` の末尾**で行う（「今描いたフレーム」の realized 結果を確実に使うため）。
- 保存手順（1 回の P 押下でやること）
  1. `export_svg(..., path=data/output/svg/{script_stem}.svg)`（既存ロジック再利用）
  2. `rasterize_svg_to_png(...)` で `data/output/png/{script_stem}.png` を生成（上書き）
- SVG→PNG ラスタライズは **`resvg` CLI** を第一候補（外部バイナリ）。見つからない場合は明確なエラーを表示する。

## 変更予定ファイル（案）

- `src/grafix/interactive/runtime/draw_window_system.py`（P キー追加 + 保存フラグ処理）
- （新規）SVG→PNG ラスタライズ
  - 案 1: `src/grafix/export/image.py`（headless / interactive から再利用しやすい）
  - 案 2: `src/grafix/interactive/runtime/png_save_system.py`（interactive 専用に閉じる）
- テスト
  - `tests/export/test_image.py`（コマンド生成 + エラー整形をモックで検証）
- （任意）`README.md` にキー操作（S/V/P）を追記

## TODO（チェックリスト）

- [ ] 出力パス生成（`script_stem` は ParamStore と同じ規則で算出）
- [ ] 適切なモジュール先頭に `PNG_SCALE: float = 4.0` を定義し、`png_size=(canvas_w*PNG_SCALE, canvas_h*PNG_SCALE)` を決める
- [ ] SVG→PNG ラスタライズ関数の実装（`resvg` 呼び出し、背景/サイズ指定、`mkdir(parents=True, exist_ok=True)`）
- [ ] `DrawWindowSystem` に `_pending_png_save` を追加
- [ ] `_on_key_press` に **P キー**を追加（フラグだけ立てる）
- [ ] `draw_frame()` 末尾で pending を処理し、`Saved PNG: {path}` を表示
- [ ] テスト追加（外部コマンドはモックして「コマンド生成」「失敗時メッセージ」を検証）
- [ ] 手動確認（実行 →P→ 生成 PNG を開き、解像度=canvas\*scale / 背景 / 見た目 を確認）

## 事前に確認したい点（あなた向け質問）

- [ ] ラスタライザは `resvg` 依存で進めて OK？（見つからなければエラー表示）；OK
- [ ] `png_scale` の既定値は 4.0 で OK？（`canvas_size=(300,300)` → `1200x1200`）；OK
