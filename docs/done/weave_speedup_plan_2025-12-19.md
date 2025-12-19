# weave.py 高速化計画（2025-12-19）

対象: `src/grafix/core/effects/weave.py`

目的: `weave` effect の実行時間/メモリを改善し、同等品質（ウェブ構造・緩和挙動）を維持したまま体感を速くする。

前提:

- 公開 API（`@effect` な `weave(...)` の引数/返り値）は変えない。
- 乱数は現状どおり「実質 deterministic」（seed 固定/計算由来）を維持する。
- “必要十分な堅牢さ”の範囲で、過度な防御や互換ラッパーは作らない。

## 0) 現状観察（ボトルネック候補）

- `merge_edges_into_polylines()` の `visited_edges = np.zeros(num_nodes * num_nodes, ...)` が **O(N^2)** のメモリ/初期化コスト（N=ノード数）。
- `elastic_relaxation_nb()` が各 iteration で `forces = np.zeros((n, 2), ...)` を再確保（反復回数が多いと allocation が支配的になり得る）。
- `create_web_nb()` の「候補線ごとに全エッジ走査」ループは `num_candidate_lines` が大きいと素直に重い（ただし Numba 化済みなので改善余地は“アルゴリズム/割り切り”寄り）。
- `_webify_single_polyline()` が `transform_back()` を “ポリライン単位”で繰り返し呼び、`astype`/copy が多い可能性。

## 1) 計測（最初にやる）

- [ ] ベンチ入力を決める（最低 3 ケース）
  - [ ] 小: 頂点 64 / lines 50 / relax 10
  - [ ] 中: 頂点 256 / lines 100 / relax 15（デフォ相当）
  - [ ] 大: 頂点 1024 / lines 500 / relax 50（上限）
- [ ] JIT 影響を分離して計測する（warmup 1 回 + 本計測 N 回）
- [ ] ステージ別の粗い内訳を出す
  - [ ] `transform_to_xy_plane`
  - [ ] `create_web_nb`
  - [ ] `merge_edges_into_polylines`
  - [ ] `transform_back` + `_lines_to_realized`
- [ ] 目標値を決める（例: “中ケースで 2x 以上” など）

## 2) 施策A（最優先）: 辺訪問管理を O(E) に落とす

狙い: `merge_edges_into_polylines()` の `num_nodes * num_nodes` を廃止し、`num_edges` に比例する visited 管理へ変更する。

- [x] `build_adjacency_arrays()` を「隣接ノード + 隣接エッジID」を返す形に変更
  - [x] `adjacency_nodes: (num_nodes, max_degree)`
  - [x] `adjacency_edge_ids: (num_nodes, max_degree)`（各スロットが対応する edge index）
- [x] `visited_edges = np.zeros(num_edges, dtype=np.bool_)` に置き換え
- [x] `trace_chain` / `trace_cycle` を “(current, neighbor) から edge_id を得て visited を更新” する形に書き換え
- [x] 受け入れテスト
  - [x] 既存 `tests/core/effects/test_weave.py` の閉曲線ケースが通ることを確認
  - [x] 開ポリラインが no-op になるテストを追加（後述）

期待効果:

- 大ケースでメモリと初期化時間が目に見えて減る（N^2 → E）。

## 2.5) 施策A'（実入力対策）: 開ポリラインは no-op

実入力（`verts=50000 lines=1 closed_lines=0`）が「閉曲線ではない」ため、`weave` を適用せず入力を返す。

- [x] `weave()` で「始点と終点が一致しないポリライン」はそのまま返す（重い処理へ入らない）

## 3) 施策B（高優先）: 弾性緩和の allocation を削る

- [ ] `elastic_relaxation_nb()` の `forces` をループ外で 1 回だけ確保し、各 iteration でゼロクリアする
- [ ] “固定ノード”についてはクリップ/更新処理をスキップする（`if not fixed[i]` 側へ寄せる）
- [ ] ベンチで `relaxation_iterations` を増やしたときに比例改善が出ることを確認

## 4) 施策C（中優先）: 交点選択の定数コスト削減（min2 を逐次更新）

現状は「交点を最大 20 個まで配列に溜めてから最小 2 個を探索」している。
必要なのは最小 2 個なので、走査中に `min1/min2` を更新して配列確保/追記を無くす。

- [ ] `create_web_nb()` の交点収集を “2 最小追跡” に置換（`idx1/idx2` と `(ix,iy)` も保持）
- [ ] 交点数が多いケースでも結果が変わらないことを確認（「上位 2 つだけ使う」意味で等価）

## 5) 施策D（中〜低優先）: 変換コストをまとめる

狙い: `transform_back()` の “ポリライン単位”呼び出しを減らし、copy/astype を減らす。

候補:

- [ ] D1: `transform_back()` 内の不要な `astype`/copy を削る（入力 dtype 前提を揃える）
- [ ] D2: `create_web()` を「packed（coords+offsets 相当）」で返せる内部関数にし、最後に 1 回だけ座標変換する

注意:

- 実装変更量が増えるので、A/B の効果が不足した場合に着手する。

## 6) 施策E（任意）: dtype/配列設計の見直し

- [ ] `float64` → `float32` の影響をベンチで比較（速度/品質）
- [ ] `max_degree=10` の固定値が実際に安全か確認（危険なら “degrees.max() を使って確保” に変更）

## 7) Done の定義（受け入れ条件）

- [ ] 主要ベンチ（中ケース）で実測 2x 以上、もしくは “体感で引っかかりが消える”程度の改善
- [x] 大ケースでメモリ使用量が悪化しない（少なくとも N^2 配列は作らない）
- [ ] `PYTHONPATH=src pytest -q` が通る
- [ ] `ruff check .` / `mypy src/grafix` を（変更範囲に応じて）通す

## 8) 事前確認したいこと

- [x] 実運用の入力規模（頂点数レンジ、`num_candidate_lines`/`relaxation_iterations` の典型値）
  - [x] `verts=50000 lines=1 closed_lines=0`（開ポリライン、スキップ許可）
- [ ] “出力の同等性”の許容範囲（完全一致が必要か、微小な数値差は許容か）
