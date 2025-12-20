# grafix → grafix リネーム作業チェックリスト（2025-12-18）

目的: pip 配布名（`pyproject.toml` の `project.name`）と import 名（`import grafix`）を、両方とも `grafix` に統一する。

注意: 互換ラッパー/シム（`grafix` を残す等）は作らない。

## 0) 事前に決める（あなたの確認が必要）

- [x] 置換スコープ（ドキュメント）:
  - A: README/architecture/spec 等の「現行ドキュメントのみ」を `grafix` に更新（推奨）；こちらで。
  - B: `docs/done/` など履歴も含め、全ドキュメントを一括で `grafix` に更新（差分が大きくなる）
- [x] ログ/識別子の文字列も改名するか:
  - 例: `"grafix-mp-draw-"`, `"[grafix-perf]"` を `grafix` にする/しない；する。

## 1) パッケージ設定（pyproject）

- [x] `pyproject.toml` の `[project] name` を `grafix` に変更
- [x] `pyproject.toml` の setuptools 設定を更新（`include = ["grafix*"]`）
- [x] （もし存在すれば）entrypoint/console_scripts 等の名前も `grafix` に統一（現状なし）

## 2) ディレクトリの rename（破壊的・Ask-first）

- [x] `src/grafix/` → `src/grafix/` に移動（`git mv` 相当）
- [x] 先頭ヘッダコメント等のパス表記（例: `src/grafix/...`）を `src/grafix/...` に更新

## 3) import / 参照の更新

- [x] `src/` 内の `import grafix` / `from grafix ...` を `grafix` に置換
- [x] `tests/` 内の import を `grafix` に置換
- [x] `sketch/` 内の import を `grafix` に置換
- [x] `tools/` 内の import を `grafix` に置換
- [x] 文字列参照（ログ prefix 等）を、0) の決定に従って更新

## 4) テスト（ハードコードされている想定の更新）

- [x] `tests/architecture/test_dependency_boundaries.py` の `src/grafix` や `grafix.*` を `grafix` に更新
- [x] `tests/**` の import パス期待値があれば更新

## 5) ドキュメント/運用手順の更新

- [x] `README.md` の import 例（`from grafix.api ...`）を `grafix` に更新
- [x] `architecture.md` 等の主要ドキュメントの `grafix` 参照を更新（0) のスコープに従う）
- [x] `docs/memo/performance.md` のパス/ログ例（`src/grafix/...`, `[grafix-perf]`）を `grafix` に更新
- [x] `AGENTS.md` の Build/Test/Style（例: `src/grafix/`, `mypy src/grafix`）を `grafix` に更新

## 6) 動作確認（ローカル）

- [ ] `PYTHONPATH=src python -c "import grafix; import grafix.api"`
- [ ] `PYTHONPATH=src pytest -q`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`

## 7) 仕上げ

- [ ] `grafix` の残骸確認（0) の方針に合わせて検索範囲を調整）
  - 例: `rg -n "\\bgrafix\\b" pyproject.toml README.md src tests sketch tools`
- [ ] `pip install -e .` 済みの venv を使っている場合、手元で再インストール（パスが変わるため）
