# Phase 1B-2（GPU upload skip / tobytes削減）実装チェックリスト（2025-12-18）

目的: `many_layers` のように `render_layer` が支配的なケースで、同一ジオメトリの再 upload を避けて FPS を改善する。

前提:

- 計測手順は `docs/memo/performance.md` を参照。
- 全体計画は `improvement_realize_multiprocess.md` を参照。
- キャッシュキーは原則 `Geometry.id`（内容署名）とし、「同じなら同じ」を素直に使う。
- pyimgui/GL はメインのみ（worker は関係なし）。

## 実装

- [x] `DrawRenderer.render_layer(...)` に `geometry_id` を渡せるようにする（呼び出し側も更新）
- [x] `Geometry.id` ベースの GPU メッシュキャッシュを導入する（ヒット時は upload をスキップ）
  - 初見の `geometry_id` を全部キャッシュすると「毎フレーム別 id」ケースで逆効果なので、**2回目以降にキャッシュへ昇格**する（候補リスト→キャッシュ）
  - キャッシュは LRU（件数上限）で古いものから解放する
- [x] `LineMesh.upload()` の `tobytes()` を廃止し、buffer protocol（NumPy配列/`memoryview`）で `write()` する
- [x] renderer の `release()` でキャッシュした GPU リソースを確実に解放する

## 再計測（手元）

- [ ] `many_layers` を再計測し、`render_layer` が下がる（または少なくとも悪化しない）ことを確認する
  - 例: `GRAFT_SKETCH_CASE=many_layers GRAFT_SKETCH_LAYERS=500 GRAFT_SKETCH_PARAMETER_GUI=0 GRAFT_PERF=1 GRAFT_PERF_EVERY=60 python sketch/perf_sketch.py`
- 注意: `many_layers` は `t` でジオメトリが毎フレーム変わるため、`geometry.id` キャッシュによる upload skip は基本的に発動しない。
  - この再計測は「とりあえず悪化していない（or tobytes削減ぶん僅かに改善）」の確認用。
- [ ] 「静的ジオメトリが多い実スケッチ」で、upload が減ることを確認する（`Geometry.id` が安定するケース）
