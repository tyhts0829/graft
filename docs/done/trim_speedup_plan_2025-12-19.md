# `src/grafix/core/effects/trim.py` 高速化計画（2025-12-19）

## 目的

- `trim` effect の処理時間/メモリを改善する（特に「長いポリライン」「線が多い」ケース）。
- 公開 API（`@effect` な `trim(...)` の引数/返り値）は変えない。
- 既存テストの挙動を維持する（`tests/core/effects/test_trim.py`）。

## 実データ傾向（遅い入力）

- 大きめの閉曲線（正多角形リング）
  - `verts=5001 lines=1 closed_lines=1`（`tools/benchmarks/cases.py` の `ring_big` と同等）
- 1 本の長い折れ線（頂点数が多い）
  - `verts=50000 lines=1 closed_lines=0`（`tools/benchmarks/cases.py` の `polyline_long` と同等）

## 現状メモ（ボトルネック候補）

- `_build_arc_length()` が頂点ごとに Python ループ + `np.linalg.norm` 呼び出し。
- `_interpolate_at_distance()` が 2 回、距離配列を線形探索。
- 中間頂点の抽出も `distances` 全走査（Python ループ）。
- 1 本あたり概ね「弧長計算 + 探索 2 回 + 抽出」で複数回の O(N) Python ループが走り、長い線で支配的になりやすい。

## ベースライン計測（先にやる）

- [ ] `trim` 単体のベンチを取得する（HTML/JSON を保存）
  - `PYTHONPATH=src python -m tools.benchmarks.effect_benchmark --only trim --cases polyline_long,ring_big --repeats 10 --warmup 2 --disable-gc`
- [ ] 「長い 1 本」「閉曲線（閉リング）」で差が出るかを確認する（現状は上記 2 ケースで十分）
- [ ] 目標値を決める（例: `polyline_long` で 2x、`many_lines` で 1.5x など）

## 改善案（優先順）

### P0（最優先）: 1 本内の計算を NumPy 化（cumsum + searchsorted）

- [x] `_build_arc_length()` の Python ループ/`np.linalg.norm` を廃止（Numba で弧長 prefix-sum）
  - 実装: `src/grafix/core/effects/trim.py` の `_build_arc_length_nb()`
- [x] `_interpolate_at_distance()` の線形探索を廃止（二分探索）
  - 実装: `_lower_bound()` + `_interpolate_at_distance_nb()`
  - 0 長セグメントは「開始頂点を返す」扱い（旧仕様踏襲）
- [x] 中間頂点抽出を「境界 index（upper/lower bound）」で決め、範囲コピーへ置換
  - 実装: `_upper_bound()` / `_lower_bound()` から `start_i/end_i` を算出
- [x] 「終点が直前点と allclose なら追加しない」を維持
  - 実装: `_allclose3()`（`rtol=1e-05, atol=1e-08`）

期待効果:

- 長い線で大きい（Python ループ削減）。

### P1（高優先）: 出力構築の allocation を減らす（2 パス count → fill）

- [ ] 1 パス目で各線の出力頂点数（および drop 判定）を数える
- [ ] `out_coords/out_offsets` を一括確保し、2 パス目で詰める
- [ ] `results: list[np.ndarray]` + `np.concatenate` を回避し、短線大量での固定費（小配列の多量生成）を下げる
- [x] `offsets` 構築を list→固定長 `np.ndarray` に変更（小改善）
  - 実装: `src/grafix/core/effects/trim.py` の `_lines_to_realized()`

期待効果:

- 線本数が多いケースでメモリ/GC と Python オーバーヘッド削減。

### P2（任意）: Numba 化（P0/P1 で不足する場合）

- [x] トリム処理を `@njit` に寄せ、ループを JIT（距離計算・探索・出力 fill を一体化）
  - 実装: `_trim_polyline_nb()`
- [x] テスト/ベンチが動作することを確認

注意:

- 初回コンパイルコストがある（ウォームアップ計測が必要）。

## 検証（変更ごとに回す）

- [x] テスト
  - `PYTHONPATH=src pytest -q tests/core/effects/test_trim.py`
- [ ] lint/type（変更範囲に応じて）
  - `ruff check .`
  - `mypy src/grafix`
- [x] ベンチ（ベースラインと同条件で再計測し、`mean_ms` を比較）
  - 実測（after）: `polyline_long mean_ms≈0.234` / `ring_big mean_ms≈0.030`（`data/output/benchmarks/trim_after_20251219/results.json`）

メモ:

- ruff は手元環境に未導入（`command not found`）。必要なら `pip install -e ".[dev]"` 後に再実行。
- mypy は既存の未解決エラーが多く、現状は「全体を通す」前提になっていない（本件では trim のロジック/テスト優先）。

## Done の定義（受け入れ条件）

- [ ] 代表ケースで実測の改善が出ている（目標値達成）
  - 現状: before 未計測。after は上のベンチ結果を記録済み。
- [x] `tests/core/effects/test_trim.py` が通る
- [x] 既存仕様（no-op 条件、線 drop 条件）が維持される（テストで担保）

## 事前確認したいこと（あなたに質問）

- [x] 実データで遅い傾向の入力が「長い 1 本」「閉リング」であることを確認（上の 2 ケース）
- [x] 出力の同等性: 微小な数値差（float 演算差）は許容か、完全一致が必要か；許容
- [x] Numba 経路（P2）まで踏み込んで良いか（初回コンパイルコストと引き換え）；はい
