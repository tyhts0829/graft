# どこで: `docs/memo/dependency_extras_plan_2025-12-16.md`。

# 何を: 依存関係の方針（core 必須 + dev extras）を、実装/ドキュメント/検証の観点で整理する。

# なぜ: `pip install -e .` だけで描画ウィンドウ（Parameter GUI 含む）まで到達できる UX にするため。

# 依存方針（core 必須 / dev extras）整理（2025-12-16）

対象: `docs/memo/conversation_md_refactor_review_2025-12-16.md` の対応表
「依存ライブラリも層で分け…」のうち、**“extras で入れなくてよい” は採用せず**、
代わりに「入れたらすぐ描画できる」UX を優先して core に寄せる方針に変更する。

注: `docs/plan_standard_src_layout.md` の実施により、import 名は `graft` に統一し、
ディレクトリ構成は `src/graft/` を正とする（`src` パッケージは廃止）。

## 前提（現在地）

- `pyproject.toml` があり、core 依存と extras（optional-dependencies）が宣言済み。
- 標準 src レイアウト（`src/graft/`）へ移行済みで、公開 import は `graft.*`。
- `README.md` は `pip install -e .` で描画までの導線があり、Dependencies は core / dev を区別している。
- core/export/interactive の依存方向はテストで守れている（= “コード上の層” はある）。

## ゴール（成功条件）

- `pip install -e .`（または同等）で **描画ウィンドウ + Parameter GUI** まで動く（依存が揃う）。
- `pip install -e ".[dev]"` で開発用ツール（pytest/ruff/mypy）が入る。
- `README.md` の Dependencies は「core / dev」表現に置き換わり、インストールコマンドが明記される。

## 依存レイヤー設計（現状）

- core（デフォルト）:
  - numpy
  - numba
  - shapely
  - pyglet
  - moderngl
  - pyimgui
- extras:
  - `dev`: pytest, ruff, mypy（+必要なら types 系）

## 実装チェックリスト（現状反映）

### 0) 事前合意（ここだけ先に確認）

- [x] 配布名（`project.name`）は `graft` で確定する
- [x] Python 最低対応バージョンは `>=3.11` とする
- [x] import パスは `graft.*` に統一する（標準 src レイアウトへ移行済み）
- [x] numba は core 必須のままにする
- [x] shapely / pyglet / moderngl / pyimgui を core 必須にする（UX 優先）

### 1) 依存の棚卸し（層ごと）

- [x] core vs extras を確定する（core: numpy/numba/shapely/pyglet/moderngl/pyimgui, extras: dev）
- [x] `import graft.api` が GUI 依存無しで通ることを確認する（run は遅延 importのまま）

### 2) `pyproject.toml` を追加して依存を宣言する

- [x] `pyproject.toml` を追加し、依存を宣言する（PEP 621 + setuptools）
  - [x] `[project.dependencies]` に core 依存（numpy/numba）を記述
  - [x] `[project.dependencies]` に shapely / pyglet / moderngl / pyimgui を追加する
  - [x] `[project.optional-dependencies]` は dev のみにする
  - [x] パッケージ検出を `src/` 起点で `graft*` に設定する

### 3) 実行時 UX を成立させる（必要なら最小修正）

- [x] `graft.api` は遅延 import を維持し、`import graft.api` が軽く済む
- [ ] （任意）起動時の失敗（OpenGL/ドライバ/環境差）を README に追記する

### 4) ドキュメントを更新する

- [x] `README.md` の Dependencies を「core / dev」へ置換する
- [x] インストール例を `pip install -e .` / `pip install -e ".[dev]"` に更新する

### 5) 検証（最小）

- [ ] （クリーン環境で）`pip install -e .` だけで `python -c "import graft.api; from graft.api import E, G, L, Export, run"` が通る
- [ ] `python sketch/main.py` が起動できる（Parameter GUI も含む）
- [ ] shapely 必須 effect（`partition/offset` 等）が実行できる

## 事前確認したいこと（追加で気づいたら追記）

- [x] 依存管理は `pyproject.toml` 一択にする（requirements.txt は併記しない）
- [x] extras は `dev` のみにする
- [x] numba を “必須” のままで許容する
- [ ] （任意）将来、最小 headless を作りたくなったら方針を見直す（interactive を extras に戻す等）
