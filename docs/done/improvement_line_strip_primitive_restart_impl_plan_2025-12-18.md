# LINE_STRIP + Primitive Restart 導入（実装計画 / 2025-12-18）

目的: 旧プロジェクトで行っていた「`LINE_STRIP + primitive restart`」の方式を本リポジトリにも導入し、同一 `coords + offsets` 表現のまま **インデックス量と転送量を削減**する。

前提:

- Geometry は「複数ポリラインの頂点列」を `coords(float32[N,3]) + offsets(int32[M+1])` で表す。
- polylines 同士は繋げず、各 polyline 内は連結して描画したい。
- 可能なら 1 draw call（= 1 layer）に集約したい（polyline ごとの draw call は増やさない）。

非目的（今回やらない）:

- `many_layers` のような「レイヤー数が多い」ケースの **draw call 回数削減**（バッチ化/まとめ描き）は別件。
- export/SVG の表現変更。

## 現状整理（このリポの状態）

- `src/grafix/interactive/gl/index_buffer.py` の `build_line_indices(offsets)` は現在 `GL_LINES` 前提の “エッジ列” を生成している。
- `LineMesh` は `primitive_restart=True` と `primitive_restart_index=0xFFFFFFFF` を設定済み。
- `DrawRenderer` は `vao.render(mode=ctx.LINES, ...)` で描画している。

## 方針（旧プロジェクト方式の再現）

- draw mode を `LINE_STRIP` にする。
- IBO は「各 polyline の頂点 index を 0..N-1 の連番として並べ、polyline 間に `PRIMITIVE_RESTART_INDEX` を挿入」する。
  - 例: `offsets=[0,3,5]` → `indices=[0,1,2,PR,3,4]`
- primitive restart を有効にしたまま 1 draw call で描画する。

期待できる効果（主に upload / memory）:

- polyline 長 k に対し indices が概ね `2*(k-1)` → `k(+PR)` に減る（長い polyline ほど効く）。
- IBO 転送量が減り、`render_layer` が IBO upload 由来で重いケースで効く可能性がある。

## 実装チェックリスト

### 1) index 生成を LINE_STRIP 方式へ

- [x] `src/grafix/interactive/gl/index_buffer.py` に `LINE_STRIP + PR` 用の indices 生成を実装する
  - [x] “頂点が 2 未満の polyline はスキップ”の扱いを明確化（スキップしても polyline 間が繋がらないよう PR を入れる）
  - [x] dtype は `np.uint32` を維持（`PR=0xFFFFFFFF` と一致）
  - [x] offsets 内容ベースの LRU キャッシュは維持（同一 offsets なら再計算しない）
- [x] `build_line_indices` は名称据え置きで仕様を LINE_STRIP 用に更新する
  - 破壊的変更は許容（互換ラッパーは作らない方針）

### 2) renderer の draw mode 切り替え

- [x] `DrawRenderer.render_layer(...)` を `ctx.LINE_STRIP` で描画する
- [x] `LineMesh` の `primitive_restart` 設定をそのまま利用する（`PR=0xFFFFFFFF`）
- [ ] 見た目確認（polyline 同士が繋がらない / 期待通りの線分になる）

### 3) テスト更新

- [x] `tests/interactive/test_index_buffer.py` を LINE_STRIP 方式の期待値に更新する
  - [x] `offsets=[0,3]` → `[0,1,2]`
  - [x] `offsets=[0,3,5]` → `[0,1,2,PR,3,4]`
  - [x] スキップケースも更新する

### 4) 再計測（期待値の確認）

- [ ] `many_vertices`（巨大ポリライン）で IBO 量が減った分、`render_layer` が下がるか確認する
  - `GRAFIX_SKETCH_CASE=many_vertices GRAFIX_SKETCH_SEGMENTS=200000 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`
- [ ] `upload_skip` で 1 フレーム目の “upload あり” コストが下がるかを見る（indices 半減の影響を見たい）
  - `GRAFIX_SKETCH_CASE=upload_skip GRAFIX_SKETCH_UPLOAD_SEGMENTS=500000 GRAFIX_SKETCH_UPLOAD_LAYERS=2 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=1 python sketch/perf_sketch.py`

## リスク / 注意点

- geometry shader は `layout(lines) in;` だが、`LINE_STRIP` は内部的に line segment 列へ分解される想定のため動作する見込み。
  - ただし環境差があり得るので、まずはスケッチで視覚確認する。
- この変更は「polyline 内の連結表現」を変えるだけで、`many_layers` の根本（500 draw call）には効かない。
