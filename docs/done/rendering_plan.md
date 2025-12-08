# rendering_plan.md

レンダリング系（Layer / Scene 正規化 / RenderPrepCache / GpuCache / ランナー）の実装計画メモ。
本メモは **実装方針とチェックリストのみ** を記述し、実際のコード変更はこの計画に対して承認をもらってから行う。

対象となる仕様は主に `spec.md` の以下の節：

- 2.3 Layer
- 5.4 描画キャッシュ（RenderPrepCache / GpuCache）
- 6. draw の契約とシーン正規化
- 7. 実行モデル（描画・並行性）
- 13. 太線レンダリング（PreparedStroke / シェーダ契約）
- 14. 座標系・厚み単位

---

## 0. 目的と前提

- 目的
  - `Geometry` / `RealizedGeometry` / `realize_cache` まで実装済みの状態から、
    プレビュー描画と将来のエクスポートを共通化できるレンダリングパイプラインを構築する。
  - GeometryId ベースのキャッシュ（`realize_cache` / `RenderPrepCache` / `GpuCache`）を活かして、
    フレームごとの再計算を最小化しつつ滑らかなリアルタイム描画を行う。
- 前提
  - `src/core/geometry.py`, `src/core/realize.py`, `src/core/realized_geometry.py` は既に存在し、
    `RealizedGeometry(coords, offsets)` と `realize_cache` / `inflight` が仕様どおり実装済み。
  - レンダリング用の OpenGL バックエンドはまだ未決定とし、本計画では「インターフェースと責務の分離」までを設計範囲とする。
  - エクスポート（静止画/G-code/動画）は、プレビュー描画と同じ Scene 表現を共有するが、
    本計画では「Scene 正規化」と「太線メッシュ生成」までを優先する。

---

## 1. モジュール構成案

レンダリング関連の責務は `src/render/` 配下にまとめる。
モジュール名は暫定であり、実装時に微調整してよい。

1. `src/render/layer.py`
   - `Layer` データクラスとスタイル解決ロジック。
2. `src/render/scene.py`（または `scene_spec.py`）
   - `user_draw` の戻り値を `list[Layer]` に正規化するヘルパ。
3. `src/render/render_prep.py`
   - `PreparedStroke` と `RenderPrepCache` の実装。
4. `src/render/gpu_cache.py`（または `gpu.py`）
   - `GpuStrokeHandle` と `GpuCache` のインターフェース層。
5. `src/render/renderer.py`
   - 1 フレーム分の Scene を描画する Renderer（メインスレッド専用）。
6. `src/render/runner.py`
   - フレームループ（スナップショット作成 / user_draw 実行 / realize / render_prep / GPU 描画）の統合。
7. `src/api/run.py`（あるいは既存 `api` への追加）
   - 公開 API としての `run` / `run_sketch` の薄いラッパ。

---

## 2. Layer / Scene 正規化

### 2-1. Layer モデル (`src/render/layer.py`)

- 仕様対応
  - `spec.md` 2.3 および 6.2 に基づき、以下のフィールドを持つ不変データクラスを定義：
    - `geometry: Geometry`
    - `color: tuple[float, float, float, float] | None`（RGBA）
    - `thickness: float | None`
    - `name: str | None`
- スタイル解決

  - ランナー側で使用するための関数を用意：
    - `resolve_layer_style(layer: Layer, defaults: RenderDefaults) -> ResolvedLayer`
    - `RenderDefaults` には `{color, thickness}` のグローバル既定値を保持。
  - `thickness is not None` の場合は `thickness > 0` を検証し、満たさない場合は `ValueError`。
  - Geometry 自体にはスタイル情報を埋め込まず、`Layer` 側のみが色・線幅を持つことを明示。

- チェックリスト
  - [ ] `Layer` データクラスの定義。
  - [ ] `RenderDefaults` / `ResolvedLayer` の定義（内部用でも可）。
  - [ ] `resolve_layer_style` の仕様（既定値の適用順 / エラー条件）を docstring で固定。
  - [ ] 単体テスト：`thickness <= 0` の拒否 / None → 既定値適用の確認。

