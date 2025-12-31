# Output（benchmarks 以外）を「sketch 構造」でミラー + run_id（上書き）実装チェックリスト (2025-12-31)

## 背景 / 目的（pip install ユーザーも想定）

- 現状は出力ファイル名が `script_stem` だけなので、同名ファイルがあると衝突しやすい（例: `a/readme.py` と `b/readme.py`）。
- 作品（ユーザースクリプト）側で階層化した構造を、`output/`（benchmarks 以外）側にも反映して整理したい。
- `run(draw, run_id="...")` を導入し、同一 `run_id` は同一パスへ上書き保存したい。
- `pip install grafix` のユーザーに `sketch/` ディレクトリを強制しない（無設定でも動く）形にしたい。

## 仕様（あなたの意図 + pip install UX）

### ルール

- `output_root` は既存の `runtime_config.paths.output_dir`（例: `data/output`）。
- `output_root/benchmarks/` は対象外（現状維持）。
- `output_root/{kind}/` の **kind 配下**で、`sketch_dir` 配下のサブディレクトリ構造をミラーする。
  - 対象 kind 例: `param_store`, `png`, `svg`, `video`, `midi`
- ファイル名は `{script_stem}{suffix}.{ext}`。
  - `script_stem`: draw 定義元ファイルの stem（例: `readme.py` → `readme`）
  - `suffix`: `run_id` 未指定なら空、指定なら `_{run_id}`（例: `_v1`）
  - `run_id` はファイル名に安全に入るように正規化する（英数 + `._-` 以外は `_`）
- `run_id` が同じなら同じパスになるので、単純に上書き（期待通り）。

### sketch_dir の決め方（sketch を必須にしない）

- `config.yaml` の `paths.sketch_dir` が未設定でも動く（pip ユーザーは必須指定なし）。
- `paths.sketch_dir` が設定され、かつ draw の定義元がその配下にある場合に限りミラーする。

### 具体例（ParamStore）

- （`paths.sketch_dir: "sketch"` の例）`sketch/readme.py`
  - `output_root/param_store/readme.json`
  - `run_id="v1"` → `output_root/param_store/readme_v1.json`
- `sketch/folder1/readme.py`
  - `output_root/param_store/folder1/readme.json`
  - `run_id="v1"` → `output_root/param_store/folder1/readme_v1.json`

### 他フォーマットも同様（例）

- `sketch/folder1/readme.py`
  - SVG: `output_root/svg/folder1/readme.svg`（または `readme_v1.svg`）
  - PNG: `output_root/png/folder1/readme.png`（または `readme_v1.png`）
  - Video: `output_root/video/folder1/readme.mp4`（または `readme_v1.mp4`）

### ミラーできない draw（フォールバック: 常に動く）

- draw 定義元が sketch_dir 配下と判定できない場合:
  - `output_root/{kind}/misc/{script_stem}{suffix}.{ext}`

## 実装チェックリスト

- [x] 作業開始時点の差分を確認し、依頼範囲外の差分は触らない（`git status --porcelain`）
- [x] 公開 API `run()` に `run_id: str | None = None` を追加する
  - 対象: `src/grafix/api/runner.py`
  - 対象: `src/grafix/api/__init__.pyi`
- [x] `config.yaml` に `paths.sketch_dir`（任意）を追加し、runtime_config で読めるようにする
  - 対象: `src/grafix/core/runtime_config.py`
  - 対象: `src/grafix/resource/default_config.yaml`（キー説明のみ追加、既定値は無し）
  - 対象: `tests/core/test_runtime_config.py`
- [x] 出力パス生成ロジックを 1 箇所に集約する（kind + ext + run_id + sketch 相対 dir）
  - 例: `src/grafix/core/output_paths.py`
- [x] ParamStore の既定保存パスを「param_store/<sketch 相対 dir>/stem[_run_id].json」へ変更する
  - 対象: `src/grafix/core/parameters/persistence.py`
  - 影響: `src/grafix/api/runner.py`（load/save）
- [x] interactive 出力（SVG/PNG/Video）の既定保存パスを同様に変更する
  - 対象: `src/grafix/interactive/runtime/draw_window_system.py`
  - 対象: `src/grafix/export/image.py`
  - 対象: `src/grafix/interactive/runtime/video_recorder.py`
- [x] MIDI 保存（CC snapshot / profile）を run_id に追従させて反映する
  - 対象: `src/grafix/api/runner.py`
  - 対象: `src/grafix/interactive/midi/factory.py`
- [x] テスト追加/更新（compile の filename を使って sketch_dir 配下のパスを擬似的に作る）
  - 対象: `tests/core/parameters/test_persistence.py`
- [x] 動作確認（短く）
  - `PYTHONPATH=src pytest -q tests/core/test_runtime_config.py tests/core/parameters/test_persistence.py`
  - `ruff` は手元環境に無いので未実行

## 要確認（あなたの回答待ち）

- [x] `run_id` の接尾辞は `_{run_id}` 固定で良い（OK）
- [x] run_id は ParamStore だけでなく PNG/SVG/Video/MIDI 全部に効かせる（全部）
- [x] 新規 config キー名は `paths.sketch_dir` とする
- [x] 自動検出は入れない
- [x] フォールバックは `output_root/{kind}/misc/...` とする
