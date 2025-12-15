<!--
どこで: `architecture.md`。
何を: `src/` 配下の実装を基に、Graft のアーキテクチャ（責務境界・依存方向・実行フロー）を整理した設計メモ。
なぜ: 機能追加やリファクタのときに「どこを触るべきか」「どこに依存させないか」を迷わないため。
-->

# Graft アーキテクチャ

## 1. 目的と中心アイデア

Graft は「線（ポリライン列）を **生成** し、effect を **チェーン** して **変形** し、リアルタイムに **プレビュー** する」ための小さなツールキット。

中心アイデアは次の 3 つ。

1) **Geometry は“配列そのもの”ではなくレシピ（DAG ノード）**  
`src/core/geometry.py` の `Geometry` は `(op, inputs, args)` を持つ不変ノードで、`id` は内容署名（content signature）。  
`draw(t)` は配列ではなく Geometry レシピを返す。

2) **実体配列は realize で遅延生成し、GeometryId でキャッシュする**  
`src/core/realize.py` の `realize(geometry)` が `Geometry -> RealizedGeometry` を評価する。  
`RealizedGeometry` は `coords(float32, Nx3)` と `offsets(int32, M+1)` を持ち、配列は writeable=False で固定される（`src/core/realized_geometry.py`）。

3) **描画スタイル（色・線幅）は Geometry から分離し Layer に載せる**  
`src/render/layer.py` の `Layer` が `Geometry + (color/thickness)` を束ねる。  
同じ Geometry を色違いで描いても Geometry キャッシュを共有できる。

加えて、Parameter GUI は「実行中に発見した引数」を ParamStore に蓄積し、GUI/CC から値を上書きして **次フレーム以降の Geometry 生成** に反映する（配列を後から書き換えない）。

## 2. パッケージ構造（責務の分割）

`src/` 配下は概ね次の責務で分割されている。

- `src/api/`（公開 API / ファサード）
  - `G`（primitive 生成）、`E`（effect チェーン）、`L`（Layer 化）、`run`（プレビュー起動）
  - 内部の core/render/app を直接触らせないための入口
- `src/core/`（ドメインコア）
  - `Geometry`（レシピ DAG）と署名生成、`RealizedGeometry`（配列表現）
  - primitive/effect のレジストリ、`realize()`（評価 + キャッシュ + inflight 排除）
- `src/primitives/`（組み込み primitive 実装）
  - `@primitive` デコレータでレジストリ登録される “実体生成関数” 群
- `src/effects/`（組み込み effect 実装）
  - `@effect` デコレータでレジストリ登録される “実体変換関数” 群
- `src/parameters/`（パラメータ解決・ストア）
  - `parameter_context`（フレーム単位の snapshot 固定）
  - `resolve_params`（base/GUI/CC の統合 + 量子化 + 観測レコード化）
  - `ParamStore`（状態・メタ・ラベル・表示順序の永続）
- `src/render/`（シーン正規化・描画パイプライン）
  - `normalize_scene`（`draw()` 戻り値を `list[Layer]` に正規化）
  - `render_scene`（Layer style 解決 → realize → index 生成 → renderer へ）
  - `DrawRenderer`（ModernGL での実描画）
- `src/app/`（ランタイム / ウィンドウ / GUI）
  - `runtime/`：複数ウィンドウを 1 ループで回す・各サブシステムを分離
  - `parameter_gui/`：pyimgui + pyglet で `ParamStore` を編集する GUI

## 3. 依存方向（レイヤ）と “呼び出し” の流れ

依存は「外側 → 内側」を基本にし、逆流はレジストリ登録（副作用 import）に限定する。

```
user sketch (main.py の draw(t))
  |
  v
src/api            : G/E/L/run（書き味の層）
  |  Geometry.create() を呼ぶ / resolve_params() を呼ぶ
  v
src/core           : Geometry / realize / registries（ドメイン核）
  ^\
  | \__ src/primitives, src/effects : @primitive/@effect で登録（実装の“プラグイン”）
  |
  +--> src/parameters : ParamStore / parameter_context / resolve_params（入力解決）
  |
  v
src/render          : Scene 正規化 / Layer style / GPU への橋渡し
  |
  v
src/app/runtime     : pyglet ウィンドウ群のループとサブシステム
         |
         +--> src/app/parameter_gui : ParamStore 編集 UI（pyimgui）
```

