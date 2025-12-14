# どこで: `docs/memo/src_code_review_2025-12-14.md`。
# 何を: `src/` 配下のモジュール群のコードレビュー結果をまとめる。
# なぜ: 改善の優先順位と、次に触るべき境界（責務/依存）を明確にするため。

# `src/` コードレビュー（2025-12-14）

## 対象 / スナップショット

- 対象ディレクトリ: `src/api`, `src/app`, `src/core`, `src/effects`, `src/parameters`, `src/primitives`, `src/render`
- 対象ファイル: 57 Python modules（`.py`）
- 主要依存（読み取れる範囲）:
  - 数値: `numpy`
  - 実行/ウィンドウ: `pyglet`
  - 描画: `moderngl`（OpenGL 4.1）
  - GUI: `imgui`（pyimgui, Parameter GUI 有効時のみ）

## 全体像（ざっくり依存/流れ）

### 1) ユーザー API（`src/api/*`）

- `G.*`（primitive）/ `E.*`（effect）で **Geometry DAG（レシピ）** を組み立てる
- `L(...)` で Geometry にスタイルを付け **Layer** にする（`Layer.site_id` は呼び出し箇所由来）
- `run(draw)` で runtime 起動（描画ウィンドウ + optional GUI）

### 2) 1 フレーム処理（`src/app/runtime/*` + `src/render/*`）

`DrawWindowSystem.draw_frame()` 内で:

- `parameter_context(store)` で **フレーム中の ParamStore snapshot を固定**（決定性の確保）
- `render_scene(draw, t, defaults, renderer)` で
  - `normalize_scene()` → `list[Layer]` へ正規化
  - `resolve_layer_style()` + GUI の layer-style 上書きを反映
  - `realize(Geometry)` で DAG を評価し `RealizedGeometry` を得る
  - `build_line_indices()` → `DrawRenderer.render_layer()` で描画

### 3) パラメータ解決（`src/parameters/*`）

- Geometry 生成時点で `resolve_params()` が base/GUI/CC を統合し **effective 値を確定**
- 同時に `FrameParamsBuffer` に観測結果を記録
- フレーム終了時に `ParamStore.store_frame_params()` が state/meta/ordinal を更新
- Parameter GUI は snapshot→行モデル→UI→差分適用の流れで store を更新（次フレームから反映）

## 良い点

- **責務分離が素直**:
  - `src/api` は「レシピ構築」
  - `src/core` は「DAG/評価」
  - `src/parameters` は「決定性のある値解決 + GUI 用モデル」
  - `src/render` は「描画パイプライン」
  - `src/app` は「ウィンドウ/ループ統合」
  という分け方になっていて、読みやすい。
- **フレーム決定性の設計が明確**:
  - `parameter_context()` で snapshot を固定し、同フレーム中に GUI が動いても解決がブレない。
- **Geometry の不変/内容署名（`GeometryId`）が明快**:
  - `Geometry.create()` → `normalize_args()` → `compute_geometry_id()` で「同じ入力→同じ id」を強制できている。
  - これが `RealizeCache` のキーとして綺麗に繋がっている。
- **Parameter GUI 周りが純粋関数寄り**:
  - `src/parameters/view.py`、`src/app/parameter_gui/*` の grouping/labeling/rules は切り出しが良い。
- **依存の重いものを遅延 import している**:
  - `parameter_gui=True` の時だけ GUI サブシステムを import/初期化するのは扱いやすい。

## 気になった点 / 改善余地（優先度順）

### P0（早めに直す価値が高い）

1) **`PrimitiveRegistry.register()` / `EffectRegistry.register()` の decorator 経路で `defaults` が捨てられる**
   - `src/core/primitive_registry.py` / `src/core/effect_registry.py`
   - `register(name, func=None, ..., defaults=...)` で `func is None` の場合、内部 decorator が `defaults` を引き継いでいない。
   - 現状の主要経路（`@primitive(meta=...)` / `@effect(meta=...)`）では `defaults` は別途算出して渡しているので直ちに壊れてはいないが、API としては罠になり得る。

2) **“公開 API” の型/Docstring が揺れている箇所がある**
   - 例: `src/api/__init__.py:run()` は `*args, **kwargs` で型が落ちる（遅延 import の意図は良い）。
   - `src/parameters/context.py` の `current_*` 系は `__init__.py` で再エクスポートされる一方、docstring が最小限（または無し）。
   - 「公開 API は NumPy docstring + 型ヒント」の方針に合わせるなら、ここは一貫させたい。

