# src 配下コードレビュー（2025-12-12）

## 対象

- `src/api/*`
- `src/app/*`
- `src/core/*`
- `src/effects/*`
- `src/parameters/*`
- `src/primitives/*`
- `src/render/*`

## 総評

Geometry（不変 DAG）→ realize（評価）→ render（描画）という依存方向が素直で、`src/api` のファサード（`G/E/L/run`）も含めて「ユーザーが触る面」と「内部実装」の分離ができています。`parameters` はフレーム境界で snapshot を固定する設計になっており、リアルタイム描画と相性が良いです。

一方で、現状は「機能として存在するがまだ繋がっていない（labels/GUI/serialize）」部分が点在しており、加えて `__pycache__/*.pyc` が `src/` 配下に残っているため、リポジトリの衛生と不具合の切り分けを難しくしています。

## 良い点

- `src/core/geometry.py`: 引数正規化 → 内容署名（`blake2b`）で `GeometryId` を作る設計が明快で、キャッシュと相性が良い。
- `src/core/realize.py`: `realize_cache` + inflight で重複計算を避ける実装が最小限で読みやすい。
- `src/parameters/context.py`: `contextvars` によるフレーム固定がスレッド/並列に自然に拡張できる。
- `src/render/frame_pipeline.py` + `src/render/scene.py`: user_draw の戻り値を正規化して描画パイプラインに落とす流れがシンプル。
- `src/api/*`: `G/E/L` による API の見通しが良い（登録/実装の詳細を隠蔽できている）。

## 要修正（バグ/仕様不整合の可能性が高い）

### 1) kind 名の不整合（`"str"` vs `"string"`）

- `src/parameters/meta.py` は `ParamMeta.kind` として `"str"` を返す一方、`src/parameters/view.py` は `kind == "string"` を分岐に使っており、`str` が正常に扱われない可能性が高い。
- `src/app/parameter_gui.py` も現状 `"float"` 以外は unknown 扱いで落ちるため、`parameters` の設計と UI がまだ繋がっていない状態。

### 2) ラベル機能がスナップショット/表示に反映されない

- `src/api/primitives.py` / `src/api/effects.py` では `ParamStore.set_label()` を呼べる設計だが、`src/parameters/view.py:rows_from_snapshot()` が snapshot 内の `label` を無視しており、表示に出ない（実装されているのに使われない）。

### 3) `ParamStore.to_json()` が tuple 値で壊れる可能性

- `json.dumps()` は tuple を標準ではエンコードできないため、`ui_value` が `tuple`（例: `"vec3"`/`"rgb"`）になった時点で `to_json()` が例外になり得る。
- `ParamMeta.choices` 等も含め、永続化フォーマットとして「JSON に落ちる型」へ寄せる方針を早めに決めるのが安全。

## 優先度高めの改善提案（品質/保守性）

### 4) `src/**/__pycache__/*.pyc` がリポジトリに存在

- `src/` 配下に複数 Python バージョンの `.pyc` があり、かつ存在しないモジュール名の `.pyc` も含まれている（例: `geometry_registry` や `debug_renderer` 等）。
- これがあると「実際のソース」と「過去の実行結果」が混ざり、レビュー/デバッグのノイズになるため、削除 + `.gitignore` で再発防止が望ましい。

### 6) Render の線幅単位が設計コメントとズレる可能性

- `src/api/run.py` は `line_thickness` を「ワールド単位」と説明しているが、`src/render/shader.py` のジオメトリシェーダは clip space 上で `line_thickness` をそのまま加算している。
- 期待する線幅が「ワールド」なのか「画面/clip」なのかを明確化し、必要なら `RenderSettings.canvas_size` 等から換算して uniform を渡すのがよさそう。

### 7) `LineMesh._ensure_capacity()` が常に VAO を作り直す

- `src/render/line_mesh.py` は resize の有無に関係なく毎回 `simple_vertex_array()` を呼んでおり、アップロード頻度が高いと無駄が出やすい。
- 「バッファを差し替えた時だけ VAO を張り直す」にすると意図と実装が一致する。

## 中優先度の改善提案（スタイル/型/表現）

- `src/core/*_registry.py` の `items()` の戻り値注釈に `type: ignore` があり、mypy/ruff 的にも気持ち悪い。`collections.abc.ItemsView` 等で素直に表現できる。
- `src/render/utils.py` は他ファイルに比べてヘッダ/説明が薄いので、同じ形式（どこで/何を/なぜ）に揃えると統一感が出る。

## 次に確認したいこと（質問）

- GUI は `pyimgui` 想定で、`ParamStore` の永続化（`to_json/from_json`）はどのタイミングで使う予定か？
- `ParamMeta.kind` の許容値は `"float/int/bool/str/choice/vec3/rgb"` で確定か、それとも UI 実装に合わせて増やす想定か？
- 「labels」は UI 上で何を指す想定か（primitive/effect 単位の見出し、引数の表示名、あるいはノード名）？