### 2-2. Scene 正規化 (`src/render/scene.py`)

- 仕様対応

  - `spec.md` 6.1–6.2 に基づき、`user_draw` の戻り値から `list[Layer]` を生成する関数を定義：
    - `normalize_scene(output: Geometry | Layer | Sequence[Geometry | Layer]) -> list[Layer]`
  - 処理規則：
    - `Geometry` → `Layer(geometry=..., color=None, thickness=None, name=None)` で包む。
    - `Layer` → そのまま採用（コピー不要）。
    - `Sequence` → 再帰的にフラット化しつつ上記規則で `list[Layer]` に変換。
  - `cc` や Parameter GUI とは独立し、純粋に戻り値の構造だけを見る。

- チェックリスト
  - [ ] `normalize_scene` の定義と型ヒント。
  - [ ] ネストしたリスト/タプルにも対応するフラット化ロジック。
  - [ ] 戻り値が空の場合（何も描かないフレーム）の扱いを仕様として許容。
  - [ ] 単体テスト：`Geometry` / `Layer` / ネストした `Sequence` に対する正規化テスト。

---

## 3. RenderPrepCache と PreparedStroke

### 3-1. PreparedStroke モデル (`src/render/render_prep.py`)

- 仕様対応
  - `spec.md` 13.2–13.3 に基づき、以下のフィールドを持つ `PreparedStroke` を定義：
    - `attrs: np.ndarray`（`float32`, shape `(V, K)`）
      - カラムには少なくとも `curr`, `prev`, `next`, `side` を含む。
      - 実装上は列順と意味をコメント/定数で固定する（例：`ATTR_IDX_CURR = slice(0, 3)` など）。
    - `indices: np.ndarray`（`uint32` または `int32`, shape `(I,)`）
    - `polyline_ranges: np.ndarray | None`（`offsets` に対応する区間情報。デバッグ用）
- 生成規則

  - 入力：`RealizedGeometry(coords, offsets)`。
  - 各ポリライン（`coords[offsets[i]:offsets[i+1]]`）を独立に処理し、前後ポリラインとの接続は行わない。
  - 各点 `i` ごとに 2 頂点（`side = ±1`）を生成し、`V = 2 * N`。
  - 各セグメント（`i → i+1`）ごとに 2 三角形（計 6 インデックス）を生成。
  - ジョイン/キャップは最小仕様として miter + miter_limit + butt cap を採用し、
    パラメータは当面 Renderer 側の定数として持つ。

- チェックリスト
  - [ ] `PreparedStroke` データクラスの定義。
  - [ ] `prepare_stroke(realized: RealizedGeometry) -> PreparedStroke` の定義。
  - [ ] `RealizedGeometry.offsets` に従ったポリライン分割の実装。
  - [ ] `prev` / `next` の端点処理（端は自分自身を複製するか、片側方向で計算するかのルールを決める）。
  - [ ] `attrs` / `indices` の `writeable=False` 設定。
  - [ ] 単体テスト：単一セグメント / 複数ポリライン / 退化ケース（1 点のみ）などの形状検証。

### 3-2. RenderPrepCache (`src/render/render_prep.py`)

- 仕様対応

  - `spec.md` 5.4.1 に基づき、`GeometryId -> PreparedStroke` のキャッシュを実装：
    - `class RenderPrepCache:`
      - `get(geom_id: GeometryId) -> PreparedStroke | None`
      - `set(geom_id: GeometryId, value: PreparedStroke) -> None`
  - 容量上限は当面設けず、将来の最適化で推定バイト数ベースの上限管理を追加できるようにしておく。
  - スレッドセーフにするため `threading.Lock` で内部辞書を守る（`realize_cache` と同等レベル）。

- チェックリスト
  - [ ] `RenderPrepCache` クラス定義。
  - [ ] グローバルインスタンス `render_prep_cache` の配置場所を決定（`render_prep.py` 内で定義し、他から import）。
  - [ ] 容量管理フック（将来的なバイト数計算用のメソッド）をコメントで予約。
  - [ ] 単体テスト：キャッシュヒット/ミスの挙動確認。

