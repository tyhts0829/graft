# Grafix 性能計測

## 1. 目的

Grafix の性能計測は、次の 2 系統を分けて扱う。

- **再現可能な比較**: `python -m grafix benchmark` の schema v4 runner
- **実ウィンドウの診断**: `PerfCollector` による interactive frame 計測

wall time だけで最適化を判断せず、出力 checksum、実行環境、case 定義、
memory 増分も同時に保存する。

## 2. Packaged benchmark

### Case を確認する

```bash
python -m grafix benchmark list
python -m grafix benchmark list --suite smoke
python -m grafix benchmark list --json
```

case は Grafix package 内に定義される。リポジトリに存在しない専用 sketch への
依存はない。

### 計測する

短い動作確認:

```bash
python -m grafix benchmark run \
  --suite smoke \
  --profile smoke \
  --out /tmp/grafix-benchmark
```

対象を限定する:

```bash
python -m grafix benchmark run \
  --case runtime.provenance.rows_1000 \
  --case gui.parameter_table.rows_1000 \
  --profile short \
  --out /tmp/grafix-benchmark
```

測定 mode:

- `warm`: 同一 child process 内で warmup と calibration を行う。
- `process-cold`: sample ごとに fresh process を起動する。
- `compile-cold`: sample ごとに fresh process と空の `NUMBA_CACHE_DIR` を使う。

各 case は別 process で setup・計測される。JSON には以下が保存される。

- iteration 数を含む raw nanosecond samples
- median、MAD、min/max
- 20 samples 以上の場合だけ p95/p99
- setup/warmup/calibration 後 baseline と timed loop 後 peak の RSS delta
- geometry の dtype・shape・bytes を含む exact checksum
- source identity（commit、dirty、diff hash）
- environment compatibility key
- case source・fixture・parameter・seed の compatibility key

同じ出力先に既存 run ID がある場合は上書きしない。run ID を省略すると、
microsecond timestamp と random suffix を持つ ID が生成される。

### 2 run を厳格に比較する

```bash
python -m grafix benchmark compare \
  /tmp/grafix-before/runs/BEFORE.json \
  /tmp/grafix-after/runs/AFTER.json
```

`compare` は指定した 2 run の before/after を確認するためのコマンドである。
source identity が違うことは比較目的上許可する。一方、environment、measurement
mode、case identity が違う比較は既定で拒否する。`--allow-incompatible` は調査用で
あり、正式な before/after 判定には使わない。

checksum が変わった正常 case がある場合、`compare` は非 0 で終了する。

### 履歴を可視化する offline report

可視化用の optional extra を導入する。

```bash
python -m pip install -e ".[benchmark-report]"
```

```bash
python -m grafix benchmark report --out /tmp/grafix-benchmark
```

`report` は `--out` の `runs/*.json` に蓄積された schema v4 run を時系列で探索する。
同じ machine と measurement settings で同じ出力先を継続利用する。

```bash
python -m grafix benchmark run --suite smoke --profile short \
  --out /tmp/grafix-benchmark
# 実装を変更した後も同じ出力先へ追加する
python -m grafix benchmark run --suite smoke --profile short \
  --out /tmp/grafix-benchmark
python -m grafix benchmark report --out /tmp/grafix-benchmark
```

run ID は一意であり、既存 JSON は上書きされない。別 machine や計測条件の run も
失われないが、同じ性能線へは接続されず、別の compatibility cohort として表示される。

次を生成する。

- `/tmp/grafix-benchmark/report.html`: filter や tooltip を備えた対話的 report
- `/tmp/grafix-benchmark/overview.svg`: 共有しやすい静的 overview
- `/tmp/grafix-benchmark/warnings.json`: 読み込み warning の機械可読 summary

HTML は Vega runtime と chart spec を inline に含めるため JavaScript を使うが、CDN や
表示時のネットワーク接続は必要としない。壊れた JSON や非対応 schema は黙って
除外せず、HTML と warning summary に path と理由を残す。

主 chart は横軸が UTC の実測日時、縦軸が 1 iteration 当たりの median milliseconds
である。点と線が median、各点の縦 whisker が `median ± MAD` を表す。MAD は同一 run
内 sample のばらつきであり、信頼区間ではない。2 点だけの上下から regression や
improvement を断定しない。category と case を選ぶと、互換性の異なる cohort が独立した
縦軸の row panel として並ぶ。

同じ case ID、case compatibility key、environment compatibility key、mode、実効
measurement settings を持つ点だけが 1 本の線へ接続される。case を含まない run が
途中にあっても線は切れない。一方、環境・case 定義・設定の変更は別 cohort となり、
failure や checksum 変更では線が切れる。workload や iteration 数の異なる case 間では
絶対時間を直接比較しない。

run coverage strip は、青が current cohort、橙が checksum 変更、黄が同じ case の別 cohort、
灰が case 欠測、赤が current cohort の failure を表す。guardrail history は同じ contract
定義の `actual / limit` を追跡し、`1.0` を閾値として表示する。どちら側が pass かは
contract の comparator に依存する。

`benchmark report` は保存済み run 全体の推移を探索するために使い、特定の変更前後を
厳格に比較する場合は `benchmark compare BASE.json HEAD.json` を使う。report は
schema v4 の compatibility key と計測情報を前提とするため、情報の不足した legacy
schema を推測で v4 の系列へ接続しない。

report の終了 code は最新の valid run にある hard contract failure だけで決まる。
過去の failure と読み込み warning は HTML / `warnings.json` の historical audit に残るが、
最新 run が正常な report を継続して失敗させない。

## 3. CI での扱い

- hosted runner の wall time は artifact として観察し、hard gate にしない。
- smoke job は checksum 生成、case 完走、schema 検証を確認する。
- JSON、HTML、SVG overview、warning summary は GitHub Actions artifact として保存する。
- report が示す履歴は、その workflow で出力先に存在した run の範囲だけである。
  現在の一時 directory は workflow 間で継続されないため、CI artifact を長期履歴とは
  扱わない。
- wall-time ratio の gate が必要な場合は、固定された self-hosted Mac で base/head を
  同一 job 内に交互実行する。

## 4. Interactive frame 診断

既存 sketch を通常どおり interactive 実行し、環境変数で計測を有効にする。

```bash
GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python -m grafix run path/to/sketch.py
```

structured trace:

```bash
GRAFIX_PERF=1 \
GRAFIX_PERF_TRACE=data/output/performance.jsonl \
python -m grafix run path/to/sketch.py
```

GPU 同期待ちを診断する場合だけ次を使う。

```bash
GRAFIX_PERF=1 GRAFIX_PERF_GPU_FINISH=1 \
python -m grafix run path/to/sketch.py
```

`GRAFIX_PERF_GPU_FINISH=1` は `ctx.finish()` 自体が待ちを作るため、通常の性能比較には
使わない。

主な区間:

- `frame`: `draw_frame()` 全体
- `scene`: scene の評価と realize
- `draw`: user `draw(t)`。`scene` の部分区間
- `render_layer`: layer の upload と draw submit
- `gpu_finish`: 明示的 GPU 同期待ちを有効にした場合のみ

`draw` は `scene` の部分区間なので、両者を足さない。最初の window には import、
JIT、cache 構築が混ざるため、steady な複数 window と tail latency を確認する。

## 5. 比較時の原則

1. 同一 machine・同一 environment compatibility key を使う。
2. case compatibility key と output checksum を先に確認する。
3. hosted CI の数 sample から p95/p99 を推定しない。
4. time 改善と RSS delta 悪化を分けて記録する。
5. fake GL case の結果だけで実 GPU の改善を断定しない。
