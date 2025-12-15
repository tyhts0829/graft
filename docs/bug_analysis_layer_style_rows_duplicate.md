# bug_analysis_layer_style_rows_duplicate.md

どこで: `src/api/layers.py`（`L(...)` の site_id 生成）、`src/render/frame_pipeline.py`（Layer style 行の生成）、`src/parameters/store.py`（GUI 表示/保存前 prune）、`data/output/param_store/*.json`（永続化）。
何を: 「Layer のパラメータ行（line_thickness/line_color）が複製される」現象の原因を調査してまとめる。
なぜ: `main.py` の編集と再起動を繰り返すと、Style セクション内に過去の Layer 行が残って UI が汚れ、どれが現行 Layer か分からなくなるため。

## 1. 現象（整理）

- Parameter GUI の Style セクション内にある、Layer ごとの
  - `line_thickness`
  - `line_color`
  の行が、再起動/編集を繰り返すと増えていく（= “複製されて見える”）。
- `data/output/param_store/main.json` にも `__layer_style__` の古いエントリが残り続ける。

補足（現状の観測）:

- 現在の `data/output/param_store/main.json` には `__layer_style__` の `site_id` が複数（例: `main.py:19:254`, `main.py:19:258`, …）残っている。
- 一方 `main.py` の `draw()` は `L(...)` 呼び出しが 2 つなので、期待としては Layer style のグループは 2 つに収まってほしい。

## 2. Layer の「パラメータ行」がどう作られているか（実装の流れ）

### 2.1 Layer style 行のキーは `Layer.site_id`（callsite）で決まる

- `src/api/layers.py` の `L(...)` は、Layer の識別子として
  - `site_id = caller_site_id(skip=1)`
  を採用している。
- そして `Layer(site_id=site_id, ...)` を返す。

この `site_id` が、そのまま Layer style 永続化のキーになる（重要）。

### 2.2 描画パイプラインが毎フレーム `__layer_style__` の state/meta を作る

- `src/render/frame_pipeline.py` の `render_scene()` は、各 `Layer` について
  - `ensure_layer_style_entries(store, layer_site_id=layer.site_id, ...)`
  を呼び、ParamStore に `__layer_style__` の行を作る。
- `ensure_layer_style_entries()`（`src/parameters/layer_style.py`）は、
  - `(op="__layer_style__", site_id=layer_site_id, arg="line_thickness")`
  - `(op="__layer_style__", site_id=layer_site_id, arg="line_color")`
  の 2 行を `store.ensure_state()` + `store.set_meta()` で登録する。

GUI は ParamStore の snapshot から行を生成するので、**store に残っている `__layer_style__` の行は、そのまま GUI に出続ける**。

## 3. 原因（結論）

原因は 2 つが合わさったもの。

### 原因A: `Layer.site_id` がコード編集で変わりやすい

- `caller_site_id()` は `src/parameters/key.py` の `make_site_id()` を使い、形式が:
  - `"{filename}:{co_firstlineno}:{f_lasti}"`
  になっている。
- ここで `f_lasti` は「最後に実行したバイトコードのオフセット」なので、
  - 引数の追加
  - 空行の追加/削除
  - ちょっとしたリファクタ
  でも変わり得る。

結果:

- 同じ `L(...)` 呼び出し箇所のつもりでも、編集後の再起動で `site_id` が別物になり、
  **新しい Layer style グループ**が作られる。

### 原因B: `__layer_style__` は prune/hide 対象から外してある（意図的な未対応）

現行の ParamStore は、増殖対策（reconcile/prune）をまず primitive/effect に限定しており、
`STYLE_OP="__style__"` と `LAYER_STYLE_OP="__layer_style__"` は対象外になっている。

- `src/parameters/store.py` の `snapshot_for_gui()` は stale グループを隠すが、
  - `STYLE_OP` と `LAYER_STYLE_OP` を除外しているため、**古い Layer style が GUI から隠れない**
- 同じく `prune_stale_loaded_groups()` も、
  - `STYLE_OP` と `LAYER_STYLE_OP` を除外しているため、**古い Layer style が保存前に削除されない**

よって、コード編集で `site_id` が変わるたびに:

1. 新しい `__layer_style__` グループが追加される
2. 古い `__layer_style__` グループが残り続ける
3. GUI 上では Layer 行が “複製” されたように見える
4. JSON も肥大化する

※ この挙動は `docs/done/parameter_gui_phase4_layer_impl_plan.md` の「古い Layer の整理は後回し（溜まるのを許容）」という割り切りと整合しており、現時点では “仕様上の未実装” が表面化している状態。

## 4. 追加の注意点（このままでは直しにくい箇所）

Layer style は `FrameParamRecord` 経由で store に入るわけではなく、`ensure_layer_style_entries()` が直接 `ensure_state()` している。
そのため、primitive/effect で使っている `store._observed_groups`（`store_frame_params()` 由来）には **`__layer_style__` が入らない**。

つまり、もし将来 `__layer_style__` も “observed-only” で掃除したいなら、

- `ensure_layer_style_entries()` 側で「このフレームで layer_site_id を観測した」情報を ParamStore に渡す

という仕組みが別途必要になる（ここは原因というより、修正時の設計ポイント）。

