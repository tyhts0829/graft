# どこで: `docs/memo/dependency_extras_plan_2025-12-16.md`。

# 何を: 依存関係を層（core/export/interactive）+ extras（任意機能）に分離するための実装計画（チェックリスト）をまとめる。

# なぜ: 「入れなくてよい依存」を `pip install .[extra]` で明示し、README の Dependencies 一括列挙を解消するため。

# 依存の層分け + extras 分離 実装計画（2025-12-16）

対象: `docs/memo/conversation_md_refactor_review_2025-12-16.md` の対応表
「依存ライブラリも層で分け、extras 等で “入れなくてよい” 状態にする（❌）」の解消。

注: `docs/plan_standard_src_layout.md` の実施により、import 名は `graft` に統一し、
ディレクトリ構成は `src/graft/` を正とする（`src` パッケージは廃止）。

## 前提（現在地）

- `pyproject.toml` があり、core 依存と extras（optional-dependencies）が宣言済み。
- 標準 src レイアウト（`src/graft/`）へ移行済みで、公開 import は `graft.*`。
- `README.md` に `pip install -e ".[interactive]"` の導線はあるが、Dependencies はまだ一括列挙になっている。
- core/export/interactive の依存方向はテストで守れている（= “コード上の層” はある）。

## ゴール（成功条件）

- `pip install -e .`（または同等）で **core 最小依存**だけが入り、`import graft.api` が通る。
- `pip install -e ".[interactive]"` を入れたときだけ interactive 依存（`pyglet/moderngl/pyimgui`）が入る。
- `pip install -e ".[shapely]"` を入れたときだけ Shapely 依存が入る（shapely 必須 effect の実行が可能になる）。
- `README.md` の Dependencies は「層 + extras」表現に置き換わり、インストールコマンドが明記される。

## 依存レイヤー設計（現状）

- core（デフォルト）:
  - numpy
  - numba
- extras:
  - `interactive`: pyglet, moderngl, pyimgui
  - `shapely`: shapely（`partition/offset` 等）
  - `dev`: pytest, ruff, mypy（+必要なら types 系）
  - （任意）`all`: interactive + shapely（+将来増える optional を束ねる）

## 実装チェックリスト（現状反映）

### 0) 事前合意（ここだけ先に確認）

- [x] 配布名（`project.name`）は `graft` で確定する
- [x] Python 最低対応バージョンは `>=3.11` とする
- [x] import パスは `graft.*` に統一する（標準 src レイアウトへ移行済み）
- [x] numba は core 必須のままにする

### 1) 依存の棚卸し（層ごと）

- [x] core vs extras を確定する（core: numpy/numba, extras: interactive/shapely/dev）
- [x] `import graft.api` の import 連鎖で「extras が無いと落ちる箇所」が無いことを確認する（run は遅延 import）
- [ ] （任意）`all` extras を追加するか決める（現状は未定義）

### 2) `pyproject.toml` を追加して依存を宣言する

- [x] `pyproject.toml` を追加し、依存を宣言する（PEP 621 + setuptools）
  - [x] `[project.dependencies]` に core 依存（numpy/numba）を記述
  - [x] `[project.optional-dependencies]` に `interactive/shapely/dev` を記述
  - [x] パッケージ検出を `src/` 起点で `graft*` に設定する
  - [ ] （任意）`all` extras を追加する（interactive + shapely）

### 3) “入れなくてよい” を実行時にも成立させる（必要なら最小修正）

- [x] `interactive` 依存は「run を呼ぶときだけ」要求される（`graft.api.run()` は遅延 import）
- [x] `shapely` は effect 実行時にのみ import される（未導入時は import error / `RuntimeError` で落ちる）
- [ ] （任意）interactive 未導入で run を呼んだ場合のエラーメッセージを改善する（install 例を示す等）

### 4) ドキュメントを更新する

- [ ] `README.md` の Dependencies を「層 + extras」へ置換する（現在は一括列挙）
- [x] インストール例（interactive）を README に追加する（`pip install -e ".[interactive]"`）
- [ ] core / shapely / dev（/ all）も README に追記する
  - [ ] core: `pip install -e .`
  - [ ] interactive: `pip install -e ".[interactive]"`
  - [ ] shapely: `pip install -e ".[shapely]"`
  - [ ] dev: `pip install -e ".[dev]"`
  - [ ]（任意）all: `pip install -e ".[all]"`

### 5) 検証（最小）

- [ ] （クリーン環境で）core のみで `python -c "import graft.api; from graft.api import E, G, L, Export, run"` が通る（run の呼び出しは除く）
- [ ] `interactive` ありで `python sketch/main.py` が起動できる
- [ ] `shapely` ありで shapely 必須 effect が実行できる（`partition/offset` 等）

## 事前確認したいこと（追加で気づいたら追記）

- [x] 依存管理は `pyproject.toml` 一択にする（requirements.txt は併記しない）
- [x] extras 名は `interactive` / `shapely` / `dev` で良い
- [x] numba を “必須” のままで許容する
- [ ] （任意）`all` extras を導入するか（導入するなら README とも同期する）
