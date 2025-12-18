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
  - `src/grafix/interactive/runtime/`（ループ/スケジューリング/非同期）
  - `src/grafix/interactive/gl/`（indices/GPU 転送の最適化とキャッシュ）
  - `src/grafix/core/pipeline.py`（関数分割のみ。mp の知識は入れない）
- mp の分岐は `DrawWindowSystem` に散らさず「同期/非同期 Scene 供給器」を差し替える形に寄せる。
- Parameter/GUI は「ワーカーは snapshot を読むだけ」「観測（records/labels）を返すだけ」「マージはメインで 1 箇所」に固定する。
- 依存追加はしない（既存の numpy/numba などは利用可）。

## Phase 0（先にやる）: 計測で CPU/GPU の支配項を切り分ける

狙い: 「CPU が詰まっている」のか「GPU/転送が詰まっている」のかで、打ち手が真逆になるため。

- 有効化（interactive）:
  - `GRAFIX_PERF=1` で有効化（一定フレームごとに標準出力へ集計を出す）
  - `GRAFIX_PERF_EVERY=60` で出力間隔（フレーム数）
  - `GRAFIX_PERF_GPU_FINISH=1` で `ctx.finish()` を含む同期計測を有効化
- 出力ラベル（現在）:
  - `frame`（draw_frame 全体）, `draw`（user draw）, `scene`（realize_scene）, `indices`（build_line_indices）, `render_layer`（upload+draw 呼び出し）, `gpu_finish`（同期待ち）
- 最低限の区間計測を入れて、フレーム内の割合を可視化する。
  - 例: `draw` / `realize` / `build_line_indices` / `LineMesh.upload` / `render`。
- GPU 側が疑わしい場合の補助:
  - 計測用に `ctx.finish()`（または同等）で同期して “その区間が GPU 待ちを含むか” をざっくり見る。
  - （注意）同期計測は挙動を変えるので、恒常的な処理にはしない。

### 計測結果（2025-12-17, `sketch/perf_sketch.py`）

前提:

- `draw` は `scene` の内側（subset）として計測されるため、`draw + scene` のように足さない。
- `GRAFIX_PERF_GPU_FINISH=1` は同期待ちを強制するため、数値は「診断用の目安」として扱う。

#### Case: `cpu_draw`（draw 支配）

実行:

- `GRAFIX_SKETCH_CASE=cpu_draw GRAFIX_SKETCH_CPU_ITERS=500000 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`

結果（代表値）:

- `frame≈50ms`, `draw≈48.7ms`, `scene≈48.8ms`, `render_layer≈1.0–1.2ms`, `indices≈0.03ms`

解釈:

- ほぼ `draw(t)` の CPU が支配的（`realize_scene` の残りはごく小さい）。
- このタイプは **Phase 2（mp-draw）** が効く想定。

再計測（Phase 1A/1B/1C 実装後）:

- `frame≈49.6ms`, `draw≈48.2–48.7ms`, `scene≈48.3–48.7ms`, `render_layer≈1.0–1.2ms`, `indices≈0.005ms`
- 支配項は変わらず `draw`。Phase 2 の go 条件を満たす。

#### Case: `many_vertices`（indices 支配）

実行:

- `GRAFIX_SKETCH_CASE=many_vertices GRAFIX_SKETCH_SEGMENTS=200000 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF_GPU_FINISH=1 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`

結果（代表値）:

- `frame≈26.6–28.6ms`, `indices≈17.8–18.1ms`, `scene≈6.0–7.8ms`, `render_layer≈1.7–2.1ms`, `gpu_finish≈0.69ms`, `draw≈0.09ms`

解釈:

- `build_line_indices(offsets)` が支配的で、ここを削るのが最短。
- このスケッチはトポロジが固定（`offsets` が毎フレーム同じ）なので、**indices キャッシュ**だけで大きく改善できる見込み。
- `draw` が極小のため mp-draw はほぼ効かない。GPU 同期も小さく、GPU ボトルネックの可能性は低い。

再計測（Phase 1A/1B/1C 実装後）:

- `frame≈8.6–9.3ms`, `indices≈0.003ms`, `scene≈6.2–6.7ms`, `render_layer≈1.5–1.8ms`, `gpu_finish≈0.69–0.76ms`
- `indices` は解消。新しい支配項は `scene`（主に realize 側）へ移動。

#### Case: `many_layers`（render_layer 支配）

実行:

- `GRAFIX_SKETCH_CASE=many_layers GRAFIX_SKETCH_LAYERS=500 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`

結果（代表値）:

- `frame≈229–232ms`, `render_layer≈200–203ms (500x)`, `scene≈21ms`, `draw≈10–11.5ms`, `indices≈1.42–1.44ms (500x)`

解釈:

- 1 フレームで `render_layer()` を 500 回呼んでおり、upload/VAO/GL 呼び出しのオーバーヘッドが支配的。
- mp-draw を入れても支配項が動かないので優先度は低い。
- **Phase 1B（GPU upload/VAO まわりの最適化）**が最優先。
  - 特に現状の `LineMesh._ensure_capacity()` が、容量が足りている場合でも毎回 VAO を作り直しているため、レイヤー数が多いと致命的になりやすい。

再計測（Phase 1A/1B/1C 実装後）:

