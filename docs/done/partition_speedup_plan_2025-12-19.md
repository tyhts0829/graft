# `src/grafix/core/effects/partition.py` 高速化計画（2025-12-19）

## 目的

- `partition` effect（偶奇領域の Voronoi 分割）の処理時間/メモリを短縮する。
- 対象は `src/grafix/core/effects/partition.py`（必要なら周辺ユーティリティまで）。
- 指標はベンチ（`tools/benchmarks/effect_benchmark.py`）と既存テスト（`tests/core/effects/test_partition.py`）の維持。

## 現状メモ（ボトルネック候補）

- 平面推定が `np.linalg.svd(centered)`（`(N,3)`）で重い＆ `float64` コピーが入る。
- 2D 投影で `points - origin` の `(N,3)` 一時配列（`float64`）が発生する。
- Shapely の領域構築が「各リング Polygon → `symmetric_difference` を逐次」で、リング数が増えると重い。
- サイト生成が rejection sampling（`Point` 生成 + `region.covers` を最大 `max(1000, site_count*50)` 回）で、領域が細い/穴が多いほど遅くなりやすい。
- Voronoi セルごとに `cell.intersection(region)` を回し、領域境界が細かい（頂点数が多い）と支配的になりやすい。

## ベースライン計測（先にやる）

- [ ] `partition` 単体のベンチを取得する（HTML/JSON を保存）
  - `PYTHONPATH=src python -m tools.benchmarks.effect_benchmark --only partition --cases ring_big,rings_2 --repeats 10 --warmup 2 --disable-gc`
- [ ] 最遅ケースを特定し、主因が「平面変換」か「Shapely（XOR / covers / intersection）」かを切り分ける（`cProfile` で OK）
  - `PYTHONPATH=src python -m cProfile -o /tmp/partition.prof -m tools.benchmarks.effect_benchmark --only partition --cases ring_big --repeats 3 --warmup 1 --disable-gc`
- [ ] 目標値を決める（例: 最遅ケースで 1.5x 〜 2x）

## 改善案（優先順）

### P0（挙動をほぼ変えずに効く可能性が高い）

- [ ] 平面推定の fast-path（XY）を追加する
  - 例: `z` の範囲（`ptp(z)`）が閾値以下なら「XY 平面」とみなして SVD/eigh をスキップ
  - ねらい: ベンチケース（z=0）や一般的な 2D 入力で固定費を消す
- [ ] `_fit_plane_basis()` の「フル SVD」をやめ、`3x3` 共分散の固有分解（`np.linalg.eigh`）へ置換する
  - ねらい: `O(N)` の集計 + 小行列 eig にして、SVD と巨大中間配列を削減
- [ ] `_project_to_2d()` を「`(points - origin)` を作らない」式へ変更する
  - 例: `x = points @ u - origin·u`, `y = points @ v - origin·v`
  - ねらい: `(N,3)` 一時配列を消してメモリ帯域を節約
- [ ] サイト生成を Shapely 2.x の vectorized predicate（例: `contains_xy`/`covers_xy` 系）で一括判定する
  - 方針: 乱数で候補点をまとめて生成 → ベクタ化 predicate で内点だけ抽出 → 足りるまでバッチ反復
  - ねらい: `Point` 生成 + Python ループ + `region.covers(...)` の固定費を削減
- [ ] `prepared geometry`（`shapely.prepared.prep(region)`）も試し、上記より速い方を採用する
- [ ] ループのソート key を軽くする（外周は常に閉じている前提で `loop[:-1].mean(...)` に固定、`allclose` をやめる）

### P1（Shapely 呼び出し回数/重さを下げる）

- [ ] （`rings_2` 最適化）2 リングで「一方が他方を包含」する場合は `Polygon(outer, holes=[inner])` で領域を構築する
  - ねらい: `symmetric_difference`（overlay）を回避して領域構築を軽くする（外周+穴の典型ケース）
  - フォールバック: 交差/包含でない場合は現状どおり XOR
- [ ] 偶奇領域構築を「XOR 逐次」から軽い形へ寄せる（入力が素直な場合のみ）
  - 方針: リングが非交差・包含関係（外周+穴+島）だけで表現できる場合は、包含ツリーを作って `Polygon(shell, holes)` の集合へ変換し、`unary_union` で確定
  - フォールバック: 判定が怪しい/交差がある場合は現状どおり `symmetric_difference`
  - ねらい: リング数が多いケースで overlay 回数を減らす
- [ ] Voronoi 交差前に領域境界を軽くする（必要なら）
  - 例: `region = region.simplify(tol, preserve_topology=True)`（※挙動変化の許容が必要）

### P2（効果は大きい可能性があるが、仕様/依存に触れる）

- [ ] rejection sampling をやめ、領域の三角化ベースでサイトを生成する（薄い領域でも安定）
  - ねらい: 失敗試行（trials）による遅さを根本解消
  - 代償: 実装増（ただし Shapely の `triangulate` 等が使えるなら比較的単純）
- [ ] 追加パラメータを導入する（必要なら）
  - 例: `max_trials`, `simplify_tolerance`, `site_strategy`（現状は固定値なので調整不可）

## 検証（変更ごとに回す）

- [ ] テスト
  - `PYTHONPATH=src pytest -q tests/core/effects/test_partition.py`
- [ ] ベンチ（ベースラインと同条件で再計測し、`mean_ms` を比較）
  - `PYTHONPATH=src python -m tools.benchmarks.effect_benchmark --only partition --cases ring_big,rings_2 --repeats 10 --warmup 2 --disable-gc`

## Done の定義（受け入れ条件）

- [ ] 最優先ケースで実測の改善が出ている（目標値達成）
- [ ] `tests/core/effects/test_partition.py` が通る
- [ ] 既存仕様（非共平面は no-op、同一入力+seed の決定性、閉ループ出力）が維持される

## 事前確認したいこと（あなたに質問）

- [x] 最優先ケース
  - `ring_big`（正多角形リング）: `verts=5001 lines=1 closed_lines=1`
  - `rings_2`（外周+穴）: `verts=3202 lines=2 closed_lines=2`
- [ ] 「出力形状が少し変わっても良い」高速化（simplify / 生成サイトの分布変更）まで踏み込むか
- [x] Shapely はすでに 2.x 系（vectorized predicate を前提にして良い）
