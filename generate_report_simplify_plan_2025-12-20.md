# `generate_report.py` で時系列レポート生成に一本化する（簡素化計画）（2025-12-20）

## 目的

- `python generate_report.py` を実行すると `data/output/benchmarks/report.html` が生成（上書き）される。
- それ以外の機能（`--out` 等の引数、複数 CLI、役割分散）は削除して迷いを減らす。
- 入力は `data/output/benchmarks/runs/*.json`（`tools/benchmarks/effect_benchmark.py` が蓄積する形式）に固定する。

## 変更方針（候補）

### 案 A（最小変更）

- `generate_report.py` を追加し、内部では既存の `tools/benchmarks/effect_benchmark_report.py` / `tools/benchmarks/report.py` を呼ぶ。
- 既存ファイルは残るが、ユーザーが触る入口は 1 つになる。

### 案 B（完全一本化・推奨）

- `generate_report.py` に「集約 + HTML 生成」をすべて統合する。
- `tools/benchmarks/effect_benchmark_report.py` と `tools/benchmarks/report.py` は削除する（混乱の元を無くす）。

## 作業チェックリスト

### P0: 合意（どの案でいくか）

- [ ] 案 A/案 B を確定する
- [ ] （案 B の場合）`tools/benchmarks/effect_benchmark_report.py` と `tools/benchmarks/report.py` を削除してよいか確認する

### P1: `generate_report.py` 実装

- [ ] ルートに `generate_report.py` を追加する
  - 入力: `data/output/benchmarks/runs/*.json`
  - 出力: `data/output/benchmarks/report.html`
  - 絞り込み等の引数は持たない（固定）
- [ ] 実行時に run が無い場合は分かりやすいエラーで終了する

### P2: 既存コード整理（案に応じて）

- [ ] 案 A: 既存実装を内部関数として使い、CLI/argparse を撤去する（ファイルは残す）
- [ ] 案 B: 既存 2 ファイルを削除し、参照を `generate_report.py` に統一する
  - テストも `generate_report.py` を対象に付け替える

### P3: テスト/動作確認

- [ ] `PYTHONPATH=src python -m pytest -q tests/tools/test_benchmark_timeseries.py` を更新して通す（対象を `generate_report.py` に寄せる）
- [ ] `python generate_report.py` で `data/output/benchmarks/report.html` が更新されることを確認する

## Done（受け入れ条件）

- [ ] `python generate_report.py` だけ覚えれば良い状態になっている
- [ ] `report.html` は常に 1 つで上書きされる
- [ ] 余計な CLI / 引数が無い（または入口が 1 つだけ）

## 事前確認（あなたに質問）

- [ ] 案 A（最小変更）と案 B（完全一本化）のどちらで行く？；B
- [ ] 案 B の場合、`tools/benchmarks/effect_benchmark_report.py` と `tools/benchmarks/report.py` を削除して OK？；はい