重要な「呼び出し順」は次。

1. `src/api/run.py:run()` が `ParamStore` とウィンドウサブシステムを作る
2. `src/app/runtime/window_loop.py:MultiWindowLoop.run()` がフレームループを回す
3. 毎フレーム `DrawWindowSystem.draw_frame()` が
   - Style（背景色/線幅/線色）を `ParamStore` から解決し
   - `parameter_context(store)` の中で `render_scene(draw, t, defaults, renderer)` を呼ぶ
4. `render_scene()` が
   - `normalize_scene(draw(t))` で Layer 列にし
   - Layer ごとに Layer style（line_thickness/line_color）を GUI で上書きし
   - `realize(geometry)` で配列を得て
   - インデックスを作り renderer に描画依頼する

GUI ウィンドウは同じループで `ParameterGUIWindowSystem.draw_frame()` が呼ばれ、`ParamStore` を更新する。
ただし draw 側は `parameter_context` の snapshot で “そのフレームの読み取り” が固定されるため、同一フレーム中に GUI が動いても `resolve_params` の結果はぶれない。

## 4. コアデータモデル

### 4.1 Geometry（レシピ DAG ノード）

- 実装: `src/core/geometry.py`
- 主な責務:
  - `params` を内容署名に入れられる形へ正規化（`normalize_args()`）
  - `(schema_version, op, inputs.id, args)` から `GeometryId` を計算（`compute_geometry_id()`）
  - `Geometry.create()` で「不変ノード」を生成する

`Geometry` は「何をするか」を表すだけで、実体配列（頂点配列）を持たない。

### 4.2 RealizedGeometry（評価結果）

- 実装: `src/core/realized_geometry.py`
- 形:
  - `coords: np.ndarray` … `(N,3)` float32
  - `offsets: np.ndarray` … `(M+1,)` int32（各ポリラインの開始 index。`offsets[0]=0`, `offsets[-1]=N`）
- 性質:
  - `__post_init__` で shape/dtype 整合性を検証し、`writeable=False` に固定する
  - 2D `(N,2)` 入力は `(N,3)`（z=0）へ補完する

### 4.3 Layer（Geometry とスタイルの分離）

- 実装: `src/render/layer.py`
- 形:
  - `Layer(geometry, site_id, color?, thickness?, name?)`
  - `LayerStyleDefaults(color, thickness)` … None 欠損を埋める既定値
- 重要点:
  - `site_id` は Layer style（GUI の line_color/line_thickness 行）のキーに使う
  - `resolve_layer_style()` は thickness が正でない場合に例外

### 4.4 Parameter 系（識別・状態・メタ）

- `src/parameters/key.py`
  - `ParameterKey(op, site_id, arg)` … GUI 行の一意キー
  - `site_id` は `filename:co_firstlineno:f_lasti` 形式（呼び出し箇所識別）
- `src/parameters/meta.py`
  - `ParamMeta(kind, ui_min, ui_max, choices)` … UI/検証の最低限メタ
- `src/parameters/state.py`
  - `ParamState(override, ui_value, cc_key)` … GUI 状態（値・上書きフラグ・CC 割当）
- `src/parameters/store.py`
  - `ParamStore` … 永続ストア（state/meta/label/ordinal/chain 情報）
  - `snapshot()` は state をコピーして返し、GUI は snapshot から rows を生成して編集する

## 5. レジストリ（primitive / effect）と拡張ポイント

### 5.1 仕組み

`src/core/primitive_registry.py` と `src/core/effect_registry.py` が op 名 → 実体関数を保持する。

- primitive 関数の契約（レジストリ側）:  
  `func(args: tuple[tuple[str, Any], ...]) -> RealizedGeometry`
- effect 関数の契約（レジストリ側）:  
  `func(inputs: Sequence[RealizedGeometry], args: tuple[tuple[str, Any], ...]) -> RealizedGeometry`

`@primitive` / `@effect` デコレータは “ユーザーが書きやすい関数” を “レジストリ契約の wrapper” に変換して登録する。

### 5.2 組み込み primitive/effect の登録

組み込みは **import 時の副作用** で登録される。

- `src/api/primitives.py` が `src.primitives.circle/polygon/polyhedron` を import して登録させる
- `src/api/effects.py` が `src.effects.scale/rotate/fill` を import して登録させる

この方式により、`from api import G, E` した時点で “組み込み op が使用可能” になる。

