# plan_layer_style_unify_observation_and_prune.md

どこで: `src/render/frame_pipeline.py`（Layer style の観測）、`src/parameters/layer_style.py`（Layer style の key/meta ）、`src/parameters/store.py`（reconcile/hide/prune の対象範囲）、`tests/parameters/*`（回帰テスト）。
何を: Layer style（`__layer_style__`）を primitive/effect と同じ「FrameParamRecord を観測して ParamStore にマージ → reconcile/hide/prune → 永続化」の流れに統合し、Layer 行の増殖を止める。
なぜ: `Layer.site_id` がコード編集で揺れる前提でも、GUI/JSON に古い Layer 行が残らない（=複製しない）状態を、既存の reconcile/prune の枠組みで美しく実現するため。

---

## ゴール（完了条件）

- `main.py` を編集（空行追加/削除、引数追加など）して再起動しても、Style セクションの Layer 行が増殖しない。
- `data/output/param_store/*.json` に、今回の実行で観測されなかった `__layer_style__` グループが残らない。
- layer の名前（`L(name=...)`）がある場合は、site_id が揺れても可能な範囲で GUI 調整値が引き継がれる。
- layer の名前が無く曖昧な場合は、誤マッチせず初期化される（既存方針を維持）。

## 非ゴール

- `caller_site_id()` の安定化（f_lasti 依存排除）はこの対応では行わない。
- `__style__`（global style）は引き続き「常設キー」として扱い、observed-only には寄せない。

---

## 現状の問題（要点だけ）

- Layer style は `FrameParamRecord` 経由ではなく、`render_scene()` が `ensure_layer_style_entries()` で ParamStore を直接更新している。
- そのため ParamStore の `_observed_groups` に `__layer_style__` が載らず、primitive/effect の増殖対策（reconcile/hide/prune）の枠外にいる。
- 結果として `Layer.site_id` が揺れるたびに古い `__layer_style__` が残り続け、GUI 行/JSON が増殖する。

---

## 設計方針（primitive/effect と揃える）

### 1) Layer style も “観測” を `FrameParamRecord` に統一する

`render_scene()`（Layer を列挙できる唯一の場所）で、各 Layer について次の 2 レコードを `current_frame_params().record(...)` する:

- `key = ("__layer_style__", layer.site_id, "line_thickness")`
  - `base = resolved.thickness`
  - `meta = LAYER_STYLE_THICKNESS_META`
  - `explicit = (layer.thickness is not None)`
- `key = ("__layer_style__", layer.site_id, "line_color")`
  - `base = rgb01_to_rgb255(resolved.color)`
  - `meta = LAYER_STYLE_COLOR_META`
  - `explicit = (layer.color is not None)`

これにより `ParamStore.store_frame_params()` の既存ロジックで:

- `_observed_groups` が更新される（= stale 判定できる）
- `explicit` 追従 / override 既定値が一貫する（explicit=True なら override 既定 False、implicit なら True）

### 2) `__layer_style__` を reconcile/hide/prune の対象に含める

`ParamStore` の以下の処理で、除外する op から `LAYER_STYLE_OP` を外す:

- `_reconcile_loaded_groups_for_runtime()`（再リンク候補の生成）
- `snapshot_for_gui()`（stale の非表示）
- `prune_stale_loaded_groups()`（保存前の stale 削除）

除外し続けるのは `STYLE_OP="__style__"` のみ。

### 3) 役割の整理（Layer style の “キー生成” を 1 箇所に寄せる）

- `ensure_layer_style_entries()` は「直接 store を触る」よりも、
  - `FrameParamRecord` を生成する純粋ヘルパ（例: `layer_style_records(...) -> list[FrameParamRecord]`）
  へ寄せる（呼び出し元は `render_scene()`）。
- `store.set_label("__layer_style__", layer.site_id, layer.name)` は `L(...)` 側に残す（現状どおり）。

---

## 実装チェックリスト（OK をもらったらここから着手）

- [ ] `src/render/frame_pipeline.py`：Layer style の観測を `FrameParamRecord` に統一する
  - [ ] `current_frame_params()` を使い、`line_thickness/line_color` の 2 レコードを記録する
  - [ ] `ensure_layer_style_entries()` の直接呼び出しを削除する（責務の二重化をなくす）
- [ ] `src/parameters/layer_style.py`：record 生成ヘルパへ寄せる（必要なら）
  - [ ] `layer_style_key()` と meta 定義は維持
  - [ ] （任意）`layer_style_records(...) -> list[FrameParamRecord]` を追加して `render_scene()` を簡潔にする
- [ ] `src/parameters/store.py`：`__layer_style__` を reconcile/hide/prune 対象に含める
  - [ ] 除外 op を `STYLE_OP` のみにする（`LAYER_STYLE_OP` を除外しない）
- [ ] テスト追加（回帰）
  - [ ] `tests/parameters/test_param_store_reconcile.py` に「layer_style の op/site_id 揺れでも増殖しない」テストを追加
    - [ ] 1回目: `__layer_style__` をロードした store を作る
    - [ ] 2回目: 別 site_id の `__layer_style__` を観測し、`snapshot_for_gui()` に旧 site_id が残らない
    - [ ] `prune_stale_loaded_groups()` 後に `snapshot()` から旧 site_id が消える
- [ ] 手動スモーク（`main.py`）
  - [ ] `L(...)` の周辺の空行/引数を編集 → 再起動しても Layer 行が増殖しない

---

## 事前確認したいこと（YES/NO）

- layer_style も primitive/effect と同じく「この実行で観測されなかったロード済みグループは保存時に削除」で OK？
  - 条件分岐で一時的に layer が出ない場合、その run で layer_style は消える（observed-only の一貫性）。

