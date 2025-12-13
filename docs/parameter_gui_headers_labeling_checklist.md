# どこで: `docs/parameter_gui_headers_labeling_checklist.md`。
# 何を: `docs/parameter_gui_phase3_checklist.md` の「ヘッダ行・ラベリング（Style / Primitive / Effectチェーン / Layer）」を、素直に実装できる順に段階的に実装する計画。
# なぜ: 現状の実装・データモデルに合わせて“まずできるもの”から積み上げ、必要な設計追加（chain_id / style store / layer discovery）を後段で安全に入れるため。

## ゴール（最終形）

- Parameter GUI に以下の順で「ヘッダ行」を表示できる。
  1) Style（background/global_thickness/global_line_color）
  2) Layer（L(name) ごとに thickness/color）
  3) Primitive（op 名 or ラベル）
  4) Effect チェーン（effect#N or ラベル）
- ラベルは ParamStore に永続化され、GUI は snapshot から取り出して表示する。
- 同名が衝突した場合は GUI 表示名を `name#1`, `name#2` のように自動付与して区別する。

## 現状確認（すでに出来ている/出来ていない）

- ラベル永続化（出来ている）
  - `G(name=...)` / `E(name=...)` が `ParamStore.set_label(op, site_id, name)` に保存する。
  - `ParamStore.snapshot()` は各 ParameterKey に対応する `label` を返す。
- GUI が label を表示に使う（出来ていない）
  - `rows_from_snapshot()` が snapshot の label を捨てている（`_label`）。
  - GUI の表示ラベルは `op#ordinal` 固定。
- Style / Layer セクション（出来ていない）
  - background/global thickness/global line_color は ParamStore とは別系統（run/settings/defaults）。
  - Layer の一覧（L(name)）は ParamStore に観測されていない。
- Effect「チェーン」単位のヘッダ（出来ていない）
  - ordinal は op ごとなので、チェーン境界やチェーン順序が復元できない。
  - チェーンID（chain_id）の概念がまだ無い。
- 用語のズレ
  - チェックリスト本文は `label(name=...)` と書いているが、実装は `G(name=...)` / `E(name=...)` 方式。

## 実装順序（“素直にできる”→“設計追加が必要”）

### Phase 1（最小・素直）: ParamStore の label を GUI の Primitive/Effect(ステップ) 表示に反映 + 重複連番

- [ ] 表示ルールの確定
  - [ ] ラベル付与 API は現行の `G(name=...)` / `E(name=...)` を採用（`label(name=...)` は作らない）
  - [ ] 「複数回 label 呼びは例外」扱いにするか決める
    - 現状は「最後勝ち（上書き可）」で実装/テスト済みなので、例外化するなら破壊的変更になる
- [ ] GUI が snapshot.label を取り出して使えるようにする
  - [ ] 方式A: `rows_from_snapshot()` を拡張して label を ParameterRow に載せる（view 側で責務を持つ）
  - [ ] 方式B: `render_store_parameter_table()` が snapshot を見て header 表示名を計算する（GUI 側で責務を持つ）
  - [ ] どちらか一方に寄せ、二重計算は避ける
- [ ] 重複名の自動付与（`name#1`, `name#2`）
  - [ ] “衝突解消”は表示専用（永続化は元の label のまま）にする
  - [ ] 同じ op で複数 site がある場合も視認できるよう、未指定時のデフォルト名に ordinal を混ぜるか検討
- [ ] テスト（最小）
  - [ ] 表示名重複の連番付与をユニットテストで担保（純粋関数に切り出すのが楽）

完了条件:
- GUI に「Primitive ヘッダ」相当の見出し（label or op）が出る（まずは table 内の group header でも可）。
- 同名ラベル衝突時に `#1/#2` で区別できる。

### Phase 2（設計追加・中）: Effect “チェーン” ヘッダ（effect#N or ラベル）を正しく出す

