# どこで: `docs/parameter_gui_phase3_style_impl_plan.md`
#
# 何を: `docs/parameter_gui_headers_labeling_checklist.md` の Phase 3（Style）を、現状実装に合わせて実装するための計画。
#
# なぜ: Style は ParamStore/GUI/描画ループの 3 箇所に跨るため、最小の“通る”縦スライスから順に進められるようにする。

## 現状確認（実装の前提）

- 描画側
  - `src/api/run.py` は `background_color: tuple[float, float, float]`（RGB、alpha=1.0 固定）を受け取る。
  - `RenderSettings.background_color` は RGB。
  - `DrawRenderer.clear(color)` は `ctx.clear(r, g, b, 1.0)`。
  - `LayerStyleDefaults` は `color: tuple[float, float, float]` / `thickness: float`（＝global_line_color / global_thickness に相当）。
- GUI/ParamStore 側
  - GUI は `src/app/parameter_gui/store_bridge.py` → `src/app/parameter_gui/table.py` の 4 列テーブルで ParamStore を編集する。
  - 表示順は現状「Primitive → Effect（チェーン順）→ その他」。
  - kind は GUI 側で `float/int/vec3/bool/string/choice` を描画できるが、色編集用の `color_edit3` は未導入。
  - `src/parameters/view.py` の `kind == "rgb"` は 0–255 int を正としている（描画側の float 0–1 とは変換が必要）。

## 方針（ここでは代替案を増やさない）

- Style 値は ParamStore に統合する（“特殊 op/site_id/arg” で表現）。
- GUI では Style を最上段に 1 セクションとして出す（`collapsing_header("Style")`）。
- 色（`kind="rgb"`）は **0–255 int の RGB** を正とする（GUI は `imgui.color_edit3(..., COLOR_EDIT_UINT8)` を使う）。
- 描画側（renderer/shader）が使う RGB は 0–1 float のため、適用直前に 0–255→0–1 変換する。

## 実装計画（チェックリスト）

### 1) Style を ParamStore に載せる（キー設計 + 初期化）

- [x] Style 用の定数/キーを定義する（`src/parameters/style.py`）
  - `STYLE_OP = "__style__"`
  - `STYLE_SITE_ID = "__global__"`
  - arg は `background_color`, `global_thickness`, `global_line_color`
- [x] `DrawWindowSystem.__init__` で style key の meta/state を初期化する
  - [x] 3 つの style key を `store.ensure_state(..., base_value=...)` で作成する
  - [x] `store.set_meta(key, meta)` で GUI 対象にする（snapshot に載る条件が meta のため）
  - [x] `initial_override=True`（初期は GUI 上書きを有効にして、触らなくても base と一致する）
- [x] Style の `ParamMeta` を固定する
  - [x] `background_color`: `ParamMeta(kind="rgb", ui_min=0, ui_max=255)`
  - [x] `global_line_color`: 同上
  - [x] `global_thickness`: `ParamMeta(kind="float", ui_min=0.0, ui_max=...)`

### 2) `kind="rgb"` を GUI で `color_edit3`（0–255 表示）できるようにする

- [x] `src/app/parameter_gui/widgets.py`
  - [x] `widget_rgb_color_edit3(row)` を追加（内部は float 0–1 へ変換し、`COLOR_EDIT_UINT8` で 0–255 表示）
  - [x] `_KIND_TO_WIDGET["rgb"] = widget_rgb_color_edit3` を追加
- [x] `src/app/parameter_gui/table.py`
  - [x] Column 3（min-max）は `rgb` では表示しない
  - [x] Column 4（cc/override）は `rgb` は「override だけ」にする（cc は一旦無し）

### 3) GUI に Style セクションを追加する（表示順 + ヘッダ）

- [x] `src/app/parameter_gui/store_bridge.py`
  - [x] style 行（`row.op == STYLE_OP`）を抽出し、表示順の最上段へ移動する
  - [x] 既存の primitive/effect/other の並びは維持する（style だけ先頭へ）
- [x] `src/app/parameter_gui/table.py`
  - [x] style 行を 1 グループとして扱い、グループ境界で `collapsing_header("Style")` を描画する
  - [x] style 行の 1 列目は `row.arg`（例: `background_color`）を表示する

### 4) 描画へ反映する（次フレームに反映される経路）

- [x] `src/app/runtime/draw_window_system.py`
  - [x] フレーム冒頭で style の effective 値を解決し、描画へ適用する
    - [x] `background_color` を `renderer.clear(...)` に使う
    - [x] `LayerStyleDefaults` を `global_thickness/global_line_color` で組み立て、`render_scene(..., defaults=...)` に渡す
  - [x] 色は 0–255→0–1 に変換してから renderer/shader へ渡す
  - [x] style の float 量子化で見た目が変わるのを避けるため、style は resolver を経由せず store 状態を直接参照する

### 5) テスト（最小・重くしない）

- [x] `tests/parameters/*`
  - [x] `rgb`（0..255 clamp）の正規化テストが落ちないこと
- [ ] `tests/app/*`（純粋関数/ロジックだけ）
  - [ ] `DrawWindowSystem` そのものの描画テストは増やさない（GL/pyglet 依存が重い）

## 完了条件（Phase 3 / Style）

- GUI に `Style` の `collapsing_header` が 1 つ出る。
- 直下に `background_color`, `global_thickness`, `global_line_color` の 3 行が出る。
- 変更した値が「次フレームの描画」に反映される。
  - 背景色が変わる
  - 既定線幅/既定線色が変わる（Layer 側で未指定のとき）

## 既知の割り切り（Phase 3 の範囲外）

- Layer セクション（L(name) ごとの thickness/color）は Phase 4。
- CC 入力は style では当面無効（必要なら後で追加）。
