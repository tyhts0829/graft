# 描画パフォーマンス改善（段階的）実装計画

## 目的

- 線/頂点が多い複雑な描画でも「カクつき（フレーム落ち / 入力停止）」を抑える。
- 実装・構造を汚さない（変更箇所を局所化、既存 API を壊さない）。
- 効きやすい順に Phase 1 → 2 → 3 で進め、各 Phase の効果を確認してから次へ進む。

## 現状の 1 フレーム処理（概略）

- `MultiWindowLoop` が window ごとに `dispatch_events()` → `draw_frame()` → `flip()` を繰り返す。
- 描画ウィンドウの `draw_frame()` は概ね以下:
  - Style（背景/線幅/線色）確定（store を直接参照）
  - `parameter_context(store)` を張る
  - `realize_scene(draw, t, defaults)`（内部で `draw(t)` / `normalize_scene` / `realize`）
  - `build_line_indices(offsets)`（CPU）
  - `LineMesh.upload(vertices, indices)`（GPU 転送）
  - render

## ボトルネック候補（stutter に効きやすい順）

- indices 生成: `build_line_indices(offsets)` が Python ループで巨大配列を作る
- GPU 転送: `LineMesh.upload()` が毎レイヤー毎フレーム orphan+write
- `realize()` の CPU コスト（effect/primitive 次第）
- `draw(t)` の CPU コスト
- ループの固定 `sleep(1/fps)` が「重いフレーム時ほど」余計に遅くなる可能性

## 構造を汚さないための原則

- 変更は 3 箇所に寄せる:
  - `src/graft/interactive/runtime/`（ループ/スケジューリング/非同期）
  - `src/graft/interactive/gl/`（indices/GPU 転送の最適化とキャッシュ）
  - `src/graft/core/pipeline.py`（関数分割のみ。mp の知識は入れない）
- mp の分岐は `DrawWindowSystem` に散らさず「同期/非同期 Scene 供給器」を差し替える形に寄せる。
- Parameter/GUI は「ワーカーは snapshot を読むだけ」「観測（records/labels）を返すだけ」「マージはメインで 1 箇所」に固定する。
- 依存追加はしない（既存の numpy/numba などは利用可）。

## Phase 0（先にやる）: 計測で CPU/GPU の支配項を切り分ける

狙い: 「CPU が詰まっている」のか「GPU/転送が詰まっている」のかで、打ち手が真逆になるため。

- 最低限の区間計測を入れて、フレーム内の割合を可視化する。
  - 例: `draw` / `realize` / `build_line_indices` / `LineMesh.upload` / `render`。
- GPU 側が疑わしい場合の補助:
  - 計測用に `ctx.finish()`（または同等）で同期して “その区間が GPU 待ちを含むか” をざっくり見る。
  - （注意）同期計測は挙動を変えるので、恒常的な処理にはしない。

## Phase 1（最優先）: mp なしで “毎フレームの仕事” を減らす

狙い: 頂点数が増えたときの stutter は、mp より先に「indices 生成」と「GPU 転送」を削ると効きやすい。

### 1A. indices を再計算しない / 速くする

- indices は `offsets` だけで決まるので、同じ `offsets` ならキャッシュできる。
  - 例: `offsets.tobytes()` の hash をキーにする、または `RealizedGeometry` の同一性をキーにする。
- まずはキャッシュを優先し、必要なら `build_line_indices` の実装を「頂点ごとのループ」から「ポリラインごとのループ」へ寄せて Python ループ回数を減らす（変更は `index_buffer.py` に閉じる）。
- さらに必要なら `build_line_indices` を numba 化する（2 パス: 出力長カウント → `np.empty` へ書き込み）。
  - 注意: 初回 JIT の一瞬のヒッチが出る可能性があるため、必要なら起動時にウォームアップする。

### 1B. GPU upload を減らす（同じジオメトリは再転送しない）

- **Layer 単位**で「その Layer の描画データが変わったか？」を判定し、変わっていなければ upload をスキップして前回の GPU データのまま描画する。
  - 判定キーはまず `Layer.geometry.id` を採用する（内容署名なので “同じなら同じ” を素直に表現できる）。
  - 色/線幅だけ変わった場合は頂点/インデックスは不変なので、upload は不要で uniform 更新だけで足りる。
- `DrawRenderer` 側に「`geometry.id` → (VBO/IBO/VAO など)」の小さなキャッシュを導入する。
  - 現状は `LineMesh` が 1 個で毎レイヤー上書きしているため、**複数メッシュ（またはメッシュキャッシュ）**へ寄せる必要がある。
  - キャッシュヒット時は upload せずに `vao.render(...)` のみ実行する。
- キャッシュは LRU + 上限（件数/推定バイト）でよい。解放は renderer の `release()` に閉じる。
- `tobytes()` 由来のコピー削減を検討する。
  - `moderngl.Buffer.write()` が許すなら `memoryview(ndarray)`（または `ndarray` そのもの）を渡し、`ndarray.tobytes()` を避ける。
  - 配列は contiguous 前提になるので、必要なら `np.ascontiguousarray` を使う（ただし余計な copy を増やさないように計測して判断する）。