- [ ] チェーン境界を復元できるデータを追加する
  - [ ] `EffectBuilder` に chain_id を導入（例: “最初の step の site_id” を chain_id とする）
  - [ ] 観測レコード（FrameParamRecord）へ chain_id を載せる（key とは別フィールド）
  - [ ] ParamStore が chain_id の ordinal（N）を安定に割り当てられるようにする（`effect#N` の N）
- [ ] チェーンのラベル永続化
  - [ ] `E(name=...)` が「各ステップ」ではなく「チェーン」へ label を保存できるようにする
    - 例: `set_label("__effect_chain__", chain_id, name)` のような別名前空間を持つ
- [ ] GUI 側で chain_id ごとにグルーピングし、チェーンヘッダ→各ステップのパラメータ、の順で表示する
- [ ] テスト（最小）
  - [ ] `E(name="chain").scale(...).rotate(...)(g)` で “チェーンヘッダが1回だけ出る” ことを担保

完了条件:
- “チェーン”が GUI 上でまとまって見える（デフォルト名 `effect#N` も安定）。

### Phase 3（設計追加・大）: Style ヘッダ（background/global_thickness/global_line_color）を GUI で編集できるようにする

- [ ] Style を保持する場所を決める（ParamStore に統合するか、別ストアを作るか）
  - [ ] まずは ParamStore へ統合する案（特殊 op/site_id/arg で表現）を第一候補にする
- [ ] 型/kind を揃える
  - [ ] background_color（RGBA）をどう表現するか決める（vec4 kind を追加 or RGB+alpha 分割など）
  - [ ] global_line_color（RGB）をどう表現するか決める（vec3/ rgb kind 追加など）
- [ ] run/render への反映経路を追加
  - [ ] GUI 値で `renderer.clear(background_color)` を上書きできる
  - [ ] GUI 値で LayerStyleDefaults（global_thickness/global_line_color）を上書きできる
- [ ] テスト（最小）
  - [ ] 依存が重いので unit テストは「値の解決」まで、描画は手動確認に寄せる

完了条件:
- Style セクションが GUI に出て、値変更が次フレームの描画に反映される。

### Phase 4（設計追加・大）: Layer セクション（L(name)ごと thickness/color）を GUI で編集できるようにする

- [ ] Layer の“発見”（どの Layer が存在するか）をどこで行うか決める
  - [ ] `render_scene`（normalize_scene 後）で current_param_store を見て “今フレームの layers” を記録する案
  - [ ] もしくは `L(...)` 生成時に callsite_id を使って記録する案
- [ ] Layer の識別子を決める（同名 layer が複数あっても衝突しない）
  - [ ] (layer_site_id, layer_name) の併用、または ordinal 付与
- [ ] GUI の Layer セクションへ thickness/color 行を出す
  - [ ] “コードが指定した値” と “GUI の上書き値” の優先順位（override の有無）を決める
- [ ] render へ反映
  - [ ] resolve_layer_style の前後どちらで上書きするか決め、責務を分離する
- [ ] テスト（最小）
  - [ ] Layer の識別・上書き適用の純粋部分を unit テストで担保

完了条件:
- L(name) ごとの thickness/color を GUI で調整でき、描画に反映される。

### Phase 5（仕上げ）: docs の整合

- [ ] `docs/parameter_gui_phase3_checklist.md` の `label(name=...)` 表記を現行 API（`G(name=...)`/`E(name=...)`）に合わせる
- [ ] “複数回 label 呼び”のルールを、実装とテストの実態に合わせて更新する（例外化するならここで明記）

## 事前確認したいポイント（決めないと実装が分岐する）

- 「複数回 label 指定」は例外にする？それとも現状どおり「最後勝ち」で良い？
- background_color は RGBA 1 行にする？（vec4 kind 追加が必要） それとも RGB+alpha に分ける？
- Layer の識別は name ベースに寄せる？それとも callsite_id ベースで安定識別する？

