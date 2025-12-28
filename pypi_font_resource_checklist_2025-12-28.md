# PyPI 環境でのフォント解決・データ配置の改善チェックリスト（2025-12-28）

目的: PyPI から新規環境へインストールした `grafix` で `G.text()` 等が `data/` 前提で落ちる問題を解消し、最小限の同梱フォントで「設定なしでも動く」状態にする。

背景（今回の事象）:

- `grafix.core.primitives.text` が `_REPO_ROOT = Path(__file__).resolve().parents[4]` で repo ルート推定して `data/input/font` を参照する。
- インストール環境では `_REPO_ROOT` が `.../lib/python3.12` を指し、`.../lib/python3.12/data/input/font` が存在せず `FileNotFoundError` → `RealizeError` になる。

方針（今回の決定）:

- デフォルト同梱フォント: `src/grafix/resource/font/Google_Sans/static`（`.ttf`）を使用する。

非目的:

- repo の `data/input/font`（約 913MB）を wheel に同梱しない
- 互換ラッパー/シムで旧パスを延命しない（破壊的変更 OK）
- 既存 API を増やすだけの複雑化はしない

## 0) 事前に決める（あなたの確認が必要）

- [ ] デフォルトフォントとして実際に使うファイル名（例: `GoogleSans-Regular.ttf`）を 1 つに固定するか、ファミリーとして扱うか；一つに固定
- [ ] `font=` の受け入れ仕様（例）:
  - A: ファイル名/部分一致（現状踏襲）；これで
  - B: `google_sans` 等のエイリアス名を導入（シンプルだが仕様追加）
- [ ] `config.yaml` の置き場所と優先順位（案）: `--config` > `./.grafix/config.yaml` > `~/.config/grafix/config.yaml` > 環境変数 > デフォルト；推奨どおりで
- [ ] 「OS フォント探索」をやる/やらない（やるなら macOS のみ/全 OS）；やらない

## 1) 受け入れ条件（完了の定義）

- [ ] クリーン環境（wheel インストールのみ）で `G.text()` が例外なく実体化できる（`data/` が無くても動く）
- [ ] 例外時のメッセージに「探した場所/次の手」が出る（config の作り方、探索パス）
- [ ] `sketch/readme.py` が（少なくともフォント解決理由で）落ちない

## 2) リソース設計（repo 前提の撤廃）

- [ ] `grafix` パッケージ内リソースとしてフォントを扱う（`importlib.resources` ベース）
- [ ] `src/grafix/resource/font/Google_Sans/` の配布方針を確定
  - [ ] `static/*.ttf` を同梱
  - [ ] ライセンス同梱（`OFL.txt`, `README.txt`）を wheel に含める
  - [ ] `.DS_Store` は配布物に入れない（削除 or package-data から除外）

## 3) 実装変更（フォント解決）

- [ ] `text` primitive の `_REPO_ROOT` / `_FONT_DIR` を廃止し、解決元を以下の順に整理する
  - [ ] 1. `font` が実在パスならそれを使う
  - [ ] 2. `config.yaml`（または環境変数）で指定された `font_dirs` から解決
  - [ ] 3. `grafix` 同梱フォント（`src/grafix/resource/font/Google_Sans/static`）から解決
  - [ ] 4. 見つからなければ「探索先一覧 + config 例」を添えて例外
- [ ] デフォルト `font` 値を同梱フォントへ切り替える（`SFNS.ttf` 依存を消す）

## 4) packaging（wheel/sdist へ確実に入れる）

- [ ] `pyproject.toml` の `tool.setuptools.package-data` にフォントとライセンスを追加する
- [ ] `sdist` でも欠落しないよう必要なら `MANIFEST.in` も用意する（setuptools 設定と二重で齟齬が無いよう最小限）
- [ ] `pip install dist/*.whl` の新規 venv で `importlib.resources` からフォントが見えることを確認する

## 5) config.yaml（任意設定として導入）

- [ ] config スキーマ（最小）を決める: `data_root`, `font_dirs`, `output_dir`（必要なら `cache_dir`）
- [ ] 「config が無くても動く」を守る（無い場合はデフォルト + 同梱フォント）
- [ ] エラー時に `config.yaml` の最小例を表示する（コピペ可能な YAML）

## 6) テスト（最小の安全柵）

- [ ] `importlib.resources` で同梱フォントが取得できることのユニットテストを追加
- [ ] `G.text(font=...)` の解決優先順位（明示パス > config > 同梱）をテスト
- [ ] 例外文言に探索先が含まれることをテスト（文字列の一部一致で十分）

## 7) ドキュメント（ユーザー導線）

- [ ] README に「設定なしで動く」例を掲載（同梱フォントが使われることを明記）
- [ ] README に「外部フォントを使いたい場合」の章を追加（config の置き場所、最小例、優先順位）
- [ ] `sketch/readme.py` の `font="Cappadocia.otf"` は、同梱されないなら差し替え or 注釈（入手/配置方法）を付ける

## 8) 実行コマンド（ローカル確認）

- [ ] `PYTHONPATH=src pytest -q`（または対象テストのみ）
- [ ] `ruff check .`
- [ ] `mypy src/grafix`
- [ ] wheel を作って新規 venv で確認（例: `python -m build` → `pip install dist/*.whl` → `python -c "from grafix import G; G.text(text='ok')"`）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] PyPI 配布に含めるフォントのライセンス文面（`OFL.txt`）が意図通りか、README に一言入れるか
