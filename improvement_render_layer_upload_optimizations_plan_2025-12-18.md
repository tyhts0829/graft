# 目的
`render_layer` のうち「GPU upload 側」のコストを下げる。

対象アイデア:

- `np.ascontiguousarray(..., dtype=...)` のコピーを消す（入力を最初から揃える）
- `orphan()` を毎回やらない（必要時のみ + 簡易ダブルバッファ）
- 転送量を減らす（条件が合えば `uint16` index）

## 前提
- 現状は `DrawWindowSystem` がレイヤーごとに `DrawRenderer.render_layer()` を呼び、内部で必要なら `LineMesh.upload()` → `vao.render()` している。
- ここで言う「upload 側」は主に `LineMesh.upload()`（VBO/IBO への `write()` とその前後）を指す。

---

# Phase U0（任意・推奨）: upload と draw call を分離して計測
目的: 最適化の当たり所を誤らないため。

- [ ] `render_layer` の内訳を `render_upload` / `render_draw_call` に分割して perf セクションを追加する。
- [ ] `sketch/perf_sketch.py` の `polyhedron` / `static_layers` / `upload_skip` で再計測し、どちらが支配かを確認する。

※本計画の Phase U1/U2/U3 の効果確認が楽になる。

---

# Phase U1: 入力を最初から contiguous + dtype を揃え、upload 内コピーを消す
狙い: `LineMesh.upload()` 内の `np.ascontiguousarray(vertices, dtype=float32)` / `np.ascontiguousarray(indices, dtype=uint32)` を「毎回」実行しない。

## 方針
- `RealizedGeometry` の不変条件に **C-contiguous** を追加し、`RealizedGeometry` を作った時点で 1 回だけ揃える。
- `build_line_indices()` は（現状どおり）連続メモリの `uint32` を返す前提で、`LineMesh.upload()` 側では変換をやめる（or 最小限にする）。

## 実装チェックリスト
- [ ] `src/graft/core/realized_geometry.py` の `RealizedGeometry.__post_init__()` で:
  - [ ] `coords` を `np.ascontiguousarray(coords, dtype=np.float32)` にする（2D→3D補完後）
  - [ ] `offsets` を `np.ascontiguousarray(offsets, dtype=np.int32)` にする
  - [ ] `writeable=False` は維持する
- [ ] `src/graft/interactive/gl/line_mesh.py` の `upload()` で:
  - [ ] `vertices_f32 = np.ascontiguousarray(...)` / `indices_u32 = ...` を削る（または `np.asarray` 程度に縮める）
  - [ ] `vertices` は `float32` contiguous、`indices` は整数 contiguous を前提として扱う
- [ ] 既存テストが壊れないことを確認する（最低限: `pytest -q tests/interactive/test_index_buffer.py`）。
- [ ] （任意）`RealizedGeometry` の contiguous を保証する単体テストを追加する（必要なら）。

## 再計測
- [ ] `GRAFT_PERF=1` で `polyhedron` と `upload_skip` を再計測し、`render_layer`（または `render_upload`）が下がることを確認する。

---

# Phase U2: `orphan()` を毎回やらない（必要時のみ）+ 簡易ダブルバッファ
狙い: `orphan()` の固定費を削りつつ、GPU が参照中のバッファを上書きして stall しない。

## 重要な注意（この最適化が効く条件）
- 1つの `LineMesh` を **1フレーム内で複数回 upload→draw** すると、`orphan()` を外したときに stall/同期が発生しやすい。
  - 現状の scratch 経路は「キャッシュに乗らないレイヤー」を scratch 1個に upload して描いているため、レイヤー数が多いフレームだと複数回 upload し得る。
- よって、最初に「1フレームあたり scratch upload 回数」をざっくり把握し、2 重（または 3 重）バッファで足りる前提か確認する。