- `frame≈222–225ms`, `render_layer≈196–199ms (500x)`, `indices≈0.63–0.64ms (500x)`, `scene≈20–21.5ms`
- `indices` は半減したが、支配項は引き続き `render_layer`。
- 500 回/フレームの upload+draw 呼び出しが支配的なので、次の打ち手は「同一 layer の upload スキップ（静的向け）」と「呼び出し回数削減（バッチ化等）」が本命。

### 再計測のタイミング（いつ・何を測るか）

原則:

- 「変更 → 同じ条件で再計測 → 支配項の移動を確認」のループで進める。
- 1 回目の出力はウォームアップが混ざりやすいので、**2 回目以降**（安定後）を比較対象にする。
- 計測はまず `GRAFIX_SKETCH_PARAMETER_GUI=0` を推奨（ノイズ低減）。GUI 影響も見たいときだけ `=1` で別枠計測する。

ベースライン（Phase 0 完了時 / 大きな方向性の確認）:

- `cpu_draw` / `many_vertices` / `many_layers` を一通り測って「どれが支配的か」を把握する。

Phase 1A（indices キャッシュ/高速化）後:

- `many_vertices` を再計測して `indices` が下がることを確認する。
  - 例: `GRAFIX_SKETCH_CASE=many_vertices GRAFIX_SKETCH_SEGMENTS=200000 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`
- `indices` が支配のままなら 1A を深掘り（キャッシュキー/実装/numba）。支配項が `render_layer` や `scene` に移ったら次の項目へ進む。

Phase 1B（GPU upload/VAO まわり）後:

- `many_layers` を再計測して `render_layer` が下がることを確認する。
  - 例: `GRAFIX_SKETCH_CASE=many_layers GRAFIX_SKETCH_LAYERS=500 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`
- ここで `gpu_finish` が急に増える場合だけ、診断用に `GRAFIX_PERF_GPU_FINISH=1` を付けて GPU 待ちの可能性を確認する（常用しない）。

Phase 1C（sleep 見直し）後:

- 軽いケース（既定 `polyhedron` など）で `frame` の平均が改善/悪化していないことと、重いケースで「余計に遅くなっていない」ことを確認する。

Phase 2（mp-draw）実装前後:

- `cpu_draw` を再計測して、メインスレッドが詰まらず入力/描画が滑らかになるかを確認する。
  - mp 無効（比較用）: `GRAFIX_SKETCH_CASE=cpu_draw GRAFIX_SKETCH_CPU_ITERS=500000 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`
  - mp 有効: `GRAFIX_SKETCH_CASE=cpu_draw GRAFIX_SKETCH_CPU_ITERS=500000 GRAFIX_SKETCH_N_WORKER=4 GRAFIX_SKETCH_PARAMETER_GUI=0 GRAFIX_PERF=1 GRAFIX_PERF_EVERY=60 python sketch/perf_sketch.py`
  - 注意: mp 有効時は `draw` 区間が worker 側へ移るため、メインの perf 出力では `draw` は測れない（`scene` は主に realize 側になる）。
- go/no-go:
  - Phase 1 後も **実スケッチで `draw` が支配的**なら Phase 2 を進める。
  - `indices` / `render_layer` が支配的なら Phase 2 の優先度は低い（Phase 1 を深掘り）。

実スケッチ（ユーザーの描画）での再計測:

- Phase 1 の各サブ項目を入れるたびに 1 回、そして Phase 1 完了時に 1 回、同じ環境変数で測って差分を見る。

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

- [x] Phase 0 の区間計測を入れて支配項を把握する（CPU/GPU/転送の切り分け）
- [x] indices キャッシュ導入（offsets 内容ベース）
- [x] `build_line_indices` 高速化（キャッシュミス時もポリライン単位 + NumPy）
- [x] `LineMesh._ensure_capacity()` の VAO 張り直し条件を修正（毎フレーム/毎レイヤーの VAO 再生成を止める）
- [ ] `build_line_indices` の numba 版追加（必要なら。JIT ヒッチ対策も検討）
- [x] GPU メッシュキャッシュ導入（`geometry.id` キーで同一ジオメトリの upload をスキップ）
- [x] `tobytes()` を避けた upload（NumPy 配列をそのまま `write()`）
- [x] `MultiWindowLoop` の sleep 見直し
- [x] 小さなテスト追加（indices の同値性、キャッシュが効くこと）

### Phase 1 変更ファイル（案）

- `src/grafix/interactive/gl/index_buffer.py`（indices キャッシュ/高速化）
- `src/grafix/interactive/gl/draw_renderer.py`（GPU キャッシュ）
- `src/grafix/interactive/gl/line_mesh.py`（必要なら最小の分離・再利用 API 追加）
- `src/grafix/interactive/runtime/window_loop.py`（sleep 見直し）
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

- [x] `src/grafix/interactive/runtime/mp_draw.py` を追加（Queue + spawn。ワーカー target はトップレベル関数）
- [x] ワーカー初期化で `grafix.api.primitives` / `grafix.api.effects` を import（組み込み op 登録）
- [x] worker 用の snapshot context を追加（records/labels を回収できる形）
- [x] `run(..., n_worker=...)` を追加（`<=1` は無効、`>=2` で有効）
- [x] `DrawWindowSystem` に mp-draw 分岐を追加（結果をマージして描画）
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
