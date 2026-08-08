# README ユーザーフレンドリー化プラン（2026-08-08）

## 目的

`README.md` を、初見の利用者が Grafix の用途を判断し、インストールして最初の作品を表示・出力するまで迷わず進める入口へ作り直す。

現状の 653 行に混在している API 仕様、内部設計、運用上の不変条件、WIP 紹介は README から外し、本文を 140〜180 行程度（上限 200 行）に収める。

## 対象

- `README.md`
- `docs/readme/` 配下の README 掲載用画像（必要な場合のみ、既存画像は削除・上書きせず軽量版を追加する）

## 対象外

- Grafix 本体の API・CLI・描画挙動の変更
- 既存の作品画像や設計資料の削除
- README から外す内部仕様を、そのまま別文書へ複製すること
- 依頼範囲外の既存差分

## 作業項目

### 1. 冒頭を成果中心に作り直す

- [x] Grafix を「Python で線画を生成・調整し、SVG / PNG / MP4 / G-code へ出力できるツール」と一文で説明する
- [x] 主要機能を利用者視点の 3〜5 項目へ絞る
- [x] Beta / Python 3.11+ / macOS-first を冒頭付近で判断できるようにする
- [x] 代表ビジュアルを 1〜2 点へ絞る
- [x] 掲載画像の合計を目安 8 MiB 以下へ軽量化し、GitHub / PyPI の双方で解決できる URL を使う

### 2. Installation と Quick start を最短導線へ直す

- [x] Installation と Requirements を統合し、基本利用には `resvg` / `ffmpeg` が不要だと分かる順序にする
- [x] Quick start を「ファイルへ保存 → 実行 → 期待される画面」の完結した手順にする
- [x] 最小例を、キャンバス内にはっきり表示される 2D 図形と少数の操作へ置き換える
- [x] `python -m grafix run --watch` とファイル内の `run(...)` 設定が同一ではないため、誤解を招く並べ方をしない
- [x] `G`、`E`、`draw`、`run` の役割を短く説明する

### 3. 利用者が次に必要とする情報だけを残す

- [x] `G` / `E` / `L` / `P` / `run` を短い表または箇条書きで紹介する
- [x] Export は形式、主要ショートカット、既定出力先、headless CLI の代表例 1 件に絞る
- [x] `resvg` / `ffmpeg` は、それぞれ必要になる出力機能の近くで案内する
- [x] Examples は作品 18 点を対応コードへのリンク付きで掲載し、同梱 example を一覧・コピーする CLI へ案内する
- [x] Configuration、custom operation、preset は対応可能であることだけを示し、詳細は CLI help / 既存資料へ案内する
- [x] Troubleshooting は `python -m grafix doctor` を主導線にする
- [x] Development は contributor 向けコマンドと既存 Developer Guide へのリンクだけに縮める
- [x] License と Architecture へのクリック可能なリンクを置く

### 4. README から高度・内部向けの説明を外す

- [x] transactional reload の candidate catalog / worker generation / import 制約の詳細を外す
- [x] lazy import、module ownership、selector arity、spawn identity、WorkspaceState の詳細を外す
- [x] `ResourceBudget`、runtime profiles、semantic identity の詳細を外す
- [x] capture manifest、immutable snapshot、FIFO、byte accounting、終了処理の詳細を外す
- [x] custom operation の未完成な `...` コード例と ndarray 内部契約を外す
- [x] preset catalog、config loader ownership、G-code 内部順序契約を外す
- [x] Text-to-Physical art の WIP 紹介を README から外す
- [x] `RenderSession` の所有権や benchmark harness / migration の説明を外す

### 5. 正確性と表示を確認する

- [x] Quick start のコードを現行公開 API で実行し、キャンバス内に十分な大きさで描画されることを確認する
- [x] 掲載した CLI コマンドを `--help` または一時ディレクトリで確認する
- [x] README 内のローカルリンクと画像 URL を確認する
- [x] 本文が 200 行以下で、言語と用語が一貫していることを確認する
- [x] `git diff -- README.md docs/readme/` で変更範囲を確認する
- [x] 依頼範囲外の既存差分へ触れていないことを確認する

## 完了条件

- 初見の利用者が README の冒頭から 5 分以内に最初のプレビューまで進める
- README を読めば「何ができるか」「対応環境」「最小実行方法」「主要出力」「次に読む資料」が分かる
- Quick start が白紙または極小表示にならず、そのまま実行できる
- README が 200 行以下で、内部設計書や変更履歴の役割を兼ねていない
- README が参照する画像の合計が大幅に軽量化されている

## 検証結果

- README: 197 行（旧 653 行）
- README が参照する画像: 7,859,624 bytes（7.50 MiB、旧約 49.33 MiB）
- Quick start: README からコードを抽出し、300 × 300 の SVG を正常生成
- CLI: `export` / `examples` / `init` / `config` / `doctor` / `list` / `describe` を確認
- Test: `4120 passed in 298.39s`
- Lint: `ruff check src/grafix tests` 成功
- Type check: `mypy src/grafix` 成功
