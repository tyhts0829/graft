# data_root 廃止（output_dir 一本化）実装修正計画（2025-12-28）

目的: `config.yaml` の出力先設定を `output_dir` に一本化し、`data_root` を廃止して混乱要因を削る。

背景（現状の問題）:

- `data_root` は「`output_dir` 未設定時だけ `data_root/output` に出す」というフォールバック用途になっており、ユーザー視点で “どっちを触ればいいのか” が不明確。
- 入力（フォント）は `font_dirs` で別指定なので、`data_root` を “データツリーの根” として使う設計にもなっていない。

方針（今回の決定）:

- `data_root` を削除し、出力先は `output_dir` でのみ指定する。
- `output_dir` 未設定時の既定は `data/output` のままにする。

## 0) 事前に決める（あなたの確認が必要）

- [x] `config.yaml` に `data_root` が残っていた場合の挙動
  - A: 無視（静かに無効化）
  - B: 例外（`data_root` は廃止、`output_dir` を使え）で fail-fast（おすすめ）
- [x] 環境変数 `GRAFIX_DATA_ROOT` がセットされている場合の挙動
  - A: 無視
  - B: 例外で fail-fast（やや強い）

## 1) 受け入れ条件（完了の定義）

- [x] `config.yaml` のキーは `font_dirs` と `output_dir`（＋探索順/`config_path`）だけがドキュメント化されている
- [x] `output_root_dir()` が `output_dir` 未指定時に `data/output` を返す
- [x] `GRAFIX_DATA_ROOT` はドキュメントから削除され、コードでも参照されない
- [ ] `PYTHONPATH=src pytest -q` / `mypy src/grafix` / `ruff check .` が通る（`ruff check .` は既存ファイルで失敗しているため未解消）

## 2) 実装修正チェックリスト

### 2.1 runtime config から `data_root` を削除

- [x] `src/grafix/core/runtime_config.py`:
  - [x] `RuntimeConfig` から `data_root` フィールドを削除
  - [x] `runtime_config()` で `data_root` をロードしない
  - [x] `GRAFIX_DATA_ROOT` の参照を削除
  - [x] `output_root_dir()` の優先順位を `output_dir` → 既定 `data/output` の 2 段にする
  - [x] `data_root` は無視（静かに無効化）

### 2.2 出力先の説明を更新

- [x] `README.md`:
  - [x] `data_root` の説明/例/環境変数を削除
  - [x] `output_dir` のみで出力先が変わることを明記
  - [x] 「Parameter persistence の JSON を消すとリセットできる」導線は残す（`{output_dir}/param_store/*.json`）

### 2.3 参照箇所の掃除（影響調査）

- [x] `rg -n "data_root|GRAFIX_DATA_ROOT" src tests` で参照を洗い出し、残存参照を削除
- [x] `pypi_font_resource_checklist_2025-12-28.md` の “config スキーマ” 記述を `output_dir` のみに更新

### 2.4 テスト更新/追加

- [x] `tests/`:
  - [x] `runtime_config()` / `output_root_dir()` の既定挙動テストを追加（`output_dir` 未指定 →`data/output`）
  - [x] `data_root` / `GRAFIX_DATA_ROOT` が無視されることをテスト

### 2.5 スタブ/公開 API 整合（必要なら）

- [ ] 変更が公開 API に影響しないことを確認（基本は影響なし）
- [ ] もし `run()` の docstring 等を更新したら、必要に応じて `python -m tools.gen_g_stubs` を再実行

## 3) 実行コマンド（ローカル確認）

- [x] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q`
- [x] `PYTHONDONTWRITEBYTECODE=1 mypy src/grafix`
- [ ] `PYTHONDONTWRITEBYTECODE=1 ruff check .`（既存ファイルで失敗しているため未解消）
- [x] `PYTHONDONTWRITEBYTECODE=1 ruff check src/grafix/core/runtime_config.py tests/core/test_runtime_config.py`

## メモ（作業中に気づいたら追記）

- （ここに追記）