## 方針（シンプル優先）
- `DrawRenderer` の scratch を 2 個（必要なら 3 個）に増やし、upload するたびにローテーションする。
- `LineMesh.upload()` を「orphan する/しない」を選べるようにし、scratch 経路では `orphan=False` を試す。
  - もし 1フレーム内に scratch の再利用が起きるなら、再利用時だけ `orphan=True` に戻す（安全側）。

## 実装チェックリスト
- [ ] `src/graft/interactive/gl/draw_renderer.py` で:
  - [ ] `self._scratch_mesh` を `self._scratch_meshes: list[LineMesh]` に変更（例: 2 個）
  - [ ] `render_layer()` 内で scratch を round-robin で選ぶ
  - [ ] 「このフレームで scratch が何回使われたか」をローカルに把握し、同フレーム再利用が起きる場合の挙動（orphan へフォールバック等）を決める
- [ ] `src/graft/interactive/gl/line_mesh.py` で:
  - [ ] `upload()` に `use_orphan: bool` を足す（または `upload_orphan()` / `upload_write()` に分ける）
  - [ ] `use_orphan=False` のときは `orphan()` を呼ばず `write()` のみにする
- [ ] `upload_skip`（2 layer 同一 geometry）と `many_layers/static_layers` を含めて「見た目が壊れない」ことを確認する。

## 再計測
- [ ] `polyhedron` と「動的でキャッシュに乗らない」ケースで `render_layer` が下がるか確認する。
- [ ] 初回だけ速くなって後で壊れる/ちらつく場合は、この Phase を撤退する（orphan を維持）。

---

# Phase U3: 条件が合えば `uint16` indices で IBO 転送量を半減
狙い: インデックス転送量（IBO）を下げる。

## 適用条件（最低限）
- `max_vertex_index <= 65535`（= 頂点数が 65535 未満のメッシュが対象）
- `LINE_STRIP + primitive restart` の restart index を `uint16` と整合させる必要がある

## 方針（既存の `build_line_indices()` を極力変えない）
- `build_line_indices()` は引き続き `uint32`（PR は 0xFFFFFFFF）を返す。
- `LineMesh.upload()` 側で:
  - `uint16` に収まると判断したら `indices.astype(np.uint16, copy=False)` にする（PR は 0xFFFF へ自然に変換される）
  - VAO の `index_element_size=2` を使う（必要なら VAO を張り直す）
  - `primitive_restart_index` を **index サイズに応じて**設定する（0xFFFF / 0xFFFFFFFF）

## 実装チェックリスト
- [ ] `src/graft/interactive/gl/line_mesh.py` で:
  - [ ] `index_element_size`（2 or 4）を `LineMesh` の状態として持つ
  - [ ] upload 時に「u16 でいけるか」判定し、必要なら indices を u16 化する
  - [ ] `simple_vertex_array(..., index_element_size=...)` を使い分ける（変更時は VAO を作り直す）
- [ ] `src/graft/interactive/gl/draw_renderer.py` で:
  - [ ] 描画直前に `ctx.primitive_restart_index` をメッシュの index サイズに合わせて設定する（混在に対応）
- [ ] `sketch/perf_sketch.py` で `segments < 65535` のケース（例: `GRAFT_SKETCH_SEGMENTS=50000`）を流し、IBO が支配する状況で差が出るか確認する。

## 再計測
- [ ] `many_vertices` を「u16 が使える分割数」にして比較する（初回のウォームアップは除外）。

---

# 期待効果の目安（ざっくり）
- U1: 毎フレームの不要コピー削減（転送量が大きいほど効く）
- U2: `orphan()` 固定費が支配している場合に効く（ただし状況次第で逆効果もあり得る）
- U3: IBO 転送が支配している場合に効く（頂点数が 65535 未満のとき限定）

# 進め方（推奨順）
1. U0（計測分割）→ U1（低リスク）→ 再計測
2. まだ upload が支配なら U2（ただし安全性優先で段階導入）→ 再計測
3. IBO 転送が支配しているケースがあるなら U3 → 再計測