### 1C. ループ sleep の見直し（追いつかないときは余計に寝ない）

- 現状の `sleep(1/fps)` は、重いフレームの後にさらに遅延を積みやすい。
- 「次の予定時刻までだけ sleep」「遅れていたら sleep しない」に変更するのが最小。

### Phase 1 チェックリスト

- [ ] Phase 0 の区間計測を入れて支配項を把握する（CPU/GPU/転送の切り分け）
- [ ] indices キャッシュ導入（キー方針も決める）
- [ ] `build_line_indices` 高速化（必要なら）
- [ ] `build_line_indices` の numba 版追加（必要なら。JIT ヒッチ対策も検討）
- [ ] Layer 単位の GPU バッファキャッシュ導入（`geometry.id` キーで upload をスキップ）
- [ ] `tobytes()` を避けた upload を試す（可能なら memoryview 化。効果は計測で判断）
- [ ] `MultiWindowLoop` の sleep 見直し
- [ ] 小さなテスト追加（indices の同値性、キャッシュが効くこと）

### Phase 1 変更ファイル（案）

- `src/graft/interactive/gl/index_buffer.py`（indices キャッシュ/高速化）
- `src/graft/interactive/gl/draw_renderer.py`（GPU キャッシュ）
- `src/graft/interactive/gl/line_mesh.py`（必要なら最小の分離・再利用 API 追加）
- `src/graft/interactive/runtime/window_loop.py`（sleep 見直し）
- `tests/interactive/...` or `tests/core/...`（小テスト）

## Phase 2: `draw(t)` の multiprocessing 化（Queue, spawn）

狙い: `draw(t)` が支配的なケースで CPU を分散しつつ、メイン（イベント + GL）を詰まらせない。

### 前提 / 制約（固定）

- 通信は `multiprocessing.Queue` を使う（pipe + pickle）。
- `spawn` 前提（macOS/Windows）。
  - 子プロセスへ渡す `draw` は picklable（モジュールトップレベル定義）。
  - ユーザースケッチ側は `if __name__ == "__main__":` ガード必須。
- ワーカーは CPU 計算のみ。pyglet/pyimgui/OpenGL は触らない。
  - `draw` が pyglet/pyimgui/OpenGL を触るスケッチは mp 無効（`n_worker<=1`）を前提とする。

### 設計: フレーム単位の非同期パイプライン（最新結果優先）

- メイン: イベント処理 + Style 確定 + `realize` + indices/GPU キャッシュ + render。
- ワーカー: `draw(t)` 実行（parameter snapshot を固定）→ `normalize_scene` → `list[Layer]` を返す。
- 追いつかない場合は「古い結果は捨てる」。
  - `frame_id` を単調増加で付与し、result queue を drain して最新だけ採用。
  - task queue は `maxsize = n_worker`（in-flight が増えすぎない）。

### Parameter GUI と整合を保つ

- `ParamStore` 本体はメインに残す。
- メインで作った `ParamStore.snapshot()` をワーカーへ渡し、`resolve_params` が参照する `current_param_snapshot` を固定する。
- ワーカーで観測した `FrameParamRecord` と `set_label(...)` 呼び出し履歴を返し、メインで `ParamStore` にマージする。

### Phase 2 チェックリスト

- [ ] `src/graft/core/draw_mp.py` を追加（Queue + spawn。ワーカー target はトップレベル関数）
- [ ] ワーカー初期化で `graft.api.primitives` / `graft.api.effects` を import（組み込み op 登録）
- [ ] worker 用の snapshot context を追加（records/labels を回収できる形）
- [ ] `run(..., n_worker=...)` を追加（`<=1` は無効、`>=2` で有効）
- [ ] `DrawWindowSystem` は Scene 供給器差し替えに寄せる（mp 分岐を散らさない）
- [ ] `pytest` の最小テスト追加（spawn 動作 / 例外伝播 / label 収集）
- [ ] README に制約（`__main__` ガード、top-level 定義、プロセス分離）を追記

## Phase 3（必要なら）: `realize`/indices を含めた並列化を検討

狙い: Phase 1/2 後も `realize` が支配的なケースで追加の打ち手を検討する。

注意:
- `multiprocessing.Queue` で `RealizedGeometry(np.ndarray)` を毎フレーム往復すると pickle コストが増える。
- 先に計測して「本当に `realize` が支配的か」「転送のほうが高くないか」を確認してから判断する。

候補:
- 3A. ワーカーで `realize` まで実行し、メインは GPU upload+draw のみにする（pickle コストとトレードオフ）。
- 3B. indices 生成だけをワーカーへ（ただし配列往復があるので効果は要計測）。

### Phase 3 チェックリスト（検討）

- [ ] Phase 1/2 後のボトルネックを整理（draw/realize/indices/upload）
- [ ] Queue+pickle 往復を含む設計で得か、試作で確認

## 事前に決めたいこと（確認）

1. Phase 1 → 2 → 3 の順で進めてよい？（まず “汚さず効く” 手当てを優先）
2. mp の `n_worker` デフォルトは `0`（無効）で良い？
3. 追いつかない場合は「最新結果優先（古い結果は捨てる）」で良い？