---

## 4. GPU キャッシュと Renderer

### 4-1. GpuStrokeHandle / GpuCache (`src/render/gpu_cache.py`)

- 仕様対応

  - `spec.md` 5.4.2 および 13.4–13.6 に基づき、GPU リソースキャッシュのインターフェースを定義：
    - `GpuStrokeHandle`（データクラス）
      - `vertex_buffer`, `index_buffer`, `vertex_count` など、バックエンド固有ハンドルを格納。
    - `GpuCache`
      - `get(geom_id: GeometryId) -> GpuStrokeHandle | None`
      - `set(geom_id: GeometryId, handle: GpuStrokeHandle) -> None`
      - `evict(geom_id: GeometryId) -> None`
  - 制約：
    - GPU リソースの生成/破棄/更新はメインスレッドでのみ行う。
    - 実装当初は「抽象インターフェース」として定義し、具体的な GL バインディングは後続タスクで決める。

- チェックリスト
  - [ ] `GpuStrokeHandle` データクラスの定義（バックエンド非依存の最低限フィールド）。
  - [ ] `GpuCache` の実装とグローバルインスタンス配置場所の決定。
  - [ ] 将来の容量上限（推定 GPU バイト数）の導入を想定した設計メモを残す。
  - [ ] メインスレッド以外から触らないことを docstring で明記。

### 4-2. Renderer (`src/render/renderer.py`)

- 役割
  - 1 フレーム分の `list[ResolvedLayer]` と各種キャッシュを受け取り、実際に描画コマンドを発行する。
  - `spec.md` 7.2 のステップ 5 と 13.4–13.6 のシェーダ契約を実装側から見た抽象化。
- インターフェース案
  - `class Renderer:`
    - `draw_scene(layers: Sequence[Layer], snapshot: FrameSnapshot) -> None`
    - 内部で以下の流れを行う：
      1. 各 `Layer` から `GeometryId` を取り出す。
      2. `RealizeCache` / `RenderPrepCache` / `GpuCache` を参照し、不足分を補完。
      3. 描画順は `layers` の順序に従い、後勝ちブレンド。
  - `FrameSnapshot` には `t`, `viewproj`, `viewport_size`, `render_defaults` 等を含める。
- シェーダパラメータ

  - `spec.md` 13.4–13.6 に従い、少なくとも以下をユニフォームとして渡す：
    - `u_viewproj: mat4`
    - `u_viewport_size: vec2`
    - `u_thickness: float`（ResolvedLayer から取得）
    - `u_color: vec4`
  - thickness は常にワールド単位として処理し、プレビューとエクスポートで解釈を一致させる。

- チェックリスト
  - [ ] `FrameSnapshot` データクラスの定義。
  - [ ] `Renderer` インターフェースと描画順序（Layer 並び＝描画順）の固定。
  - [ ] シェーダ契約に合わせたパラメータ受け渡し設計（実装は後続タスクでも可）。
  - [ ] アンチエイリアス（MSAA / フェザー）の導入ポイントをコメントとして明示。

---

## 5. フレームランナーと並行パイプライン

### 5-1. Runner (`src/render/runner.py`)

- 仕様対応
  - `spec.md` 7.2 の推奨パイプラインをそのままコード化するクラスを定義：
    - `class Runner:`
      - `run_frame(t: float) -> None`（あるいは内部ループ）
  - 1 フレームの処理手順：
    1. スナップショット作成：`FrameSnapshot = {t, cc_snapshot, param_snapshot, palette_snapshot, settings}`。
    2. `user_draw(t)` を実行して `SceneSpec = normalize_scene(output)` を得る（ワーカースレッドでも可）。
    3. `SceneSpec` に含まれる全 `GeometryId` を列挙し、`realize` をスレッドプールで並列実行して `realize_cache` を温める。
    4. realize 済みのものから順に `prepare_stroke` を呼び出し、`render_prep_cache` をワーカースレッド上で温める。
    5. メインスレッドは「最新に完成した SceneSpec」を採用し、`Renderer` に渡して描画（このとき不足している `GpuStrokeHandle` のみ生成）。
    6. 古い Scene/スナップショットの計算結果は破棄してよい（キャッシュは残る）。
