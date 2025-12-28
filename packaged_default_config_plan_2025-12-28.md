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

## 1) 受け入れ条件（完了の定義）

- [ ] 設定の入口が YAML だけになる（環境変数は無視・ドキュメントから削除）
- [ ] デフォルト設定はパッケージ同梱 YAML が “唯一の出どころ” になる（コード側に二重の既定値を持たない）
- [ ] README に「同梱 YAML をコピーして使う」導線がある
- [ ] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q` が通る
- [ ] `PYTHONDONTWRITEBYTECODE=1 mypy src/grafix` が通る
- [ ] `PYTHONDONTWRITEBYTECODE=1 ruff check src/grafix tests` が通る（※repo 全体でなく対象限定）

## 2) 実装チェックリスト

### 2.1 同梱デフォルト YAML を追加

- [ ] `src/grafix/resource/...` にデフォルト YAML を追加する（0)の選択に従う）
- [ ] `pyproject.toml` の `tool.setuptools.package-data` に YAML を追加する
- [ ] `MANIFEST.in` に YAML を追加する（sdist 欠落防止）

### 2.2 runtime_config のロード順を “defaults + override” に変更

- [ ] `src/grafix/core/runtime_config.py`:
  - [ ] `importlib.resources` で同梱 YAML を読み、payload（dict）としてロードする
  - [ ] 探索したユーザー config（または `config_path`）をロードして上書きする
  - [ ] 環境変数参照（`GRAFIX_OUTPUT_DIR`, `GRAFIX_FONT_DIRS`）を削除する
  - [ ] （0)の選択に従い）明示 `config_path` の missing を例外/無視で扱う
  - [ ] キャッシュ挙動が意図通りか確認（`set_config_path()` の invalidate）

### 2.3 ドキュメント更新

- [ ] `README.md`:
  - [ ] 環境変数セクションを削除する
  - [ ] 「同梱デフォルト YAML の場所」と「プロジェクトへコピーして編集」の導線を追加する
    - 例: `python -c "from importlib.resources import files; print(files('grafix').joinpath(...))"` で場所を出す
  - [ ] 利用可能キー一覧を同梱 YAML に揃える（README と YAML が乖離しない）

### 2.4 テスト更新/追加

- [ ] `tests/core/test_runtime_config.py`:
  - [ ] 「config が無いときでも defaults がロードされる」ことをテスト
  - [ ] 「ユーザー config が defaults を上書きできる」ことをテスト（`output_dir`, `font_dirs`）
  - [ ] 「環境変数が無視される」ことをテスト（`GRAFIX_OUTPUT_DIR`, `GRAFIX_FONT_DIRS`）
  - [ ] （0)で例外を選ぶなら）missing `config_path` で例外になることをテスト

## 3) 実行コマンド（ローカル確認）

- [ ] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q`
- [ ] `PYTHONDONTWRITEBYTECODE=1 mypy src/grafix`
- [ ] `PYTHONDONTWRITEBYTECODE=1 ruff check src/grafix tests`

## メモ（作業中に気づいたら追記）

- （ここに追記）
