# interactive perf 計測メモ

## 目的

- 1 フレームの中で「CPU（draw/realize/indices）」「GPU/転送（upload/draw）」のどこが支配的かを切り分ける。
- stutter（カクつき）対策の優先順位を間違えないための、最小の区間計測を提供する。

## 仕組み（実装）

- `src/graft/interactive/runtime/perf.py` の `PerfCollector` が、区間時間を集計して N フレームごとに平均値を出力する。
- `src/graft/interactive/runtime/draw_window_system.py` が `draw_frame()` 内で以下を計測する:
  - `frame`: `draw_frame()` 全体
  - `scene`: `realize_scene(...)` 全体（内部で `draw(t)` を含む）
  - `draw`: user `draw(t)`（`scene` の内側＝subset）
  - `indices`: `build_line_indices(offsets)`
  - `render_layer`: `render_layer(...)`（upload + draw 呼び出し）
  - `gpu_finish`: `ctx.finish()`（診断用。明示的同期待ち）

## 使い方（共通）

### 最小の実行例

```bash
GRAFT_PERF=1 GRAFT_PERF_EVERY=60 python sketch/perf_sketch.py
```

### GPU 側が怪しいとき（診断用）

```bash
GRAFT_PERF=1 GRAFT_PERF_EVERY=60 GRAFT_PERF_GPU_FINISH=1 python sketch/perf_sketch.py
```

### Parameter GUI を切って測る（任意）

```bash
GRAFT_SKETCH_PARAMETER_GUI=0 GRAFT_PERF=1 GRAFT_PERF_EVERY=60 python sketch/perf_sketch.py
```

## 環境変数（Perf）

- `GRAFT_PERF=1` : 計測を有効化する（既定は無効）。
- `GRAFT_PERF_EVERY=60` : 何フレームごとに出力するか（既定 60）。
- `GRAFT_PERF_GPU_FINISH=1` : `ctx.finish()` を呼び、GPU 同期待ち込みで計測する（既定は無効）。
  - 注意: 同期待ちは挙動を変えるので、常用の計測には使わない。

## 出力の読み方

出力例:

```text
[graft-perf] frame=26.900ms draw=0.090ms indices=17.850ms render_layer=1.780ms scene=6.200ms
```

- すべて「直近 N フレームの平均（ms/frame）」。
- `(...x)` が付くラベルは「1 フレーム内で複数回呼ばれている」ことを示す。
  - 例: `render_layer=202.1ms (500.0x)` は、1 フレームに 500 回呼ばれていて、合計 202.1ms/frame かかっている。
- `draw` は `scene` の一部（subset）なので、`draw + scene` のように足し算しない。

## 計測時の注意

- 最初の 1 回（最初の出力ウィンドウ）は import/初期化/キャッシュ等が混ざるので、安定した 2 回目以降を重視する。
- `GRAFT_PERF_GPU_FINISH=1` は「GPU 待ちが発生しているか」を見るための粗い診断。
  - `gpu_finish` が大きい ≒ GPU/ドライバ待ちの可能性が上がる。
  - ただし `finish()` 自体が待ちを作るので、値は“目安”として扱う。

## 計測用スケッチ（負荷の作り分け）

`sketch/perf_sketch.py` は `GRAFT_SKETCH_CASE` で負荷タイプを切り替えられる。

- `GRAFT_SKETCH_CASE=polyhedron`（既定）: ほどよい総合例
- `GRAFT_SKETCH_CASE=cpu_draw` : `draw(t)` を意図的に重くして mp-draw の検証をする
- `GRAFT_SKETCH_CASE=many_vertices` : 1 本の巨大ポリラインで indices/realize/転送の支配項を観測する
- `GRAFT_SKETCH_CASE=many_layers` : 多レイヤーで upload/draw 呼び出し回数を重くする

`GRAFT_SKETCH_N_WORKER` を 2 以上にすると `run(..., n_worker=...)` を通じて mp-draw を有効化できる。

- 注意（spawn 前提）:
  - `draw` はモジュールトップレベル定義（picklable）である必要がある。
  - スケッチ側は `if __name__ == "__main__":` ガード必須。
  - ワーカーは CPU 計算のみ（pyglet/pyimgui/OpenGL は触らない）。
- 重要: mp-draw 有効時、`draw` 区間はメインでは計測されない（worker 側で実行されるため）。
  - `scene` は「realize_scene のうち draw 以外」（主に realize 側）になる。

### cpu_draw（draw 支配）

```bash
GRAFT_SKETCH_CASE=cpu_draw GRAFT_SKETCH_CPU_ITERS=500000 GRAFT_SKETCH_PARAMETER_GUI=0 \
  GRAFT_PERF=1 GRAFT_PERF_EVERY=60 python sketch/perf_sketch.py
```

代表値（例）:

```text
[graft-perf] frame=50.1ms draw=48.8ms indices=0.03ms render_layer=1.05ms scene=48.8ms
```

読み:

- `draw` が支配的 → mp-draw（Phase 2）が効くタイプ。

mp-draw を有効にして再計測する例:

```bash
GRAFT_SKETCH_CASE=cpu_draw GRAFT_SKETCH_CPU_ITERS=500000 GRAFT_SKETCH_N_WORKER=4 \
  GRAFT_SKETCH_PARAMETER_GUI=0 GRAFT_PERF=1 GRAFT_PERF_EVERY=60 python sketch/perf_sketch.py
```

### many_vertices（巨大ポリライン）

```bash
GRAFT_SKETCH_CASE=many_vertices GRAFT_SKETCH_SEGMENTS=200000 GRAFT_SKETCH_PARAMETER_GUI=0 \
  GRAFT_PERF=1 GRAFT_PERF_EVERY=60 GRAFT_PERF_GPU_FINISH=1 python sketch/perf_sketch.py
```

代表値（例）:

```text
[graft-perf] frame=9.0ms draw=0.08ms gpu_finish=0.72ms indices=0.003ms render_layer=1.6ms scene=6.5ms
```

読み:

- Phase 1A（indices キャッシュ）後は `indices` がほぼ消え、支配項が `scene`（realize）へ移動する。
- もし `indices` が大きいままなら、indices キャッシュが外れている（または無効化されている）可能性が高い。

### many_layers（render_layer 支配）

```bash
GRAFT_SKETCH_CASE=many_layers GRAFT_SKETCH_LAYERS=500 GRAFT_SKETCH_PARAMETER_GUI=0 \
  GRAFT_PERF=1 GRAFT_PERF_EVERY=60 python sketch/perf_sketch.py
```

代表値（例）:

```text
[graft-perf] frame=231ms draw=11ms indices=1.43ms (500.0x) render_layer=202ms (500.0x) scene=22ms
```

読み:

- `render_layer` が支配的で呼び出し回数も多い → upload/VAO/描画呼び出し回数の削減（Phase 1B）が最優先。
