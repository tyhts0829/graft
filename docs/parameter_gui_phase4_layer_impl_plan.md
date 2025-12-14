# どこで: `docs/parameter_gui_phase4_layer_impl_plan.md`
#
# 何を: Phase 4（Layer ごとの line_thickness/line_color を “Style ヘッダ内” に表示し、GUI で編集できるようにする）の実装計画。
#
# なぜ: Layer は「draw の戻り値（Scene）→ normalize → resolve_layer_style → render」の流れに入り込む必要があり、識別子/発見/上書き適用の設計が重いため、最小の縦スライスに分解して安全に実装する。

## ゴール（Phase 4 の完了条件）

- GUI の表示順が `Style → Primitive → Effect → other` になる（Layer は独立セクションにしない）。
- Style セクション内に、`L(name=...)` ごとの `line_thickness` 行と `line_color` 行が出る。
- 行ラベル（テーブル 1 列目）が `"{layer_name}#{ordinal} line_color"` 形式になる（例: `bg#1 line_color`）。
- `line_thickness/line_color` の変更が「次フレームの描画」に反映される。

## 現状（前提）

- Style は ParamStore の特殊キー（`__style__`）として統合済みで、GUI 先頭に `collapsing_header("Style")` が出る。
- 描画の Layer は `src/render/scene.py:normalize_scene()` が `list[Layer]` に正規化し、`src/render/frame_pipeline.py:render_scene()` が `resolve_layer_style()` を通して描画する。
- ただし、現行 `Layer` モデルには「どの L(...) 由来か」を表す識別子（site_id）が無い。

## 方針（Phase 4 は “素直に通す”）

- Layer の識別は **callsite_id（site_id）** を正とする（name だけで識別しない）。
- Layer の GUI 編集値は ParamStore に統合する（Style と同様に “特殊 op” で表現する）。
- GUI は Layer を独立セクションにせず、Style セクション内に “Layer 行” を並べる。
- 表示名の衝突は `"{layer_name}#{ordinal}"` の ordinal で区別する（永続化ラベルは変更しない）。
- まずは「このフレームで見えた Layer が GUI に現れ、次フレームから上書きが効く」までを完了とする。
  - 使われなくなった Layer の自動削除・グレーアウトは後回し（溜まるのを許容）。

## 実装計画（チェックリスト）

### 1) Layer に site_id を持たせる（識別子の土台）

- [ ] `src/render/layer.py`
  - [ ] `Layer` に `site_id: str` を追加する（Layer 行のキーに使う）
  - [ ] 既存コード/テストを修正して通す
- [ ] `src/api/layers.py`
  - [ ] `L(...)` の呼び出し site_id を `caller_site_id()` で取得し `Layer.site_id` に入れる
  - [ ] `name` が与えられた場合は `ParamStore.set_label(LAYER_STYLE_OP, site_id, name)` を永続化する（Primitive/E と同じ “最後勝ち”）
- [ ] `src/render/scene.py`
  - [ ] `Geometry` を暗黙に `Layer(...)` に包む経路の site_id を決める
    - 例: `site_id = f"implicit:{geometry.id}"`（最低限の安定性を優先）

### 2) ParamStore に Layer style 用キーを載せる（データ表現）

- [ ] 新規: `src/parameters/layer_style.py`（Style と同様の “キー定義 + 変換” を置く）
  - [ ] `LAYER_STYLE_OP = "__layer_style__"`（衝突しない特殊 op）
  - [ ] `layer_style_key(layer_site_id, arg)`（arg は `"line_thickness"` / `"line_color"`）
  - [ ] 色変換は既存 `src/parameters/style.py` の `rgb01_to_rgb255/rgb255_to_rgb01` を再利用（重複しない）
- [ ] meta の方針（最小）
  - [ ] `line_thickness`: `ParamMeta(kind="float", ui_min=1e-6, ui_max=0.01)`（0 以下を作れない前提に寄せる）
  - [ ] `line_color`: `ParamMeta(kind="rgb", ui_min=0, ui_max=255)`
  - [ ] min-max 列（Column 3）は layer の line_thickness でも編集不可に寄せる（必要なら後で解放）

