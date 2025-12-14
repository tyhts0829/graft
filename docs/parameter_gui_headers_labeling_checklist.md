# どこで: `docs/parameter_gui_headers_labeling_checklist.md`。

# 何を: `docs/parameter_gui_phase3_checklist.md` の「ヘッダ行・ラベリング（Style / Primitive / Effect チェーン / Layer）」を、素直に実装できる順に段階的に実装する計画。

# なぜ: 現状の実装・データモデルに合わせて“まずできるもの”から積み上げ、必要な設計追加（chain_id / style store / layer discovery）を後段で安全に入れるため。

## ゴール（最終形）

- Parameter GUI に以下の順で「ヘッダ行」を表示できる。
  1. Style（background/global_thickness/global_line_color）
  2. Layer（L(name) ごとに thickness/color）
  3. Primitive（`G(name=...)` の name をヘッダ行に表示）
  4. Effect チェーン（`E(name=...)` の name をヘッダ行に表示）
- 1 つの G / 1 つの Effect チェーンのパラメータが大量に並んでも「どこに紐づくか」見失わないこと。
- 行ラベル（テーブル 1 列目）は以下の形式で統一する。
  - Primitive の各パラメータ行: `"{op}#{ordinal} {arg}"`（例: `polygon#1 n_sides`）
  - Effect の各パラメータ行: `"{op}#{step_ordinal} {arg}"`（例: `scale#1 auto_center`）
    - `step_ordinal` は “同一チェーン内での同一 op の出現回数” で採番する
      （例: `E.scale().rotate().scale()` → `scale#1`, `rotate#1`, `scale#2`）
- 同名ヘッダ（例: `G(name="A")` が複数）は GUI 表示専用に `name#1`, `name#2` で衝突回避する（永続化ラベル自体は変えない）。

## 現状確認（すでに出来ている/出来ていない）

- ラベル永続化（出来ている）
  - `G(name=...)` / `E(name=...)` が `ParamStore.set_label(op, site_id, name)` に保存する。
  - `ParamStore.snapshot()` は各 ParameterKey に対応する `label` を返す。
- GUI のラベリング（部分的に出来ている）
  - Primitive は出来ている（ヘッダ行 + `polygon#1 n_sides` 形式 + snapshot.label 反映 + 表示専用の衝突解消）。
  - Effect チェーンは出来ていない（チェーン境界/順序が復元できず、チェーン名ヘッダやチェーン内採番が未実装）。
- Style / Layer セクション（出来ていない）
  - background/global thickness/global line_color は ParamStore とは別系統（run/settings/defaults）。
  - Layer の一覧（L(name)）は ParamStore に観測されていない。
- Effect「チェーン」単位のヘッダ（出来ていない）
  - ordinal は op ごとなので、チェーン境界やチェーン順序が復元できない。
  - チェーン ID（chain_id）の概念がまだ無い。
- 用語のズレ
  - チェックリスト本文は `label(name=...)` と書いているが、実装は `G(name=...)` / `E(name=...)` 方式。

## 実装順序（“素直にできる”→“設計追加が必要”）

### Phase 1（最小・素直）: Primitive のヘッダ行 + 行ラベルを `polygon#1 n_sides` 形式へ

- [x] Primitive グルーピング単位を確定する（`(op, site_id)` → `op#ordinal`）
- [x] テーブル描画側で「ヘッダ行」を挿入できるようにする
  - [x] `rows_from_snapshot()` は “パラメータ行だけ” を返し、ヘッダ行の挿入は GUI 層でやる（責務を分離）
  - [x] (op, ordinal) が変わったタイミングで 1 行だけヘッダ行を描画する
- [x] ヘッダ表示名を snapshot の `label` から解決する
  - [x] `G(name=...)` がある場合はそれを使う
  - [x] 無い場合のフォールバックを決める（例: `polygon#1`）；ない場合は primitive 名で。
  - [x] 同名衝突は表示専用に `name#1/#2` を付与（永続化ラベルは変更しない）
- [x] パラメータ行の 1 列目を `"{op}#{ordinal} {arg}"` に変更する（例: `polygon#1 n_sides`）
- [x] テスト（最小）
  - [x] 「ヘッダ名の衝突解消」と「行ラベル整形」を純粋関数に切り出してユニットテストする

完了条件:

- Primitive ごとに 1 行のヘッダ行が出て、直下の行ラベルが `polygon#1 n_sides` 形式になっている。
- 同名ヘッダ衝突時に `#1/#2` で区別できる（表示専用）。

### Phase 2（設計追加・中）: Effect チェーンのヘッダ行 + `scale#1 auto_center`（チェーン内採番）

- [x] チェーン境界と “ステップ順序” を復元できるデータを追加する
  - [x] `EffectBuilder` に `chain_id` を導入する（例: “builder 生成時の site_id” を chain_id にする）
  - [x] 観測レコード（FrameParamRecord）へ `chain_id` と `step_index`（steps 内の順序）を載せる
  - [x] ParamStore が (op, site_id) → (chain_id, step_index) を参照できるように保持/公開する
- [x] チェーンヘッダ表示名の解決
  - [x] `E(name=...)` がある場合はその name をチェーン名として表示する
  - [x] 無い場合は `effect#N`（N は chain ごとの ordinal）を表示する
  - [x] 同名衝突は表示専用に `name#1/#2` を付与する
- [x] チェーン内の “同一 op 連番” を計算する
  - [x] steps の順序に従い、op ごとにカウントして `scale#1/scale#2` を決める
  - [x] パラメータ行の 1 列目を `"{op}#{step_ordinal} {arg}"` にする（例: `scale#1 auto_center`）
- [x] GUI 側で「チェーンヘッダ → ステップ群」の順に並べて表示する
- [x] テスト（最小）
  - [x] `E(name="xf").scale().rotate().scale()(g)` で
    - チェーンヘッダが 1 回だけ出る
    - `scale#1`, `rotate#1`, `scale#2` の採番になる
    - 行ラベルが `"{op}#{n} {arg}"` になる

完了条件:

- Effect チェーンが GUI 上でまとまって見え、チェーン内採番（`scale#1`, `scale#2`）が安定している。

### Phase 3（設計追加・大）: Style ヘッダ（background/global_thickness/global_line_color）を GUI で編集できるようにする

- [x] Style を保持する場所を決める（ParamStore に統合するか、別ストアを作るか）
  - [x] まずは ParamStore へ統合する案（特殊 op/site_id/arg で表現）を第一候補にする
- [x] 型/kind を揃える
  - [x] background_color（RGB）をどう表現するか決める（vec3/ rgb kind 追加など） rgb kind 追加。control には color_edit3 を使う。
  - [x] global_line_color（RGB）をどう表現するか決める（vec3/ rgb kind 追加など） rgb kind 追加。control には color_edit3 を使う
- [x] run/render への反映経路を追加
  - [x] GUI 値で `renderer.clear(background_color)` を上書きできる
  - [x] GUI 値で LayerStyleDefaults（global_thickness/global_line_color）を上書きできる
- [x] テスト（最小）
  - [x] 依存が重いので unit テストは「Style の純粋部分」まで、描画は手動確認に寄せる

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
- Layer の識別は name ベースに寄せる？それとも callsite_id ベースで安定識別する？
