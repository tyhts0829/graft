# Parameter GUI 監視（CPU/Mem/FPS/頂点/ライン）実装改善計画 / 2025-12-24

## 背景（現状）

- interactive には `PerfCollector`（`src/grafix/interactive/runtime/perf.py`）があるが、
  - stdout への周期出力が主目的（GUI 表示ではない）
  - CPU 使用率 / メモリ使用量 / FPS / 頂点数 / ライン数の集約表示がない
- `ParameterGUI` は ParamStore 編集専用で、実行時状態（描画負荷）を把握しづらい。

## 目的（この改善で得たい状態）

- Parameter GUI の上部に「軽量な監視バー」を常時表示し、以下をリアルタイムに把握できる。
  - CPU 使用率（プロセス）
  - memory 使用量（プロセス）
  - 描画 FPS（実測、平滑化）
  - 描画中の頂点数（定義を明確化）
  - 描画中のライン数（定義を明確化）
- 監視のために描画が重くならない（追加オーバーヘッドが実用上無視できる）。
- 設計の置き場所が明確（runtime 側で計測、parameter_gui 側で表示）。

## 非目的（今回やらない）

- GPU 使用率/VRAM/温度などの取得（OS/ドライバ依存が強い）
- 外部プロファイラ連携（speedscope 等）
- ログ収集・永続化（まずは画面表示のみ）

## メトリクス定義（仕様として固定したい）

### 1) FPS

- `MultiWindowLoop` の 1 周回を 1 フレームとして FPS を計測する（実効 FPS）。
- 表示は 1 秒窓の移動平均 or EMA（例: 0.2〜0.3）で平滑化する。

### 2) CPU 使用率（プロセス）

- `psutil` を使い、「このプロセス + 子プロセス（mp-draw worker）合算」の CPU 使用率を表示する。
  - `cpu% = 100 * Δ(sum(user+system)) / Δwall_time`
  - 100% を超え得る（複数コア使用として解釈する）

### 3) メモリ使用量（プロセス）

- `psutil` を使い、「このプロセス + 子プロセス（mp-draw worker）合算」の **現在 RSS** を表示する。
  - `rss_mb = sum(rss_bytes) / (1024**2)`

### 4) 描画中の頂点数 / ライン数

ここは曖昧になりやすいので、実装前に用語を固定する。

- **頂点数**: 実際に描画対象になった頂点数（GL に渡す座標数）。
  - `RealizedGeometry.coords` のうち、`offsets` で長さ `>=2` の polyline に含まれる頂点の合計。
- **ライン数**: 実際に描画対象になった polyline 本数（長さ `>=2`）。
- **セグメント数（線分数）**は `segments = vertices - lines` で導けるため、今回は表示しない（内部値としては必要なら持てる）。

## 提案アーキテクチャ

### 全体方針

- 計測は runtime 側で集約し、GUI へは **スナップショット（読み取り専用）** を渡す。
- 追加スレッドは使わない（ループ 1 本の前提で単純にする）。

### 1) runtime 側: `RuntimeMonitor`（新規）

- 追加ファイル案: `src/grafix/interactive/runtime/monitor.py`
- 役割:
  - フレーム tick（時刻）を受け取り FPS/CPU% を更新する
  - 一定周期（例: 0.5s）で cpu/memory をサンプリングする（psutil）
  - draw 側から「頂点/ライン/レイヤ数」を受け取り保持する
  - `snapshot()` で GUI 表示用の dataclass（数値のみ）を返す

データモデル案（例）:

- `MonitorSnapshot`
  - `fps: float`
  - `cpu_percent: float`
  - `rss_mb: float`
  - `vertices: int`
  - `lines: int`
  - `layers: int`

### 2) 頂点/ライン数の集計の置き場所

最小オーバーヘッドにするため、`offsets` の走査回数を増やさない。

- `DrawWindowSystem` は各 layer で `build_line_indices(offsets)` を必ず呼ぶ。
- `build_line_indices` は内部の numba 関数で `offsets` を走査している。

提案（どちらか）:

1. `build_line_indices_and_stats(offsets)` を新設し、`(indices, stats)` を返す（推奨）；こちらで
   - stats: `draw_vertices` / `draw_lines` 等（`draw_segments` は `vertices-lines` で導出可）
   - LRU キャッシュも `(indices, stats)` ごと効く
2. `build_line_indices` を破壊的に変更して stats も返す（リポは未配布なので許容はできる）

### 3) 配線（run → system へ monitor を共有）

- `src/grafix/api/run.py` で `RuntimeMonitor` を 1 つ生成し、以下へ渡す。
  - `DrawWindowSystem(..., monitor=monitor)`
  - `ParameterGUIWindowSystem(..., monitor=monitor)`
- `DrawWindowSystem.draw_frame()` の最後（もしくは realized_layers 確定後）に monitor を更新する。
  - `monitor.tick_frame(draw_stats=...)`
- `ParameterGUI` は `monitor.snapshot()` を読んで表示するだけ（更新責務を持たない）。

## Parameter GUI への表示（UI 方針）

- 追加ファイル案: `src/grafix/interactive/parameter_gui/monitor_bar.py`
  - `render_monitor_bar(snapshot: MonitorSnapshot) -> None` のような純粋ビュー
- `src/grafix/interactive/parameter_gui/gui.py` は
  - 上部に monitor bar を描画
  - その下に既存の `render_store_parameter_table(...)` を置く
- UI はテキストのみ（上部に固定 1 行）でよい。
  - 例: `FPS 58.4 | CPU 123% | RSS 512MB | Vtx 1,234,567 | Lines 12,345`
- 将来拡張（今回やらないが、設計余地として残す）:
  - warn 閾値で色を変える（FPS < 30 等）

## 実装チェックリスト（コード変更前提）

- [x] 指標の定義を確定（lines=polyline 本数、cpu/mem=psutil で process+children 合算、表示は上部 1 行）
- [x] `psutil` を依存に追加（cpu/mem 取得）
- [x] `RuntimeMonitor` / `MonitorSnapshot` を追加（runtime）
- [x] indices 生成から stats を取得できる API を用意（`build_line_indices_and_stats` 推奨）
- [x] `run()` で monitor を生成し、Draw/GUI system に配線する
- [x] `DrawWindowSystem.draw_frame()` で stats を更新する
- [x] `ParameterGUI` に monitor bar を追加して表示する
- [x] テスト追加（少なくとも counts の境界ケース）
- [ ] 手動確認（sketch を実行して表示の更新を確認）

## テスト/検証コマンド案

- `PYTHONPATH=src pytest -q tests/interactive/parameter_gui`
- `PYTHONPATH=src pytest -q tests/interactive/runtime`
- `ruff check .`（任意）
- 手動: `python sketch/perf_sketch.py`（parameter_gui を ON にして monitor 表示確認）

## リスク / 注意点

- `psutil` は追加依存（ビルド済み wheel 前提）。ただし cpu/mem の実装は最も単純になる。
- mp-draw 有効時に子プロセスを合算するため、プロセス生成/終了タイミングで値がわずかに揺れる可能性がある。
- offsets が巨大なケースで、追加走査が入ると測定自体が負荷になり得る（stats は indices 生成と同時に取る）。

## 前提（確定）

1. ライン数 = **polyline 本数（length>=2）** とする。線分数は頂点数と相関が強く、`segments = vertices - lines` で導けるため表示しない。
2. cpu/memory は `psutil` を追加し、**プロセス + 子プロセス合算**の現在値を表示する（依存追加 OK）。
3. monitor 表示は **上部 1 行固定** とする。