### 5.3 新しい primitive/effect を追加する方法（最短）

1. `src/primitives/` か `src/effects/` に新モジュールを追加
2. `@primitive(meta=...)` または `@effect(meta=...)` で関数を登録
3. 起動時に import されるようにする（どちらか）
   - `src/api/primitives.py` / `src/api/effects.py` に import を追加する（組み込みとして常時有効化）
   - あるいはスケッチ側でそのモジュールを import する（必要時だけ有効化）

## 6. realize（評価）とキャッシュ

実装: `src/core/realize.py`

`realize(Geometry)` は次の手順で評価する。

1. `realize_cache`（`GeometryId -> RealizedGeometry`）を参照し、ヒットなら返す
2. miss の場合、`_inflight` テーブルで同一 `GeometryId` の同時計算を 1 回に潰す
3. leader スレッドが `_evaluate_geometry_node()` で評価する
   - `op == "concat"` は inputs を realize して `concat_realized_geometries` で連結
   - inputs が空なら primitive（`primitive_registry[op]`）
   - それ以外は effect（`effect_registry[op]`）
4. 結果をキャッシュし、待機者へ通知して返す（例外は `RealizeError` でラップ）

現状の `RealizeCache` は容量上限を持たない（将来の最適化ポイント）。

## 7. パラメータ解決（GUI/CC との統合）

### 7.1 parameter_context（フレーム境界で固定するもの）

実装: `src/parameters/context.py`

`parameter_context(store, cc_snapshot)` は contextvars で次を固定する。

- `param_snapshot` … `store.snapshot()`（読み取り専用ビュー）
- `frame_params` … `FrameParamsBuffer()`（この draw で観測した引数の収集先）
- `cc_snapshot` … 今フレームの CC 値辞書（現状 run 経路では None）
- `store` … ラベル設定等のために参照（`current_param_store()`）

`finally` で `frame_params.records` を `store.store_frame_params()` にマージするため、**そのフレームで呼ばれた引数が次フレーム以降 GUI に出る**。

### 7.2 resolve_params（base/GUI/CC の統合と量子化）

実装: `src/parameters/resolver.py`

`resolve_params(op, params, meta, site_id, ...)` は引数ごとに次を行う。

- `ParameterKey(op, site_id, arg)` を作る
- snapshot に状態があればそれを使用（meta/state/ordinal/label）
- 無ければ `meta.get(arg)` がある引数のみ GUI 対象として扱う（meta が無い引数は観測しない）
- `CC > GUI > base`（ただし bool は常に GUI）で effective を選ぶ
- 量子化（既定 `DEFAULT_QUANT_STEP=1e-3`）を **ここだけ** で行い、署名に入る値と実計算値を一致させる
- `FrameParamsBuffer` に観測レコードを積む（explicit/chain_id/step_index も記録）

### 7.3 初期 override ポリシー（“省略引数は GUI で動かしやすく”）

実装: `src/parameters/store.py:store_frame_params()`

`FrameParamRecord.explicit`（ユーザーが kwargs を明示したか）を使い、

- 明示 kwargs（explicit=True）: `initial_override=False`（コードの base を優先）
- 省略 kwargs（explicit=False）: `initial_override=True`（GUI 値を優先）

という初期状態を作る（既に state がある場合は上書きしない）。

### 7.4 Style / Layer style の扱い（特殊キー）

Geometry の引数解決（`resolve_params`）とは別に、描画見た目のための “Style 行” を `ParamStore` に持つ。

- Global style: `src/parameters/style.py`
  - `STYLE_OP="__style__"`, `STYLE_SITE_ID="__global__"`
  - `background_color`, `global_thickness`, `global_line_color`
  - `DrawWindowSystem` がフレーム冒頭に `store.get_state()` で直接参照して適用する
- Layer style: `src/parameters/layer_style.py`
  - `LAYER_STYLE_OP="__layer_style__"`
  - `line_thickness`, `line_color`
  - `render_scene()` が Layer ごとにエントリを確保し、override=True の場合だけ上書きして描画する

## 8. Parameter GUI（pyimgui）アーキテクチャ

GUI は「描画（imgui）」「データ変換（純粋関数）」「store 反映」を分離している。

- 入口（ライフサイクル）: `src/app/parameter_gui/gui.py`
  - ImGui context を生成し、毎フレーム `render_store_parameter_table(store)` を呼ぶ
