# 標準的な src レイアウトへの移行計画（案）

目的: `src` を「入れ物」にし、実際に import されるパッケージ名を `graft` に統一する。

注意: 互換ラッパー/シムは作らない（`from src...` は完全に廃止）。移行後は `pip install -e .` 等の「インストール前提」で動かす。

---

## 現状のディレクトリ構造（要点）

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

## 目標のディレクトリ構造（全体）

```text
.
├── pyproject.toml
├── main.py              # 必要なら残す（例/スケッチ用途）
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
└── README.md
```

---

## 改善アクション（チェックリスト）

### 0) 事前合意（ここだけ先に確認）

- [ ] パッケージ名は `graft` で確定する（別名にするならここで決める）；OK
- [ ] 互換 import（`src`）は残さない方針で合意する;ok
- [ ] `main.py` の扱いを決める（残す / `examples/` や `sketch/` へ移す）;sketch へ移動

### 1) パッケージングの土台を作る

- [ ] `pyproject.toml` を追加し、`package-dir = {"" = "src"}`（または同等）で src レイアウトを有効化する
- [ ] 依存（numpy 等）を `pyproject.toml` に記述する（現状 README 記載をソースにする）
- [ ] `pytest/ruff/mypy` の設定置き場を決め、最小の設定を追加する（`pyproject.toml` か個別設定ファイル）

### 2) コードを `src/graft/` へ移動する（破壊的）

- [ ] `src/graft/` を作成する
- [ ] `src/api -> src/graft/api` に移動する
- [ ] `src/core -> src/graft/core` に移動する
- [ ] `src/export -> src/graft/export` に移動する
- [ ] `src/interactive -> src/graft/interactive` に移動する
- [ ] `src/__init__.py` を削除する（`src` は入れ物にする）
- [ ] `src/graft/__init__.py` を追加する（公開情報は最小でよい）

### 3) import を全面的に更新する（`src` -> `graft`）

- [ ] リポジトリ全体で `from src...` / `import src...` を `from graft...` / `import graft...` に置換する
- [ ] `graft.api` の公開 API（`E/G/L/run` 等）の import パスを確定する
- [ ] 相対 import に寄せるか（`from ..core ...`）絶対 import に統一するか決めて合わせる

### 4) 実行/テスト/ドキュメントを更新する

- [ ] `main.py` の import を `from graft.api ...` に更新する
- [ ] `README.md` のサンプルを `from graft.api ...` に更新する
- [ ] `tests/` の import を `from graft...` に更新する
- [ ] 実行手順を README に明記する（例: `pip install -e .` → `python main.py`）

### 5) 検証（最小）

- [ ] `python -c "import graft; import graft.api"` が通る
- [ ] `pytest -q` が通る（失敗したら import/path 起因かを切り分ける）
- [ ]（任意）`ruff` / `mypy` を対象限定で回し、致命的な型/静的解析エラーを潰す

---

## 事前確認したいこと（追加で気づいたら追記する）

- [ ] `graft` という名前を将来 PyPI に出す予定はあるか（同名パッケージ衝突を気にするか）
- [ ] 実行の標準フローはどれにするか
  - A: `pip install -e .` 前提（推奨）
  - B: `PYTHONPATH=src` 前提（インストール不要だが運用が雑になりがち）
