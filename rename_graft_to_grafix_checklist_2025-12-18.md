# graft → grafix リネーム作業チェックリスト（2025-12-18）

目的: pip 配布名（`pyproject.toml` の `project.name`）と import 名（`import graft`）を、両方とも `grafix` に統一する。

注意: 互換ラッパー/シム（`graft` を残す等）は作らない。

## 0) 事前に決める（あなたの確認が必要）

- [ ] 置換スコープ（ドキュメント）:
  - A: README/architecture/spec 等の「現行ドキュメントのみ」を `grafix` に更新（推奨）
  - B: `docs/done/` など履歴も含め、全ドキュメントを一括で `grafix` に更新（差分が大きくなる）
- [ ] ログ/識別子の文字列も改名するか:
  - 例: `"graft-mp-draw-"`, `"[graft-perf]"` を `grafix` にする/しない

## 1) パッケージ設定（pyproject）

- [ ] `pyproject.toml` の `[project] name` を `grafix` に変更
- [ ] `pyproject.toml` の setuptools 設定を更新（`include = ["grafix*"]`）
- [ ] （もし存在すれば）entrypoint/console_scripts 等の名前も `grafix` に統一

## 2) ディレクトリの rename（破壊的・Ask-first）

- [ ] `src/graft/` → `src/grafix/` に移動（`git mv` 相当）
- [ ] 先頭ヘッダコメント等のパス表記（例: ``src/graft/...``）を `src/grafix/...` に更新

## 3) import / 参照の更新

- [ ] `src/` 内の `import graft` / `from graft ...` を `grafix` に置換
- [ ] `tests/` 内の import を `grafix` に置換
- [ ] `sketch/` 内の import を `grafix` に置換
- [ ] `tools/` 内の import を `grafix` に置換
- [ ] 文字列参照（ログ prefix 等）を、0) の決定に従って更新

## 4) テスト（ハードコードされている想定の更新）

- [ ] `tests/architecture/test_dependency_boundaries.py` の `src/graft` や `graft.*` を `grafix` に更新
- [ ] `tests/**` の import パス期待値があれば更新

## 5) ドキュメント/運用手順の更新

- [ ] `README.md` の import 例（`from graft.api ...`）を `grafix` に更新
- [ ] `architecture.md` 等の主要ドキュメントの `graft` 参照を更新（0) のスコープに従う）
- [ ] `AGENTS.md` の Build/Test/Style（例: `src/graft/`, `mypy src/graft`）を `grafix` に更新

## 6) 動作確認（ローカル）

- [ ] `PYTHONPATH=src python -c "import grafix; import grafix.api"`
- [ ] `PYTHONPATH=src pytest -q`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`

## 7) 仕上げ

- [ ] `graft` の残骸確認（0) の方針に合わせて検索範囲を調整）
  - 例: `rg -n "\\bgraft\\b" pyproject.toml README.md src tests sketch tools`
- [ ] `pip install -e .` 済みの venv を使っている場合、手元で再インストール（パスが変わるため）