- backend（pyglet 依存）: `src/app/parameter_gui/pyglet_backend.py`
  - window 生成、IO 同期、renderer 作成
- store ↔ rows ↔ UI の橋渡し: `src/app/parameter_gui/store_bridge.py`
  1) `store.snapshot()` を読む  
  2) `rows_from_snapshot()` で `ParameterRow` に変換（`src/parameters/view.py`）  
  3) 表示順・グルーピング用の付帯情報（primitive ラベル、effect chain、layer style 名）を作る  
  4) `render_parameter_table(rows)`（imgui 描画）を呼び、更新後 rows を受け取る  
  5) 差分があれば `update_state_from_ui()` / `store.set_meta()` で store に反映
- “純粋なロジック” を集約:
  - `src/parameters/view.py` … 値正規化・rows 生成・state 反映 API（imgui 非依存）
  - `src/app/parameter_gui/grouping.py` / `group_blocks.py` / `labeling.py` … 表示名・ブロック化
  - `src/app/parameter_gui/rules.py` … kind/op ごとの列表示ルール
  - `src/app/parameter_gui/widgets.py` … kind→widget の対応（imgui 呼び出しはここに寄せる）

## 9. 描画（ModernGL）パイプライン

### 9.1 シーン正規化 → realize → 描画

実装: `src/render/frame_pipeline.py`

`render_scene(draw, t, defaults, renderer)` は、Layer 単位で次を実行する。

1) `scene = draw(t)`  
2) `layers = normalize_scene(scene)`（`src/render/scene.py`）  
   - `Geometry` が直接返ってきたら `Layer(geometry=item, site_id=f"implicit:{item.id}")` に包む
3) `resolve_layer_style(layer, defaults)` で None 欠損を埋める
4) `ParamStore` があれば layer_style を確保し、override=True の場合に GUI 値で上書き
5) `realize(geometry)` → `indices = build_line_indices(offsets)` → `renderer.render_layer(...)`

### 9.2 GPU レンダラーの構成

- `src/render/draw_renderer.py:DrawRenderer`
  - pyglet window の GL context 上で ModernGL context を生成
  - `Shader.create_shader()` でプログラム作成
  - `LineMesh` に頂点/インデックスを upload して `ctx.LINES` で描画
- `src/render/shader.py`
  - vertex: 2D（xy）を `projection` で NDC に変換
  - geometry: line（2頂点）を太さ付き四角形（triangle_strip 4頂点）に展開
  - fragment: 単色
- `src/render/utils.py:build_projection`
  - `canvas_size` に基づく正射影行列を生成（y 軸は画面座標系に合わせて反転）
- `src/render/index_buffer.py:build_line_indices`
  - `offsets` から “隣接頂点を結ぶ GL_LINES の index 列” を生成し、ポリライン区切り用に primitive restart index を挿入する

## 10. ランタイム（複数ウィンドウの統合ループ）

実装: `src/app/runtime/window_loop.py`

`MultiWindowLoop` は pyglet の複数 window を 1 ループで回す。

- 各 window について `dispatch_events()` → `draw_frame()` → `flip()` を 1 回ずつ実行する
- 目的:
  - flip の呼び出し箇所を 1 箇所に集約し、点滅や更新競合を避ける
  - GUI と描画を同一 FPS で同期させやすくする

`src/api/run.py` はこのループの “配線” に徹し、

- 描画: `DrawWindowSystem`（`src/app/runtime/draw_window_system.py`）
- GUI: `ParameterGUIWindowSystem`（`src/app/runtime/parameter_gui_system.py`）

のサブシステムとして組み立てる。

## 11. 現状の制約・未実装（ドキュメント上の注意）

README には MIDI/動画/G-code/ショートカット等の構想があるが、`src/` の現状実装に存在しないものがある。

- CC 経路は `parameter_context(cc_snapshot=...)` と `resolve_params(..., source="cc")` まで用意されているが、
  現行 `run` 経路では `cc_snapshot=None` のため、MIDI 入力の取り込みは未接続
- スクリーンショット/動画/G-code エクスポートは `src/` には未実装（README 記載のみ）
- `RealizeCache` の eviction（容量上限/LRU）は未実装

このファイルは **現状の `src/` 実装** に合わせて記述しているため、README/spec と齟齬がある場合は `src/` を正として読み替える。
