# param_store_reconcile_and_prune_plan.md

どこで: `src/parameters/persistence.py`（ロード/セーブ）と `src/parameters/store.py`（状態保持）、必要に応じて `src/parameters/reconcile.py`（新規・純粋関数）。
何を: ParamStore 復元時/保存時に「古いキーの増殖」を防ぎつつ、軽微なコード編集（引数追加、空行追加/削除、整形）でもパラメータ調整値を“再リンク”して引き継ぐ仕組みを入れる。
なぜ: `site_id` をどの方式にしても「編集で変わる」問題が残り、永続化すると GUI ヘッダ増殖 or 復元断絶が起きてユーザビリティが落ちるため。

---

## ゴール

- `main.py` の空行整理や `G.polyhedron(type_index=2)` の追加などで `site_id` が変わっても、次回起動時に
  - 旧エントリが残って増殖しない（古い方を捨てる）
  - 可能な限り“同じ呼び出し箇所”へ前回の GUI 値（override/ui_value/ui_min/ui_max/cc_key）を移す

---

## 基本方針（site_id を信仰しない）

- `site_id` は「実行時に得られる一時キー」であり、永続化の唯一キーとしては不十分と割り切る。
- 永続化の肝は `load → reconcile(再リンク) → prune(掃除) → save`。
- “誤マッチ”は UX 破壊なので、曖昧なら移さない（初期化の方がマシ）。

---

## 設計（最小・実用）

### 1) callsite（(op, site_id)）単位で “グループ” を扱う

- `ParameterKey = (op, site_id, arg)` なので、移行も `site_id` 単位で行う。
- 1 グループの特徴量（fingerprint）を作る:
  - `op`
  - `args`: その site_id に属する `arg` の集合
  - `kinds`: `arg -> meta.kind`
  - `label`（任意）: `G(name=...)` / `E(name=...)` / `L(name=...)` が付いていれば補助情報として使う（キーにはしない）

### 2) 「旧グループ」と「新グループ」を分ける

- run 開始直後（ロード直後）に `loaded_groups = {(op, site_id)}` をスナップして保持する。
- 実行中に観測された `observed_groups = {(op, site_id)}` を集める（primitive/effect を中心に）。
- 終了時に:
  - `stale = loaded_groups - observed_groups`（旧だけ存在）
  - `fresh = observed_groups - loaded_groups`（新だけ存在）
  - これが「site_id がズレて分裂した」候補。

### 3) 再リンク（reconcile）のマッチング規則

`fresh` の各グループに対して、同じ op の `stale` 候補から 1 つ選び、対応付ける（1:1）。

スコア例（単純で十分）:

- 必須条件: `op` が同じ
- 加点:
  - `label` が両方あり一致: +100
  - `arg` の一致数: +10 × 一致個数
  - `kind` の一致数（arg が一致している前提）: +5 × 一致個数
  - `arg` 集合が完全一致: +30
- タイブレーク（任意）:
  - 旧/新の “出現順” が近い（同一 op 内の first_seen index）

採用条件（誤マッチ防止）:

- スコアが閾値未満ならマッチしない（移さない）
- 同点が複数あるならマッチしない（移さない）

### 4) マッチしたら「値を移す」

対応 `(old_site_id -> new_site_id)` が決まったら、arg ごとに移す:

- 同じ `(op, arg)` が存在するものだけ対象
- `meta.kind` が一致するものだけ、以下をコピー:
  - `ParamState`: `override`, `ui_value`, `cc_key`
  - `ParamMeta`: `ui_min`, `ui_max`（choices は新を優先）
- 表示の安定性のため、可能なら ordinal も移す:
  - `store._ordinals[op]` の old site_id の ordinal を new site_id へ付け替える
  - effect chain についても同様に `chain_ordinals` を付け替え可能（優先度低）

### 5) prune（掃除）

- 再リンクが済んだら `stale` 側のグループを削除する（増殖の根本停止）。
- 削除対象:
  - `states/meta/labels/ordinals/effect_steps` の該当 site_id
  - 孤立した `chain_ordinals` も削る（使われていない chain_id）

---

## 実装タスク（チェックリスト）

- [x] `docs` にこの方針を反映（既存の site_id 議論ドキュメントも整理）
- [x] 新規: `src/parameters/reconcile.py` を追加（純粋関数で `mapping` を作る）
  - [x] `build_group_fingerprints(snapshot) -> dict[(op, site_id), Fingerprint]`
  - [x] `match_groups(stale, fresh, fingerprints) -> dict[old_group -> new_group]`
- [x] `ParamStore` 側に “グループ単位の操作” を最小 API として追加
  - [x] `group_keys(op, site_id)`（そのグループの ParameterKey 一覧）
  - [x] `migrate_group(old_group, new_group)`（state/meta/ordinal の付け替え）
  - [x] `prune_groups(groups_to_remove)`（不要グループ削除）
- [x] `src/parameters/persistence.py` に prune を組み込み（保存時に旧グループを掃除）
  - [x] load 直後に `loaded_groups` を保持（`ParamStore.from_json`）
  - [x] 実行中に `observed_groups` を収集（`ParamStore.store_frame_params`）
  - [x] `reconcile → prune → save`（`save_param_store` 保存前に prune）
- [x] GUI で増殖を見せない
  - [x] `ParamStore.snapshot_for_gui()` で stale を隠蔽し、GUI 側で使用する
- [x] pytest を追加（実際の caller_site_id 変化を含む形）
  - [x] `G.polyhedron()` と `G.polyhedron(type_index=2)` の間で site_id が変わっても値が移る/増殖しない
  - [x] 曖昧マッチ（同じ op の同型 callsite が複数）では“移さない”こと
  - [x] effect step / chain の stale が保存時に掃除されること
- [ ] 手動スモーク
  - [ ] `data/output/param_store/main.json` を残したまま（削除不要）編集 → 再起動で増殖しないこと

---

## 事前確認したいこと（YES/NO）

- 曖昧な場合は移行しない（=一部の値が初期化されても誤マッチより優先）で OK？はい
- reconcile/prune の適用範囲はまず primitive/effect だけで OK？（style/layer_style は後回しで OK？）；はい
