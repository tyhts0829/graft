# デフォルトを同梱 config.yaml で管理（環境変数廃止）: 実装改善計画（2025-12-28）

目的: `grafix` の実行時設定を **config.yaml のみに統一**し、デフォルト値も **パッケージ同梱 YAML** へ寄せて「設定の真実」を 1 箇所に集約する。

背景（現状の問題）:

- 環境変数は存在が認知されづらく、再現性が落ちやすい（どこで効いているか分かりにくい）。
- デフォルトが「コード」にあると、ユーザーが「使えるキー一覧」を YAML で確認できない。

方針（今回の決定）:

- すべての設定は YAML（config.yaml）のみ。
  - `run(..., config_path=...)` の明示指定 + 既定の探索（`./.grafix/config.yaml`, `~/.config/grafix/config.yaml`）
- デフォルト設定は **パッケージ同梱 YAML** を常にベースとして読み込む。
- 環境変数（`GRAFIX_*`）は参照しない（存在しても無視）。

## 0) 事前に決める（あなたの確認が必要）

- [x] 同梱デフォルト YAML の配置場所
  - 案 A: `grafix/resource/config.yaml`（直感的だが “ユーザー config” と名前が被る）
  - 案 B: `grafix/resource/default_config.yaml`（混乱しづらい；おすすめ）
  - 案 C: `grafix/resource/config/default.yaml`（整理されるが少し深い）
- [x] 同梱デフォルト YAML の内容（値）
  - すべて明示で書く（data ディレクトリの構造を流用）
  - `output_dir`: `"data/output"`
  - `font_dirs`: `["data/input/font"]`
- [x] マージ優先順位（上書き順）
  - `packaged defaults` < `discovered user config` < `run(..., config_path=...)`
- [x] `run(config_path="...")` で指定したファイルが存在しない場合
  - A: 例外（ユーザーが明示指定したのに無いのはミスとみなす）
  - B: そのまま無視して defaults のみで動く（現状寄り）

## 追加: config.yaml の対象を増やす（今回やること）

この改善では、`run()` 引数では指定できないが “ユーザー環境で変えたくなる値” を config.yaml 管理に追加する。

### A. 置き場所/環境依存が強いもの（config 向き）

1. ウィンドウ位置

- 定数: `DRAW_WINDOW_POS = (25, 25)`, `PARAMETER_GUI_POS = (950, 25)`
- 場所: `src/grafix/api/runner.py`
- キー案: `ui.window_positions.draw`, `ui.window_positions.parameter_gui`（タプル/配列）

2. Parameter GUI ウィンドウサイズ

- 定数: `DEFAULT_WINDOW_WIDTH = 800`, `DEFAULT_WINDOW_HEIGHT = 1000`
- 場所: `src/grafix/interactive/parameter_gui/pyglet_backend.py`
- キー案: `ui.parameter_gui.window_size`（`[width, height]`）

### B. 出力品質/重さのトレードオフ（好みが出るので config 候補）

3. PNG 変換スケール

- 定数: `PNG_SCALE = 8.0`
- 場所: `src/grafix/export/image.py`
- キー案: `export.png.scale`

実装タスク（実施）:

- [x] `src/grafix/resource/default_config.yaml` に `ui` / `export` の追加キーを追加する
- [x] `src/grafix/core/runtime_config.py` で追加キーをロードし `RuntimeConfig` に載せる
- [x] `src/grafix/api/runner.py` で `ui.window_positions.*` を `set_location()` に反映する
- [x] `src/grafix/interactive/runtime/parameter_gui_system.py` で `ui.parameter_gui.window_size` を反映する
- [x] `src/grafix/export/image.py` で `export.png.scale` を反映する（`png_output_size()`）
- [x] `README.md` の Keys を更新する
- [x] テストで home/cwd を隔離し、ユーザー環境に依存しないようにする

## 追加: default_config.yaml の構造化（今後の拡張に備える）

目的: 設定値が増えても衝突/混乱しないよう、用途ごとにネームスペース（段）を切る。

提案スキーマ（例）:

```yaml
version: 1

paths:
  output_dir: "data/output"
  font_dirs:
    - "data/input/font"

ui:
  window_positions:
    draw: [25, 25]
    parameter_gui: [950, 25]
  parameter_gui:
    window_size: [800, 1000]

export:
  png:
    scale: 8.0
```

設計ノート:

- `version` は “YAML スキーマの世代” を明示するために置く（将来の変更説明が楽）。
- 既定値の真実は同梱 YAML に集約し、README は同梱 YAML を「コピーして編集」の導線に寄せる。
- マージは現状の “トップレベル上書き” でも運用可能（defaults を丸ごとコピーして編集する前提）。
  - 部分上書き（deep merge）をやりたくなったら、その時点で方針決めする（複雑化回避）。

実装タスク（未実施）:

- [x] `src/grafix/resource/default_config.yaml` を上記ネームスペース構造へ変更する
- [x] `src/grafix/core/runtime_config.py` の読取キーを `paths.output_dir`, `paths.font_dirs` に変更する
- [x] `ui.window_positions.*`, `ui.parameter_gui.window_size`, `export.png.scale` の読取を追加する
- [x] README の「Keys」説明をネームスペース構造に合わせて更新する
- [x] `tests/core/test_runtime_config.py` を新スキーマに合わせて更新する

## 1) 受け入れ条件（完了の定義）

- [x] 設定の入口が YAML だけになる（環境変数は無視・ドキュメントから削除）
- [x] デフォルト設定はパッケージ同梱 YAML が “唯一の出どころ” になる（コード側に二重の既定値を持たない）
- [x] README に「同梱 YAML をコピーして使う」導線がある
- [x] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q` が通る
- [x] `PYTHONDONTWRITEBYTECODE=1 mypy src/grafix` が通る
- [x] `PYTHONDONTWRITEBYTECODE=1 ruff check src/grafix tests` が通る（※repo 全体でなく対象限定）

## 2) 実装チェックリスト

### 2.1 同梱デフォルト YAML を追加

- [x] `src/grafix/resource/default_config.yaml` にデフォルト YAML を追加する
- [x] `pyproject.toml` の `tool.setuptools.package-data` に YAML を追加する
- [x] `MANIFEST.in` に YAML を追加する（sdist 欠落防止）

### 2.2 runtime_config のロード順を “defaults + override” に変更

- [x] `src/grafix/core/runtime_config.py`:
  - [x] `importlib.resources` で同梱 YAML を読み、payload（dict）としてロードする
  - [x] 探索したユーザー config（または `config_path`）をロードして上書きする
  - [x] 環境変数参照（`GRAFIX_OUTPUT_DIR`, `GRAFIX_FONT_DIRS`）を削除する
  - [x] 明示 `config_path` の missing を例外（`FileNotFoundError`）で扱う
  - [x] `set_config_path()` で cache が無効化される

### 2.3 ドキュメント更新

- [x] `README.md`:
  - [x] 環境変数セクションを削除する
  - [x] 「同梱デフォルト YAML の場所」と「プロジェクトへコピーして編集」の導線を追加する
    - 例: `python -c "from importlib.resources import files; print(files('grafix').joinpath(...))"` で場所を出す
  - [x] 利用可能キー一覧を同梱 YAML に揃える（README と YAML が乖離しない）

### 2.4 テスト更新/追加

- [x] `tests/core/test_runtime_config.py`:
  - [x] 「config が無いときでも defaults がロードされる」ことをテスト
  - [x] 「ユーザー config が defaults を上書きできる」ことをテスト（`output_dir`, `font_dirs`）
  - [x] 「環境変数が無視される」ことをテスト（`GRAFIX_OUTPUT_DIR`, `GRAFIX_FONT_DIRS`）
  - [x] missing `config_path` で例外になることをテスト

## 3) 実行コマンド（ローカル確認）

- [x] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q`
- [x] `PYTHONDONTWRITEBYTECODE=1 mypy src/grafix`
- [x] `PYTHONDONTWRITEBYTECODE=1 ruff check src/grafix tests`

## メモ（作業中に気づいたら追記）

- （ここに追記）
