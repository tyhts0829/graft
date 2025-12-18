# Phase 1 実装チェックリスト（2025-12-17）

目的: 計測結果（`indices` / `render_layer` 支配）に対して、multiprocess 以前に「毎フレームの仕事」を減らす。

前提:

- 計測手順は `docs/memo/performance.md` を参照。
- 実装計画の全体像は `improvement_realize_multiprocess.md` を参照。

## Phase 1A: indices（CPU）を減らす

- [x] `build_line_indices(offsets)` に offsets 内容ベースの LRU キャッシュを入れる（同一 offsets なら再計算しない）
- [x] `build_line_indices(offsets)` の実装を「頂点ごとの Python ループ」から「ポリライン単位 + NumPy」へ変更する（キャッシュミス時の高速化）
- [x] `tests/` に `build_line_indices` の同値性テストを追加する（空/単一/複数/スキップケース）

## Phase 1B: render_layer（upload/VAO/GL 呼び出し）を減らす

- [x] `LineMesh._ensure_capacity()` が “容量不足のときだけ” VAO を張り直すように修正する（毎フレーム/毎レイヤーの VAO 再生成を止める）
- [x] VAO 張り直し時に古い VAO を `release()` してリークを止める

## Phase 1C: ループの余計な遅延を避ける

- [x] `MultiWindowLoop` の sleep を「次の予定時刻までだけ sleep / 遅れていたら sleep しない」に修正する

## Phase 1（実装後）: 再計測

- [x] `many_vertices` を再計測し、`indices` が下がることを確認する
- [x] `many_layers` を再計測し、`render_layer` が下がることを確認する
- [x] 結果を `improvement_realize_multiprocess.md` に追記する（代表値と次の支配項）

再計測メモ（代表値）:

- `many_vertices`（segments=200000）: `indices≈18ms -> 0.003ms`（支配項が `scene` に移動）
- `many_layers`（layers=500）: `render_layer≈202ms -> 196–199ms`（支配項は継続）

## 事前確認したいこと（必要なら追記）

- [ ] （必要なら）`GL_LINES` では primitive restart が不要なので、将来的に indices から restart を除去しても良いか
