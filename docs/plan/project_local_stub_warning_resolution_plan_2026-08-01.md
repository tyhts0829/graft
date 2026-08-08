# Project-local stub source 設定と型警告の解消計画

## 目的

ユーザーが任意の名前・場所に複数のsketchやoperation moduleを書く通常の運用で、
`python -m grafix stub` を引数なしで実行すれば、project-localなprimitive/effect/presetを
再現可能な形で `typings/grafix/` へ反映できるようにする。

あわせて `sketch/work/260801_codex.py` で発生しているPylance/Pyright・mypyの型エラーを
解消し、同じ欠落がstub再生成で再発しない状態にする。

## 背景と問題

- 現行実装は `grafix init` が作る最小entrypointに合わせ、`sketch/main.py` だけを
  暗黙にimportする。
- `sketch/main.py` 限定の一般的な設計理由は履歴に残っておらず、onboardingの
  single-sketch前提がそのままstub CLIの既定になったと判断できる。
- リポジトリ自身は53個のactive sketchを任意のsubdirectoryで管理しており、main-onlyの
  前提と実運用が一致していない。
- 全 `.py` の暗黙importは、top-level副作用、重い処理、古い作品の失敗、同名operation衝突を
  引き起こすため採用できない。
- 現在の `typings/grafix/api/__init__.pyi` は
  `sketch/work/260801_codex.py` を読み込まず生成され、同作品の7個のlocal primitiveが欠落した。
  その結果、7属性・8箇所の `_G` attribute errorが発生している。
- 別件として同作品の `_text()` は `align: str` を、
  `Literal["left", "center", "right"]` を要求する `G.text()` へ渡しており、1件の
  正当な型エラーがある。

## ユーザー向け仕様

### Project設定

project rootの `pyproject.toml` に、stub生成へ含めるsourceを順序付きで宣言する。

```toml
[tool.grafix.stub]
sources = [
  "sketch/main.py",
  "sketch/work/260801_codex.py",
  "sketch/presets",
]
```

`sources` の各要素はproject root基準で、次を受け付ける。

- Python file: そのmoduleをimportしてdecorator登録を取得する。
- Directory: 配下のPython sourceを既存の隔離candidate loaderで読み込む。
- Dotted module: import可能なmoduleを明示対象にする。

任意のファイル名・directory構成を利用でき、`main.py` は特別扱いしない。

### CLI

- `python -m grafix stub`
  - `[tool.grafix.stub].sources` を読み、project-local stubを既定出力へ生成する。
- `python -m grafix stub --source PATH_OR_MODULE`
  - 設定済みsourceへ一時的なsourceを追加する。複数回指定できる。
- `python -m grafix stub --isolated`
  - project設定とproject source directoryを読み込まず、built-inだけのstubを生成する。
  - installed stub同期など、project-local定義を含めてはいけない用途で使う。

現行の `--import/--module` と `--no-default-import` は削除し、互換shimは設けない。
`main.py` の暗黙importも削除する。

### 安全性と制約

- sourceはユーザーが明示したものだけを実行し、project全体を暗黙走査しない。
- file/directoryが存在しない場合や設定型が不正な場合は、対象を明示したエラーで停止する。
- sourceは順序を保って重複排除する。
- 一つのproject-local stubは一つの `G/E/P` namespaceを表す。設定source間で同名operationが
  衝突する場合は生成を失敗させ、黙って片方を採用しない。
- installed/built-in stubの `src/grafix/api/__init__.pyi` にuser operationは混入させない。

## 実装方針

### 1. Stub project設定

- `src/grafix/devtools/generate_stub.py` に、stdlib `tomllib` を使う小さなproject設定loaderを
  追加する。
- `[tool.grafix.stub]` は `sources` だけを受け付け、文字列listとしてstrictに検証する。
- file、directory、dotted moduleを分類し、file/moduleは既存target importer、directoryは
  既存authoring candidate loaderへ渡す。
- runtime configのsource directoryとproject設定のdirectoryは、解決済みpathで重複排除する。
- project設定の読み込み、source解決、catalog生成をfresh process内で完結させる。

### 2. CLIとonboarding

- `--source` と `--isolated` を追加し、main-only分岐と旧optionを削除する。
- `grafix init` が生成する `pyproject.toml` に以下を含める。

```toml
[tool.grafix.stub]
sources = ["sketch/main.py", "sketch/presets"]
```

- このリポジトリの `pyproject.toml` には、`sketch/main.py`、
  `sketch/work/260801_codex.py`、`sketch/presets` を宣言する。

### 3. Stub責務と同期

