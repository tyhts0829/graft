# `tools/benchmarks` 時系列ベンチ可視化 追加計画（2025-12-20）

## 目的

- `data/output/benchmarks/runs/<日時>.json` を時系列に並べ、最適化前後の「改善度合い」を直感的に把握できるようにする。
- 横軸: 実行日時（= ファイル名の `<日時>`）。縦軸: 計測時間（基本は `mean_ms`）。
- 「テスト方式」（= ケース: `polyline_long` など）ごとにグラフを分け、各グラフの中で effect ごとの折れ線を描く。
- レポートは常に 1 つだけ生成し、生成のたびに上書きする（計測記録は run ごとに蓄積）。

## 現状（確認できたこと）

- `tools/benchmarks/effect_benchmark.py` は 1 回の実行ごとに `data/output/benchmarks/<run_id>/results.json` と `report.html` を出力する。
- `run_id` 省略時は `%Y%m%d_%H%M%S`（例: `20251220_233835`）。
- `results.json` 構造:
  - `meta`（`created_at`, `git_sha` など）
  - `cases`（`id`, `label`, `n_vertices` など）
  - `effects[].results[case_id].mean_ms`（ほか `stdev/min/max/n`）

## 方針（確定）

- 既存の「単発レポート（ケース別ランキング）」は **廃止** する（`report.html` を run ごとに生成しない）。
- 計測は run ごとに JSON ファイルを **1 枚ずつ蓄積** する（ディレクトリを掘らない）。
  - 出力先: `data/output/benchmarks/runs/<run_id>.json`（`run_id` は `%Y%m%d_%H%M%S`）
  - `--run-id` を残す場合でも、フォーマットは上記に制限して “時系列として扱える” ことを優先する
- 可視化レポートは常に 1 つだけ生成し、**上書き** する:
  - 出力先（案）: `data/output/benchmarks/report.html`
- 見栄えと実装量のバランスのため、グラフ描画は **CDN 配信の JS グラフライブラリ** を使う（閲覧時にネット接続が必要）。
  - 候補: Chart.js（line chart が素直、導入が軽い）
  - Python 側は「run を集約して JSON 化し HTML に埋め込む」だけに寄せる（肥大化を避ける）
  - ネット無しでも最低限読めるよう、HTML 内に「表（テーブル）」も併記する（グラフが出ない場合のフォールバック）

## 作業チェックリスト（実装タスク）

### P0: 仕様整理（出力を 1 本化）

- [ ] `tools/benchmarks/effect_benchmark.py` を「計測結果を 1 ファイルで蓄積」に変更する
  - `data/output/benchmarks/runs/` を作り、`<run_id>.json` を直接保存する（run ごとのディレクトリは作らない）
  - `report.html` は生成しない（可視化は別コマンドに一本化）
- [ ] `tools/benchmarks/report.py` は時系列レポート用に置き換える（単発ランキングは撤去）
  - 目的: ツールの役割を「計測」と「時系列可視化」に絞ってクリーンにする

### P1: 入力収集（run 列の構築）

- [ ] `data/output/benchmarks/runs` 配下の `*.json` を列挙し、`<日時>.json` の `<日時>` が `%Y%m%d_%H%M%S` に合致するものだけを run として採用する
- [ ] 各 run の `<日時>.json` を読み込み、`run_datetime/run_id/meta/cases/effects` を抽出する
- [ ] run を日時昇順にソートし、集約用の中間データ（`runs[]`）に正規化する

### P2: 集約（ケース×effect の時系列）

- [ ] ケース（`case_id`）の一覧を決める（基本: 最新 run の `cases` を基準にする）
- [ ] effect 名の一覧を決める（基本: 最新 run の `effects` を基準にする）
- [ ] `series[case_id][effect_name] -> [(run_datetime, mean_ms | None), ...]` を生成する
  - 欠損/`skipped`/`error` は `None` として “線を途切れさせる” 扱いにする

### P3: 可視化（HTML レポート / CDN グラフ）

- [ ] `tools/benchmarks/report.py` を「時系列レポート生成」へ置換する
- [ ] HTML に run 一覧と集約データ（JSON）を埋め込み、Chart.js で line chart を描画する
  - ケースごとに 1 枚の折れ線グラフ
  - 横軸: run の日時（フォルダ名の昇順）
  - 縦軸: `mean_ms`（まずは線形）
  - 凡例クリックで effect の表示/非表示を切り替えられる状態にする（UI を増やしすぎない）
- [ ] “改善が見える” 最小の補助情報を出す
  - 表: `effect x (first, last, ratio, last_mean_ms)`（ケースごと）
  - hover/tooltip: `run_id / mean_ms / git_sha`（取れる範囲で）

### P4: CLI（レポート生成コマンド）

- [ ] レポート生成を実行する CLI を追加する（例: `python -m tools.benchmarks.effect_benchmark_report`）
- [ ] オプション（必要最小）
  - `--out`（入力ルート、既定: `data/output/benchmarks`）
  - `--output`（生成 HTML のパス、既定: `<out>/report.html`）
  - `--cases`（カンマ区切りでケース絞り込み、既定: 全部）
  - `--effects` / `--skip`（カンマ区切り）
  - `--top`（「最新 run の遅い順」で上位 N 本だけをデフォルト表示。既定: 10）

### P5: テスト（壊れにくさの最低限）

- [ ] ファイル名（`<日時>.json`）→日時パースのテスト（`%Y%m%d_%H%M%S`）
- [ ] 欠損（run 間で effect/case が増減、skipped/error が混在）でも例外にならず HTML を生成できるテスト
  - 追加先候補: `tests/tools/test_benchmark_timeseries.py`

### P6: 既存データの移行（最後に 1 回だけ）

- [ ] 旧形式 `data/output/benchmarks/<run_id>/results.json` を新形式へリネームする
  - 変換先: `data/output/benchmarks/runs/<run_id>.json`
  - 旧形式の `<run_id>` ディレクトリはフォルダ名が日時なので、そのままファイル名に使える
  - 目的: 過去の計測も時系列レポートの入力として使えるようにする

### P7: 検証（手元データで確認）

- [ ] 既存の `data/output/benchmarks` を入力にして HTML を生成し、グラフの並びが「日時昇順」になっていることを確認する
- [ ] レポートが 1 ファイルに上書きされることを確認する（`--output` 省略時も）
- [ ] lint: `ruff check`（変更した `tools/benchmarks/*.py`）
- [ ] `PYTHONPATH=src pytest -q tests/tools/test_benchmark_timeseries.py`

## Done（受け入れ条件）

- [ ] `data/output/benchmarks/runs/<日時>.json` が蓄積され、複数 run を集約して可視化できる
- [ ] レポートは `data/output/benchmarks/report.html`（既定）に生成され、常に上書きされる
- [ ] 横軸がファイル名由来の日時で、時系列順に並ぶ
- [ ] 欠損 run / skipped effect が混ざっても “見える形で” 破綻しない（少なくともクラッシュしない）
