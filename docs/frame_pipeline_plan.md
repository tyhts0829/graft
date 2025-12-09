# frame_pipeline_plan.md

どこで: `docs/frame_pipeline_plan.md`。対象コードは `src/api/run.py`, `src/render/frame_pipeline.py`（新規）, `src/render/draw_renderer.py`, `src/render/scene.py`, `src/render/layer.py`, `src/render/index_buffer.py` など。
何を: `run` 関数を極小化し、フレーム処理（Scene 生成→正規化→スタイル解決→realize→インデックス生成→描画）を単一パイプライン関数に集約する設計とタスク分解をまとめる。
なぜ: 依存方向を一方向に保ちつつテストしやすくし、将来のキャッシュ/描画拡張を run の責務を増やさずに行えるようにするため。

## 0. ゴール
- `run` を「ウィンドウ生成・タイマー開始・シンプルなフレーム呼び出し」に限定する。
- フレーム処理を `render/frame_pipeline.py` の 1 関数に集約し、依存を `api/run.py → frame_pipeline → render/* → core/*` の一方向に固定する。
- Renderer は「1 レイヤー描画」API を提供し、pipeline からは GL 依存を隠蔽する。
- pipeline をスタブ化して pytest 可能にし、GUI/GL なしで CPU ロジックを検証できるようにする。
- parameter_spec のコンテキスト固定（`param_snapshot` / `cc_snapshot` / `discovery_sink`）を run 側に残し、pipeline 呼び出し前にセットするフックを明示する。

## 1. 依存方向のルール
- `api/run.py` は `render/frame_pipeline.py` を呼ぶだけ。逆依存は禁止。
- `frame_pipeline.py` は `render/scene.py`, `render/layer.py`, `core/realize.py`, `render/index_buffer.py` と `DrawRenderer` への依存のみ。`app` 層は触らない。
- `DrawRenderer` は GL/pyglet 依存を内包し、`render_layer(realized, indices, *, color, thickness)` のような最小 API を提供。
- `parameter_spec` で求められるスナップショット設定 (`param_snapshot` 等) は `run` 内のフレーム開始ステップで実施し、その後に `render_scene` を呼び出す。

## 2. タスク分解
- [x] `src/render/frame_pipeline.py` 新設。
  - `render_scene(draw, t, defaults, renderer)` を定義し、以下を直列に実行:
    1. `scene = draw(t)`
    2. `layers = normalize_scene(scene)`
    3. 各 layer で `resolved = resolve_layer_style(layer, defaults)`
    4. `realized = realize(resolved.layer.geometry)`
    5. `indices = build_line_indices(realized.offsets)`
    6. `renderer.render_layer(realized, indices, color=resolved.color, thickness=resolved.thickness)`
  - 返り値は None。未使用の値は返さず、責務は副作用に限定。
  - 呼び出し前提: run 側で `param_snapshot` / `cc_snapshot` / `discovery_sink` のセットアップ済みであること（parameter_spec 8–9 の要件を満たす）。
- [x] `DrawRenderer` に `render_layer` 薄いラッパを追加し、既存 `render` を内部呼び出しにまとめるか、`render` を `render_layer` にリネームして単一 API にする。
- [x] `src/api/run.py` を簡素化。
  - `render_frame` 内部ロジックを廃し、`render_scene(draw, t, defaults, renderer)` を呼ぶだけにする。
  - `defaults = LayerStyleDefaults(...)` 生成と renderer/window の初期化のみを保持。
- [ ] テスト追加。
  - `tests/render/test_frame_pipeline.py`（新規）で、モック Renderer を使い `render_scene` が Layer 単位で呼ばれること、スタイル解決や正規化が期待どおり動くことを確認。
  - pyglet/GL 依存を避けるため Renderer はプロトコル/スタブで注入。
- [ ] ドキュメント更新。
  - `docs/layer_plan.md` または README の「ランナー構造」節に pipeline 抽出を追記。
  - 依存方向の図を簡潔に示す（テキストで可）。

## 3. リスク/留意点
- `DrawRenderer` の API 変更が他呼び出しに影響しないか確認（現状 run だけが利用）。
- 将来 RenderPrepCache/GpuCache を導入するとき、pipeline 内にフックを設ける必要がある。現段階では TODO コメントで拡張ポイントを残す。
- `normalize_scene` の例外（TypeError）をどこで握りつぶすか検討。現状は run まで伝播でよいが、UX に応じて HUD/ログに出す処理を後続タスクで検討。

## 4. 完了チェックリスト
- [ ] frame_pipeline 新設と render_scene 実装。
- [ ] DrawRenderer API 整理（render_layer 化）。
- [ ] run のシンプル化（frame_pipeline 呼び出しのみ）。
- [ ] テスト追加（frame_pipeline スタブ版）。
- [ ] ドキュメント更新。
