# PyPI 公開チェックリスト

> どこで: `Grafix` リポジトリ
> 何を: PyPI への初回公開〜自動公開の手順
> なぜ: 公開前の抜け漏れを減らす

## 0. 事前準備（アカウントとセキュリティ）

- [x] PyPI アカウントを用意する（登録 / ログイン確認）
- [x] 2 要素認証（2FA）を有効化する
- [x] リカバリコードを生成して安全な場所に保管する

## 1. 配布名（PyPI のプロジェクト名）を決める

- [x] 配布名候補を決める（`pip install <配布名>` の名前）: grafix
- [x] PyPI で同名が存在しないことを検索して確認する
- [x] 「配布名」は grafix

## 2. `pyproject.toml` を整備する（最重要）

- [x] `project.name` を配布名にする
- [x] `project.version` を手動固定で入れる（例: `0.1.0`）
- [x] `project.readme = "README.md"` を設定する
- [x] `project.license = { file = "LICENSE" }` を設定する
- [x] `project.requires-python` を決める（例: `>=3.10`）
- [-] 依存を必須から最小化し、必要なら extras（`optional-dependencies`）へ分離する
- [x] `project.classifiers` を埋める（PyPI 公式一覧から選ぶ）
- [x] `src/` 配置のパッケージ探索設定を入れる（例: setuptools の `find` で `where = ["src"]`）

## 3. 配布物（sdist / wheel）をローカルでビルドする

- [x] 作業用 venv を用意する
- [x] ビルドツールを入れる: `python -m pip install -U build twine`
- [ ] 配布物を生成する: `python -m build`（`dist/` に `.tar.gz` と `.whl`）
- [ ] 配布物を検査する: `python -m twine check dist/*`（README 表示やメタデータ不備を検出）

## 4. 生成された配布物の中身を検品する

- [ ] sdist に必要ファイルが入っていることを確認する（README / LICENSE など）
- [ ] wheel に `grafix` パッケージが入っていることを確認する
- [ ] 新規 venv で wheel を直接インストールして import テストする

```sh
python -m venv .venv_test
source .venv_test/bin/activate
python -m pip install dist/<作ったwheel>.whl
python -c "import grafix; print('ok')"
```

## 5. TestPyPI で試し公開する（推奨）

- [ ] TestPyPI にアップロードする: `python -m twine upload --repository testpypi dist/*`
- [ ] TestPyPI からインストールできることを確認する: `python -m pip install -i https://test.pypi.org/simple/ <配布名>`

## 6. 本番 PyPI へ初回リリース（手動でもよい）

- [ ] 本番 PyPI にアップロードする: `python -m twine upload dist/*`
- [ ] 2FA を含むアップロード導線が成立することを確認する

## 7. GitHub Actions で自動公開（Trusted Publishing / OIDC）

- [ ] PyPI 側で対象プロジェクトの Trusted Publishing（OIDC）設定を行う
- [ ] GitHub Actions の公開ジョブに `permissions: id-token: write` を付与する
- [ ] `pypa/gh-action-pypi-publish` を使って公開する

```yaml
name: publish
on:
  push:
    tags:
      - "v*"

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
      contents: read
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -U build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
```

## 8. リリース運用（バージョン・タグ・再現性）を固める

- [ ] `pyproject.toml` の `project.version` を更新する（例: `0.1.1`）
- [ ] タグを切って push する（例: `git tag v0.1.1 && git push --tags`）
- [ ] CI が公開まで完走することを確認する

## 任意（必要なとき）

- [ ] このリポジトリの現状に合わせて `pyproject.toml` 雛形と公開ワークフローを具体化する（依存 / extras / `src/` 探索設定の整合を含む）