- 並行性

  - `concurrent.futures.ThreadPoolExecutor` を利用したスレッドプール実装を想定。
  - `realize` は内部で `inflight` を持つため、Runner 側は単純に `executor.submit(realize, geom)` を列挙するだけで重複計算が抑制される。
  - `RenderPrepCache` は `GeometryId` 単位で独立しており、ワーカー側の CPU 前処理のみで完結させる。

- チェックリスト
  - [ ] `Runner` クラスとスレッドプール初期化の設計。
  - [ ] `user_draw` を受け取るインターフェース（コールバックやモジュール import 方式）を決める。
  - [ ] `cc` / Parameter GUI 用のスナップショットとの整合性を最低限の docstring で固定。
  - [ ] 異常系（`user_draw` や `realize` の例外）の扱い方針（ログ出力 / 継続 or 中断）を整理。

---

## 6. 公開 API への統合

### 6-1. `api.run` / `api.run_sketch`

- 仕様対応

  - `spec.md` 1.1 の `run`, `run_sketch` を最低限の形で実装するための計画：
    - `run(user_draw: Callable[[float], Geometry | Layer | Sequence[...]], *, settings: RunSettings | None = None) -> None`
      - ウィンドウと GL コンテキストを初期化し、内部で `Runner` を生成してフレームループを回す。
    - `run_sketch(path: str, *, settings: RunSettings | None = None) -> None`
      - 指定ファイルから `user_draw` 相当の関数を import して `run` を呼び出す。
  - `RunSettings` には FPS / ウィンドウサイズ / 背景色などを格納。

- チェックリスト
  - [ ] `RunSettings` と `run` / `run_sketch` のシグネチャ設計。
  - [ ] `Runner` / `Renderer` との依存関係を循環しないように整理。
  - [ ] 最小限の「Hello circle」的サンプルスケッチをどこに置くか決定（`examples/` など）。

---

## 7. テスト戦略とオープンな論点

### 7-1. テスト戦略

- ユニットテスト
  - Layer / Scene 正規化：`tests/test_scene.py`（仮）で `normalize_scene` と `resolve_layer_style` をカバー。
  - RenderPrep：`tests/test_render_prep.py` で `PreparedStroke` の頂点数・インデックス数・ポリライン分割の検証。
  - キャッシュ：`tests/test_render_cache.py` で `RenderPrepCache` / `GpuCache` の基本挙動を確認。
- 統合テスト（将来）
  - 簡単な `user_draw` を通して `Runner` が `realize` → `prepare_stroke` まで到達することを検証するテストを追加。

### 7-2. オープンな論点・要相談事項

- [x] OpenGL バックエンドとして何を採用するか  
  → 初期実装は `moderngl` を採用する（`pyglet` / 生 `PyOpenGL` は現時点では検討対象外）。
- [ ] `Renderer` と GUI（Parameter GUI / MIDI cc 表示）との結合度をどこまで高めるか  
  → 現時点では具体設計は行わず、Renderer/Runner から GUI フレームワークへ直接依存しない（ウィンドウ・イベントループは別モジュール側に寄せる）方針だけを維持する。
- [x] エクスポート（静止画/G-code/動画）の実装をどの層に置くか  
  → Renderer の派生ではなく **別モジュール** として実装し、Scene 正規化と `PreparedStroke` までは GPU 非依存の共通レイヤとして再利用する。
- [ ] MSAA や解像度に依存する設定を `RunSettings` にどこまで持たせるか  
  → 初期実装では最低限の固定設定でもよく、詳細な設定項目は実装フェーズで再検討する。
- [x] マルチモニタや HiDPI 対応（`u_viewport_size` の解釈）を最初から考慮するか  
  → 初期実装では **考慮しない**。必要になったタイミングで `RunSettings` 拡張などで対応を検討する。

---

この計画に問題なければ、このチェックリストに沿って
`src/render/layer.py` から順にレンダリング周りの実装を進めていきます。
実装中に新たな論点が出てきた場合は、この md に項目を追記していきます。
