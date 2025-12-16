# 標準的な src レイアウトへの移行計画（実施）

目的: `src` を「入れ物」にし、実際に import されるパッケージ名を `graft` に統一する。

注意: 互換ラッパー/シムは作らない（`from src...` は完全に廃止）。移行後は `pip install -e .` 等の「インストール前提」で動かす。

---

## 移行前のディレクトリ構造（参考）

```text
.
├── main.py
├── src/                # 現状: ここ自体が import 対象のパッケージ名 "src"
│   ├── __init__.py
│   ├── api/
│   ├── core/
│   ├── export/
│   └── interactive/
├── tests/
│   ├── api/
│   └── core/
├── docs/
├── data/
├── tools/
├── sketch/
└── README.md
```

---

## 移行後のディレクトリ構造（現在）

```text
.
├── pyproject.toml
├── src/
│   └── graft/           # ここが import 対象のパッケージ名 "graft"
│       ├── __init__.py
│       ├── api/
│       ├── core/
│       ├── export/
│       └── interactive/
├── tests/
│   ├── api/
│   └── core/
├── docs/
├── data/
├── tools/
├── sketch/
│   ├── main.py
│   └── 251214.py
└── README.md
```

---

## 改善アクション（チェックリスト）

### 0) 事前合意（ここだけ先に確認）

- [x] パッケージ名は `graft` で確定する
- [x] 互換 import（`src`）は残さない方針で合意する
- [x] エントリポイントは `sketch/main.py` へ移す（旧 `main.py` は削除）

### 1) パッケージングの土台を作る

- [x] `pyproject.toml` を追加し、src レイアウトのパッケージング設定を入れる
- [x] 依存（numpy 等）を `pyproject.toml` に記述する（interactive/shapely は extras）
- [x] `pytest/ruff/mypy` の設定置き場を `pyproject.toml` に統一し、最小設定を追加する

### 2) コードを `src/graft/` へ移動する（破壊的）

- [x] `src/graft/` を作成する
- [x] `src/api -> src/graft/api` に移動する
- [x] `src/core -> src/graft/core` に移動する
- [x] `src/export -> src/graft/export` に移動する
- [x] `src/interactive -> src/graft/interactive` に移動する
- [x] `src/__init__.py` を削除する（`src` は入れ物にする）
- [x] `src/graft/__init__.py` を追加する（公開情報は最小）

### 3) import を全面的に更新する（`src` -> `graft`）

- [x] コード/テスト/主要ドキュメントで `from src...` / `import src...` を `from graft...` / `import graft...` に置換する
- [x] `graft.api` の公開 API（`E/G/L/run` 等）の import パスを確定する
- [x] 絶対 import（`graft.*`）へ統一する

### 4) 実行/テスト/ドキュメントを更新する

- [x] `main.py` を `sketch/main.py` へ移動し、import を `from graft.api ...` に更新する
- [x] `README.md` のサンプルを `from graft.api ...` に更新する
- [x] `tests/` の import を `from graft...` に更新する
- [x] 実行手順を README に明記する（例: `pip install -e .` → `python sketch/main.py`）

### 5) 検証（最小）

- [x] `python -c "import graft; import graft.api"` が通る（`PYTHONPATH=src` で確認）
- [x] `pytest -q` が通る（`PYTHONPATH=src pytest -q` で確認）
- [ ]（任意）`ruff` / `mypy` を対象限定で回し、致命的な型/静的解析エラーを潰す

---

## 事前確認したいこと（追加で気づいたら追記する）

- [ ] `graft` という名前を将来 PyPI に出す予定はあるか（同名パッケージ衝突を気にするか）
- [ ] 実行の標準フローはどれにするか
  - A: `pip install -e .` 前提（推奨）
  - B: `PYTHONPATH=src` 前提（インストール不要だが運用が雑になりがち）
