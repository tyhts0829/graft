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

### 比較する

```bash
python -m grafix benchmark compare \
  /tmp/grafix-before/runs/BEFORE.json \
  /tmp/grafix-after/runs/AFTER.json
```

source identity が違うことは比較目的上許可する。一方、environment、measurement
mode、case identity が違う比較は既定で拒否する。`--allow-incompatible` は調査用で
あり、正式な before/after 判定には使わない。

checksum が変わった正常 case がある場合、`compare` は非 0 で終了する。

### Offline report

可視化用の optional extra を導入する。

```bash
python -m pip install -e ".[benchmark-report]"
```

```bash
python -m grafix benchmark report --out /tmp/grafix-benchmark
```

次を生成する。

- `/tmp/grafix-benchmark/report.html`: filter や tooltip を備えた対話的 report
- `/tmp/grafix-benchmark/overview.svg`: 共有しやすい静的 overview
- `/tmp/grafix-benchmark/warnings.json`: 読み込み warning の機械可読 summary

HTML は Vega runtime と chart spec を inline に含めるため JavaScript を使うが、CDN や
表示時のネットワーク接続は必要としない。壊れた JSON や非対応 schema は黙って
除外せず、HTML と warning summary に path と理由を残す。

report の差分は最新 run を head とし、environment、measurement settings、case set、
各 case の compatibility key がすべて一致する過去 run のうち直近のものだけを strict
baseline として選ぶ。棒グラフの値は `(head - base) / base` で、正が regression、負が
improvement を表す。履歴の帯は median ± MAD、guardrail は soft contract の閾値に
対する負荷率である。workload や iteration 数の異なる case 間では絶対時間を直接比較
しない。

## 3. CI での扱い

- hosted runner の wall time は artifact として観察し、hard gate にしない。
- smoke job は checksum 生成、case 完走、schema 検証を確認する。
- JSON、HTML、SVG overview、warning summary は GitHub Actions artifact として保存する。
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