### 3) Layer の “発見（観測）” と “上書き適用” を描画パイプラインへ入れる

- [ ] `src/render/frame_pipeline.py` もしくは `src/app/runtime/draw_window_system.py` のどちらかに責務を寄せる（二重計算しない）
  - 推奨: `frame_pipeline` 側で `current_param_store()` を見て処理する（Layer は draw の戻り値に近いのでここが素直）
- [ ] 観測（各フレーム）
  - [ ] `normalize_scene` 後の `layers` を走査し、各 layer ごとに
    - [ ] `resolve_layer_style(layer, defaults)` で base（実描画値）を確定
    - [ ] base を ParamStore へ登録（初出のみ）
      - [ ] `line_thickness` key: base_value = resolved.thickness
      - [ ] `line_color` key: base_value = rgb01_to_rgb255(resolved.color)
      - [ ] initial_override は「コードが指定していない場合 True」にする
        - thickness: `layer.thickness is None` → True
        - color: `layer.color is None` → True
    - [ ] `layer.name` があればラベルも保存（`set_label(LAYER_STYLE_OP, layer.site_id, layer.name)`）
- [ ] 上書き適用（描画時）
  - [ ] `override=True` の場合のみ GUI 値を採用し、`override=False` は base（resolved）へ戻す
  - [ ] `color` は `rgb255_to_rgb01(state.ui_value)` にして renderer に渡す
  - [ ] `thickness <= 0` が入り得る経路は作らない（GUI でクランプ）

### 4) GUI: Style セクション内に Layer 行を表示する（並び順・行ラベル）

- [ ] `src/app/parameter_gui/labeling.py`
  - [ ] Layer 行ラベル用の純粋関数を追加する（テストしやすさ優先）
    - 例: `format_layer_style_row_label(layer_name, ordinal, arg) -> "{layer_name}#{ordinal} {arg}"`
    - `layer_name` は snapshot.label（= `L(name=...)`）を優先し、無ければ `"layer"` を使う
- [ ] `src/app/parameter_gui/store_bridge.py`
  - [ ] `rows_from_snapshot` の結果を `style_rows（global） → style_rows（layer） → primitive_rows → effect_rows → other_rows` に並べる
  - [ ] layer の style 行は `ordinal` ごとに `line_thickness` → `line_color` の順になるよう安定ソートする
- [ ] `src/app/parameter_gui/table.py`
  - [ ] `row.op == STYLE_OP` と `row.op == LAYER_STYLE_OP` をどちらも Style グループ扱いにする
  - [ ] layer 行の 1 列目を `"{layer_name}#{ordinal} {arg}"` にする（例: `bg#1 line_color`）
  - [ ] layer の line_thickness は Column 3（min-max）を編集不可に寄せる（global_thickness と同様）

### 5) テスト（最小・重くしない）

- [ ] `tests/app/`
  - [ ] Layer 行ラベル整形（`bg#1 line_color`）をユニットテストで担保
  - [ ] Style セクション内の並び（global → layer → primitive…、layer 内が line_thickness→line_color）をユニットテストで担保
- [ ] `tests/render/` または `tests/parameters/`
  - [ ] 「override の有無で base/GUI が切り替わる」純粋部分を関数化できるなら unit テスト
  - [ ] GL/pyglet 依存の描画テストは追加しない（手動確認に寄せる）

## 手動確認（最小）

- `python main.py` を実行し、GUI の Style セクション内に `bg#1 line_color` のような行が出る
- layer の line_thickness/line_color を変えると、次フレームの描画に反映される

## 既知の割り切り（Phase 4 の範囲外）

- CC による Layer 色（rgb）の自動変化は未対応（入力欄は表示できるが、解決経路がまだ無い）
- “古い Layer の整理（消滅/グレーアウト）” は後回し
