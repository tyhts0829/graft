# grafix を typed package 化する（py.typed）実装計画（2025-12-27）

目的: `sketch/readme.py` などから `import grafix` したときに出る mypy 警告 `Skipping analyzing "grafix": module is installed, but missing library stubs or py.typed marker` を解消し、`grafix` の型解析を有効化する（PEP 561）。

非目的:

- `grafix` 全体の型エラーをゼロにする（必要なら別計画で実施）
- 互換ラッパー/シムの追加
- 依存追加（ネットワークが必要な作業）

## 0) 前提（現状）

- パッケージ本体: `src/grafix/`（import 名は `grafix`）
- ビルド: `pyproject.toml` + setuptools
- mypy は `grafix` を「インストール済みだが型情報なし」と見なして解析をスキップしている

## 1) 仕様の決定事項（あなたの確認が必要）

- [x] (1-1) `grafix` を typed package として宣言する（`py.typed` を追加する）
  - A: はい（推奨）。`grafix` 内の解析が有効になる；はい
  - B: いいえ。今回は `sketch/readme.py` 側の `# type: ignore[import-untyped]` 等で黙らせる
- [x] (1-2) `py.typed` を配布物（wheel/sdist）へ確実に含めるため、`pyproject.toml` に package-data を追加する
  - A: はい（推奨）。`pip install .` でも確実に反映される；はい
  - B: いいえ（当面 editable install 前提で進める）
- [x] (1-3) `py.typed` 追加後に新規に出る可能性がある mypy エラーの扱い
  - A: 今回は「警告を消す」まで（新規エラーは別途）；はい
  - B: 主要なエラーはこの場で直す（対象モジュールを先に決める）

## 2) 実装チェックリスト（承認後に実施）

### 2.1 マーカー追加

- [x] `src/grafix/py.typed` を追加（空ファイル）

### 2.2 パッケージング設定（配布物に含める）

- [x] `pyproject.toml` に以下を追加
  - `[tool.setuptools.package-data]`
  - `grafix = ["py.typed"]`

### 2.3 動作確認（対象限定）

- [x] `mypy sketch/readme.py` で当該 warning が消えることを確認する
- [x] `PYTHONPATH=src mypy sketch/readme.py` でも同様に確認する（ローカル参照の確認）
- [ ] （必要なら）インストールし直す
  - editable: `pip install -e .`
  - 非 editable: `pip install .`

## 3) 観測ポイント（実装中に追記）

- [x] `py.typed` 追加で mypy の解析範囲が増え、想定外にノイズが増えないか（増える場合は別計画化）
  - `mypy sketch/readme.py` の範囲では新規エラーなし（warning も消えた）
- [ ] `tool.mypy.ignore_missing_imports=true` のままで十分か（外部依存の型事情に応じて後日見直し）
