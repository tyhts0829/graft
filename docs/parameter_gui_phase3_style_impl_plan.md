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
  - `src/parameters/view.py` には `kind == "rgb"` 分岐があるが、現状の正規化は 0–255 int 前提になっている（描画側の float 0–1 とズレる）。

## 方針（ここでは代替案を増やさない）

- Style 値は ParamStore に統合する（“特殊 op/site_id/arg” で表現）。
- GUI では Style を最上段に 1 セクションとして出す（`collapsing_header("Style")`）。
- 色は **float 0–1 の RGB** を正とし、GUI のコントロールは **`imgui.color_edit3`** を使う。
  - そのため `kind="rgb"` の意味を「0–1 float の RGB」へ寄せ、0–255 int 前提は廃止する（破壊的変更 OK の方針に従う）。

## 実装計画（チェックリスト）

### 1) Style を ParamStore に載せる（キー設計 + 初期化）

- [ ] Style 用の定数を決める（例）
  - `STYLE_OP = "__style__"`
  - `STYLE_SITE_ID = "__global__"`
  - arg は `background_color`, `global_thickness`, `global_line_color`
- [ ] `src/api/run.py`（または `DrawWindowSystem.__init__`）で ParamStore を初期化するタイミングで
  - [ ] 3 つの style key を `store.ensure_state(..., base_value=...)` で作成する
  - [ ] `store.set_meta(key, meta)` で GUI 対象にする（snapshot に載る条件が meta のため）
  - [ ] `initial_override=True`（初期は GUI 上書きを有効にして、触らなくても base と一致する）を明示する
    - ui_value は base で初期化されるので、override=True でも見た目は変わらない
- [ ] Style の `ParamMeta` を固定する
  - [ ] `background_color`: `ParamMeta(kind="rgb")`（range は不要 or `ui_min=0.0, ui_max=1.0`）
  - [ ] `global_line_color`: 同上
  - [ ] `global_thickness`: `ParamMeta(kind="float", ui_min=0.0, ui_max=<適当>)`

### 2) `kind="rgb"` を float 0–1 に寄せ、GUI で `color_edit3` を使えるようにする

- [ ] `src/parameters/view.py`
  - [ ] `normalize_input(..., kind=="rgb")` を「(r,g,b) を float 化し 0..1 に clamp」へ変更
- [ ] `tests/parameters/test_parameter_normalize.py`
  - [ ] `kind="rgb"` の期待値を 0..1 float の clamp へ更新
- [ ] `tests/parameters/test_parameter_updates.py`
  - [ ] `rgb` の meta/値を 0..1 float 前提へ更新
- [ ] `src/app/parameter_gui/widgets.py`
  - [ ] `widget_rgb_color_edit3(row)` を追加（`imgui.color_edit3("##value", r, g, b)`）
  - [ ] `_KIND_TO_WIDGET["rgb"] = widget_rgb_color_edit3` を追加
- [ ] `src/app/parameter_gui/table.py`
  - [ ] Column 3（min-max）は `rgb` では表示しない（固定値なので）
  - [ ] Column 4（cc/override）は `rgb` は「override だけ」にする（cc は一旦無し）

### 3) GUI に Style セクションを追加する（表示順 + ヘッダ）

- [ ] `src/app/parameter_gui/store_bridge.py`
  - [ ] `rows_from_snapshot` の結果から style 行（`row.op == STYLE_OP`）を抽出し、表示順の最上段へ移動する
  - [ ] 既存の primitive/effect/other の並びは維持する（style だけ先頭へ）
- [ ] `src/app/parameter_gui/table.py`
  - [ ] style 行を 1 グループとして扱い、グループ境界で `collapsing_header("Style")` を描画する
  - [ ] style 行の 1 列目は `row.arg`（例: `background_color`）を表示する
    - 既存の `format_param_row_label(op#ordinal arg)` は style では使わない

### 4) 描画へ反映する（次フレームに反映される経路）

- [ ] `src/app/runtime/draw_window_system.py`
  - [ ] `parameter_context(self._store, ...)` のスコープを「clear + render_scene」まで含める
  - [ ] フレーム冒頭で style の effective 値を解決し、描画へ適用する
    - [ ] `background_color` を `renderer.clear(effective_background_color)` に使う
    - [ ] `LayerStyleDefaults` を `effective_global_thickness/global_line_color` で組み立て、`render_scene(..., defaults=...)` に渡す
  - [ ] “解決”は `resolve_params(op=STYLE_OP, site_id=STYLE_SITE_ID, params={...}, meta={...})` を流用して一元化する（source/base/gui の整合を揃える）

### 5) テスト（最小・重くしない）

- [ ] `tests/parameters/*`
  - [ ] `rgb` 正規化（0..1 clamp）が落ちないこと
- [ ] `tests/app/*`（純粋関数/ロジックだけ）
  - [ ] style 行を先頭へ出す “並び替え” ロジックを純粋関数化できるなら、ユニットテストで担保する
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
