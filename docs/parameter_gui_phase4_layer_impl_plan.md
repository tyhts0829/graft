# どこで: `docs/parameter_gui_phase4_layer_impl_plan.md`
#
# 何を: `docs/parameter_gui_headers_labeling_checklist.md` の Phase 4（Layer セクション: L(name) ごと thickness/color を GUI で編集）を、現状実装に合わせて進める実装計画。
#
# なぜ: Layer は「draw の戻り値（Scene）→ normalize → resolve_layer_style → render」の流れに入り込む必要があり、識別子/発見/上書き適用の設計が重いため、最小の縦スライスに分解して安全に実装する。

## ゴール（Phase 4 の完了条件）

- GUI の表示順が `Style → Layer → Primitive → Effect → other` になる。
- Layer セクションで `L(name)`（またはフォールバック名）ごとに `thickness` 行と `color` 行が出る。
- `thickness/color` の変更が「次フレームの描画」に反映される。
- 同名 Layer が複数あっても GUI 上で区別できる（表示専用の `#1/#2` 連番）。

## 現状（前提）

- Style は ParamStore の特殊キー（`__style__`）として統合済みで、GUI 先頭に `collapsing_header("Style")` が出る。
- 描画の Layer は `src/render/scene.py:normalize_scene()` が `list[Layer]` に正規化し、`src/render/frame_pipeline.py:render_scene()` が `resolve_layer_style()` を通して描画する。
- ただし、現行 `Layer` モデルには「どの L(...) 由来か」を表す識別子（site_id）が無い。

## 方針（Phase 4 は “素直に通す”）

- Layer の識別は **callsite_id（site_id）** を正とする（name だけで識別しない）。
- Layer の GUI 編集値は ParamStore に統合する（Style と同様に “特殊 op” で表現する）。
- GUI 上の衝突解消（同名）は **表示専用** に `name#1/#2` を付与（永続化ラベルは変えない）。
- まずは「このフレームで見えた Layer が GUI に現れ、次フレームから上書きが効く」までを完了とする。
  - 使われなくなった Layer の自動削除・グレーアウトは後回し（溜まるのを許容）。

## 実装計画（チェックリスト）

### 1) Layer に site_id を持たせる（識別子の土台）

- [ ] `src/render/layer.py`
  - [ ] `Layer` に `site_id: str` を追加する（Layer セクションのキーに使う）
  - [ ] 既存コード/テストを修正して通す
- [ ] `src/api/layers.py`
  - [ ] `L(...)` の呼び出し site_id を `caller_site_id()` で取得し `Layer.site_id` に入れる
  - [ ] `name` が与えられた場合は `ParamStore.set_label(layer_op, site_id, name)` を永続化する（Primitive/E と同じ “最後勝ち”）
- [ ] `src/render/scene.py`
  - [ ] `Geometry` を暗黙に `Layer(...)` に包む経路の site_id を決める
    - 例: `site_id = f"implicit:{geometry.id}"`（最低限の安定性を優先）

### 2) ParamStore に Layer style 用キーを載せる（データ表現）

- [ ] 新規: `src/parameters/layer_style.py`（Style と同様の “キー定義 + 変換” を置く）
  - [ ] `LAYER_OP = "__layer__"`（衝突しない特殊 op）
  - [ ] `layer_style_key(layer_site_id, arg)`（arg は `"thickness"` / `"color"`）
  - [ ] 色変換は既存 `src/parameters/style.py` の `rgb01_to_rgb255/rgb255_to_rgb01` を再利用（重複しない）
- [ ] meta の方針（最小）
  - [ ] `thickness`: `ParamMeta(kind="float", ui_min=1e-6, ui_max=0.01)`（0 以下を作れない前提に寄せる）
  - [ ] `color`: `ParamMeta(kind="rgb", ui_min=0, ui_max=255)`
  - [ ] min-max 列（Column 3）は layer thickness でも編集不可に寄せる（必要なら後で解放）

### 3) Layer の “発見（観測）” と “上書き適用” を描画パイプラインへ入れる

- [ ] `src/render/frame_pipeline.py` もしくは `src/app/runtime/draw_window_system.py` のどちらかに責務を寄せる（二重計算しない）
  - 推奨: `frame_pipeline` 側で `current_param_store()` を見て処理する（Layer は draw の戻り値に近いのでここが素直）
- [ ] 観測（各フレーム）
  - [ ] `normalize_scene` 後の `layers` を走査し、各 layer ごとに
    - [ ] `resolve_layer_style(layer, defaults)` で base（実描画値）を確定
    - [ ] base を ParamStore へ登録（初出のみ）
      - [ ] `thickness` key: base_value = resolved.thickness
      - [ ] `color` key: base_value = rgb01_to_rgb255(resolved.color)
      - [ ] initial_override は「コードが指定していない場合 True」にする
        - thickness: `layer.thickness is None` → True
        - color: `layer.color is None` → True
    - [ ] `layer.name` があればラベルも保存（`set_label(LAYER_OP, layer.site_id, layer.name)`）
- [ ] 上書き適用（描画時）
  - [ ] `override=True` の場合のみ GUI 値を採用し、`override=False` は base（resolved）へ戻す
  - [ ] `color` は `rgb255_to_rgb01(state.ui_value)` にして renderer に渡す
  - [ ] `thickness <= 0` が入り得る経路は作らない（GUI でクランプ）

### 4) GUI: Layer セクションを挿入して表示する（並び順・ヘッダ・行ラベル）

- [ ] `src/app/parameter_gui/labeling.py`
  - [ ] `layer_header_display_names_from_snapshot(snapshot, ...)` を追加
    - base 名は `label` があればそれ、無ければ `layer#{ordinal}`（必ず区別できるフォールバック）
    - 表示専用の衝突解消は `dedup_display_names_in_order` を再利用
- [ ] `src/app/parameter_gui/store_bridge.py`
  - [ ] `rows_from_snapshot` の結果を `style_rows → layer_rows → primitive_rows → effect_rows → other_rows` に並べる
  - [ ] layer_rows は `ordinal` ごとに `thickness` → `color` の順になるよう安定ソートする
- [ ] `src/app/parameter_gui/table.py`
  - [ ] `row.op == LAYER_OP` を検出し、`group_id=("layer", site_id)` で `collapsing_header(layer_header)` を描画
  - [ ] layer 行の 1 列目は `row.arg`（`thickness` / `color`）を表示する

### 5) テスト（最小・重くしない）

- [ ] `tests/app/`
  - [ ] layer header 表示名の衝突解消（同名 `name#1/#2`）をユニットテストで担保
  - [ ] layer 行の並び（Style→Layer→Primitive…、layer 内が thickness→color）をユニットテストで担保
- [ ] `tests/render/` または `tests/parameters/`
  - [ ] 「override の有無で base/GUI が切り替わる」純粋部分を関数化できるなら unit テスト
  - [ ] GL/pyglet 依存の描画テストは追加しない（手動確認に寄せる）

## 手動確認（最小）

- `python main.py` を実行し、GUI に Layer セクションが出る
- `L(name="...")` を使っている場合、ヘッダ名が name になる
- layer thickness/color を変えると、次フレームの描画に反映される

## 既知の割り切り（Phase 4 の範囲外）

- CC による Layer 色（rgb）の自動変化は未対応（入力欄は表示できるが、解決経路がまだ無い）
- “古い Layer の整理（消滅/グレーアウト）” は後回し