- installed stub同期は `--isolated` と同梱default configを使う。
- project-local stub同期は、ignoredな `.grafix/config.yaml` を使わず、tracked
  `pyproject.toml` のsource宣言と同梱default configだけで生成する。
- `typings/grafix/api/__init__.pyi` とroot proxyを引数なしの正規コマンドで再生成する。
- `src/grafix/api/__init__.pyi` はbuilt-in surfaceのまま変更しない。

### 4. 対象作品の型修正

- `sketch/work/260801_codex.py` の `_text()` に
  `Literal["left", "center", "right"]` を指定する。
- 描画結果、既定値、runtime登録、パラメータは変更しない。

### 5. ドキュメント

- READMEへ次を追記する。
  - installed stubとproject-local stubの違い。
  - `[tool.grafix.stub].sources` の設定例。
  - file/directory/dotted moduleの扱い。
  - `--source` / `--isolated` の用途。
  - 複数source間ではoperation名を一意にする必要があること。
- `docs/agent_docs/testing.md` とCLI helpを同じ契約へ更新する。
- main-only既定と旧optionの削除をmigration noteへ記録する。

## テスト方針

### Project設定loader

- section未指定は空sourceになる。
- 任意名・任意directoryのPython fileを読み込める。
- directory sourceからprimitive/effect/presetを生成できる。
- dotted moduleと複数sourceを順序どおり処理する。
- 不正型、unknown key、missing pathを明示拒否する。

### CLI

- 引数なしでproject設定を読む。
- `--source` は設定sourceへ追加される。
- `--isolated` はproject-local operationを含めない。
- `sketch/main.py` が存在しても、設定に無ければ暗黙importしない。
- source import失敗とoperation衝突は非0終了になる。

### Artifact同期と型検査

- installed stubをbuilt-in-only生成結果とbyte exact比較する。
- project-local stubをtracked project設定による生成結果とbyte exact比較する。
- `sketch/work/260801_codex.py` をmypyとPyrightで検査し、0エラーを確認する。
- full pytestで既存runtime、onboarding、stub生成を回帰確認する。

## 実施項目

- [x] mypyとPyrightで現行9エラーを再現する。
- [x] main-only挙動の導入履歴とmulti-sketch運用との不一致を確認する。
- [x] 対象作品の明示importで7属性・8箇所のattribute error解消を確認する。
- [x] ユーザー向け仕様をproject source設定方式へ改訂する。
- [ ] project stub設定loaderとsource分類を実装する。
- [ ] CLIを `--source` / `--isolated` 契約へ変更する。
- [ ] onboardingとこのリポジトリの `pyproject.toml` を更新する。
- [ ] installed/project-local stub同期テストとCLI回帰テストを更新・追加する。
- [ ] project-local stubを新しい引数なしコマンドで再生成する。
- [ ] 対象作品の `align` を `Literal` 型へ修正する。
- [ ] README、テスト規約、migration note、CLI helpを更新する。
- [ ] 変更対象へruffとmypyを実行する。
- [ ] 対象作品へmypyとPyrightを実行し、0エラーを確認する。
- [ ] stub/onboarding関連のfocused pytestを実行する。
- [ ] clean checkout相当で両stub同期テストを実行する。
- [ ] full pytestを実行する。
- [ ] 最終差分と作業ツリーを確認する。

## 検証コマンド

```bash
PYTHONPATH=src python -m ruff check \
  src/grafix/devtools/generate_stub.py \
  src/grafix/devtools/onboarding.py \
  sketch/work/260801_codex.py \
  tests/devtools/test_generate_stub_project_local.py \
  tests/stubs/test_api_stub_sync.py

PYTHONPATH=src python -m mypy \
  src/grafix/devtools/generate_stub.py \
  src/grafix/devtools/onboarding.py

PYTHONPATH=src python -m mypy --no-incremental \
  sketch/work/260801_codex.py

PYTHONPATH=src python -m pyright \
  --pythonpath /opt/anaconda3/envs/gl5/bin/python \
  sketch/work/260801_codex.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider \
  tests/devtools/test_generate_stub_project_local.py \
  tests/devtools/test_onboarding_cli.py \
  tests/stubs/test_api_stub_sync.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest -q -p no:cacheprovider
```

## 対象外

- project配下の全Python fileを暗黙importすること。
- editorごと・ファイルごとに異なる `G/E/P` stubを自動切替すること。
- `G` に型安全性を失わせる汎用 `__getattr__` を追加すること。
- user operationをinstalled/built-in stubへ取り込むこと。
- operation名が衝突する独立作品を一つのnamespaceへ黙って統合すること。
- 対象作品の描画内容やruntime operation APIを変更すること。
