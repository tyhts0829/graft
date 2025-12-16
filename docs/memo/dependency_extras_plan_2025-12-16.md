# どこで: `docs/memo/dependency_extras_plan_2025-12-16.md`。

# 何を: 依存関係を層（core/export/interactive）+ extras（任意機能）に分離するための実装計画（チェックリスト）をまとめる。

# なぜ: 「入れなくてよい依存」を `pip install .[extra]` で明示し、README の Dependencies 一括列挙を解消するため。

# 依存の層分け + extras 分離 実装計画（2025-12-16）

対象: `docs/memo/conversation_md_refactor_review_2025-12-16.md` の対応表
「依存ライブラリも層で分け、extras 等で “入れなくてよい” 状態にする（❌）」の解消。

## 前提（現在地）

- `pyproject.toml` / `requirements*.txt` が無い。
- `README.md` の Dependencies に第三者依存が一括列挙されている（optional の表現はあるが、インストール手段が無い）。
- コード構造としては core/export/interactive の依存方向はテストで守れている（= “コード上の層” はある）。

## ゴール（成功条件）

- `pip install -e .`（または同等）で **core 最小依存**だけが入り、`import src.api` が通る。
- `pip install -e .[interactive]` を入れたときだけ interactive 依存（`pyglet/moderngl/pyimgui`）が入る。
- `pip install -e .[shapely]` を入れたときだけ Shapely 依存が入る（必要 effect の実行が可能になる）。
- `README.md` の Dependencies は「層 + extras」表現に置き換わり、インストールコマンドが明記される。

## 依存レイヤー設計（案）

- core（デフォルト）:
  - numpy
  - numba（※現状 `src/api/effects.py` が effect 実装を一括 import するため、実質必須）
- extras:
  - `interactive`: pyglet, moderngl, pyimgui
  - `shapely`: shapely（`partition/offset` 等）
  - `dev`: pytest, ruff, mypy（+必要なら types 系）
  - `all`: interactive + shapely（+将来増える optional を束ねる）

## 実装チェックリスト（合意後に着手）

### 0) 事前合意（ここだけ先に確認）

- [ ] 配布名（`project.name`）を決める（例: `graft`）;graft でいいよ。
- [ ] Python 最低対応バージョンを決める（現状 `X | Y` などから `>=3.10` は必要）;3.10
- [ ] 今回は import パス（`src.*`）は維持する（標準 src レイアウトへの移行は別タスクに分離する）
- [ ] numba を core 必須のままにするか決める（optional 化するなら「効果モジュールの import 戦略」から変更が必要）；core 必須。

### 1) 依存の棚卸し（層ごと）

- [ ] README 既存列挙（numpy/numba/shapely/moderngl/pyglet/pyimgui）を “core vs extras” に再分類して表にする
- [ ] `import src.api` の import 連鎖で「extras が無いと落ちる箇所」が無いことを確認する
- [ ] extras 名（`interactive/shapely/dev/all`）を確定する

### 2) `pyproject.toml` を追加して依存を宣言する

- [ ] `pyproject.toml` を新規追加する（PEP 621 + build backend）
  - [ ] `[build-system]` は `setuptools`（または採用する backend）で固定
  - [ ] `[project.dependencies]` に core 依存を記述
  - [ ] `[project.optional-dependencies]` に `interactive/shapely/dev/all` を記述
  - [ ] パッケージ検出を設定し、`src` パッケージがインストール対象になるようにする

### 3) “入れなくてよい” を実行時にも成立させる（必要なら最小修正）

- [ ] `interactive` 依存を import するのは「interactive を使うときだけ」になっていることを確認する
  - [ ] 例: `src/api/__init__.py:run()` の遅延 import は維持する
- [ ] extras 未導入時に機能を呼んだ場合、エラーが分かりやすい（`pip install -e .[interactive]` 等を示す）形にする（必要なら）

### 4) ドキュメントを更新する

- [ ] `README.md` の Dependencies を「層 + extras」へ置換する
- [ ] インストール例を追加する
  - [ ] core: `pip install -e .`
  - [ ] interactive: `pip install -e .[interactive]`
  - [ ] shapely: `pip install -e .[shapely]`
  - [ ] all: `pip install -e .[all]`

### 5) 検証（最小）

- [ ] （クリーン環境で）core のみで `python -c "import src.api; from src.api import E, G, L, Export"` が通る
- [ ] `interactive` ありで `python main.py` 等が起動できる
- [ ] `shapely` ありで shapely 必須 effect が実行できる（少なくとも import/実行時エラーが変わることを確認）

## 事前確認したいこと（追加で気づいたら追記）

- [ ] 依存管理は `pyproject.toml` 一択にする？（requirements.txt も併記する？）
- [ ] extras 名は `interactive` で良い？（`gui` の方が良い？）
- [ ] numba を “必須” のままで許容する？（optional にしたいなら、このタスクのスコープが一段増える）