3) **import 経路が二重化しやすい構造**
   - `src` を “実パッケージ名” として絶対 import している一方で、`main.py` などが `sys.path.append("src")` で top-level に `api` を生やす運用になっている。
   - 同一ソースが `api.*` と `src.api.*` の両方で import 可能になり、将来的に「同じつもりの import が別モジュール扱い」になるリスクがある（特に singleton/registry 系）。
   - どちらか 1 本に寄せるのが綺麗。

### P1（品質/可読性に効く）

1) **“線幅” の単位系がコード/コメントで一貫していない**
   - `src/api/run.py` の docstring では `line_thickness` を「ワールド単位」と説明しているが、`src/render/shader.py` の実装上は clip space の値として扱われているように見える（投影後の座標差分に対して thickness をそのまま使っている）。
   - どちらに寄せるか（世界座標→clip 変換を行うか / docstring を変えるか）を揃えた方が混乱が減る。

2) **RGB 値の coercion/clamp が複数箇所に重複**
   - `src/app/runtime/draw_window_system.py` と `src/render/frame_pipeline.py`、さらに GUI 側にも類似ロジックがある（`widgets._as_rgb255` など）。
   - “UI 値は (0..255, int)” という前提を 1 箇所に集約すると、仕様変更に強くなる。

3) **ファイルヘッダ形式の揺れ**
   - ほとんどのファイルが「どこで/何を/なぜ」を持つ一方で、`src/render/shader.py` は「どこで」が無い/形式が違う、`src/render/line_mesh.py` は `engine.render` 名義の記述になっている、など軽微な不一致がある。

4) **`ParamMeta.kind` の表記ゆれ（コメントと実装）**
   - `src/parameters/meta.py` を含め、文字列型の kind 表記を `"str"` に統一した（`"string"` を廃止）。
   - GUI 側のディスパッチも `"str"` に合わせたため、語彙の揺れが無くなった。

### P2（将来の整理ポイント）

1) **`src/effects/fill.py` が大きい**
   - 仕様の塊としては妥当だが、読みやすさの観点では「2D 幾何ユーティリティ」「even-odd グルーピング」「スキャンライン生成」などをファイル分割できそう。
   - ただし今の単一ファイルでも、関数分割自体はされていて破綻はしていない。

2) **`RealizeCache` の無制限成長**
   - 実装はシンプルで良い反面、長時間実行・大量ノード生成ではメモリに上限が無い。
   - ここは “要件が出たら” LRU/世代管理などを検討、で十分そう。

## モジュール別メモ（短評）

### `src/core/*`

- `geometry.py`: 正規化/署名生成が明確。numpy scalar 等の受け入れ可否は仕様として明文化すると良い。
- `realize.py`: inflight + cache の構造が読みやすい。将来的にキャッシュ戦略を差し替えやすい形。

### `src/parameters/*`

- `context.py` / `resolver.py`: 「フレーム決定性 + 観測記録 + store マージ」の筋が通っている。
- `store.py`: ordinal/label/chain 情報まで含めた永続化がまとまっている（to/from json も素直）。
- `view.py`: 入力正規化の責務が明確。UI 側（imgui）と分離できていて良い。

### `src/render/*`

- `scene.py` / `frame_pipeline.py`: 正規化→style 解決→realize→描画、のパイプが綺麗。
- `shader.py`: 最小で良いが、ヘッダ/型/説明の統一は余地あり。線幅単位の整合もここが中心。
- `line_mesh.py`: 機能は明確。docstring/header の表記だけ整えると repo 全体の統一感が増す。

### `src/app/*` + `src/app/parameter_gui/*`

- `window_loop.py`: “flip を 1 箇所に集約” が徹底されていて良い。
- `parameter_gui/*`: grouping/labeling/rules が分離され、テストしやすい形。
- `gui.py`: macOS 固有フォントパス（`/System/Library/Fonts/SFNS.ttf`）に依存しているため、将来 OS を跨ぐならフォールバック方針が要りそう。

## テスト観点（`src` に直接効く範囲）

- `tests/` に parameter GUI の labeling/grouping/rules、`fill/rotate/scale`、`scene/layer/realize_cache` などのテストがあり、コア部分は押さえられている印象。
- 一方で “描画（moderngl/pyglet）” は E2E に寄り、単体テストには載りにくい。現状の切り分け（pure parts を unit test）が妥当。

## 次にやるなら（提案）

- まずは P0 の 1)（registry の decorator 経路）と 2)（公開 API の型/doc）を揃えるのがコスパ良い。
- import 経路（P0-3）は方針が決まると全体がスッキリするので、整理の “意思決定” を先にやるのが良さそう。
