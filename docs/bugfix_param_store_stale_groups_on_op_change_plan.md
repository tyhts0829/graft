# bugfix_param_store_stale_groups_on_op_change_plan.md

どこで: `src/parameters/store.py`（`snapshot_for_gui()` / `prune_stale_loaded_groups()`）と `data/output/param_store/*.json`（永続化）。
何を: primitive を差し替えたときに、旧 primitive の GUI 行/JSON が残り続ける不具合の原因調査結果と修正計画をまとめる。
なぜ: `main.py` を編集しながら試行錯誤するワークフローで、GUI と永続化ファイルが「過去の残骸」で汚れて混乱するため。

## 1. 現象（報告内容の整理）

再現（ユーザー報告）:

1. `main.py` を起動して 1 回描画し、閉じる（`data/output/param_store/main.json` が保存される）
2. `main.py` の `draw()` 内で、例: `ply1 = G.polyhedron(...)` を `ply1 = G.grid(...)` / `G.sphere(...)` に差し替える
3. 再度起動すると、Parameter GUI に
   - 現行コードに存在しない primitive（例: polyhedron/grid）のヘッダ/行が残る
4. `data/output/param_store/main.json` にも旧グループが残り続ける

補足（現状確認）:

- 現在の `data/output/param_store/main.json` には `sphere` がある一方で `polyhedron` / `grid` も残っている。

## 2. 調査（コード上の根拠）

### 2.1 ParamStore は「ロード済みグループ」と「観測済みグループ」を持つ

- ロード直後のグループ集合: `ParamStore._loaded_groups`
  - `ParamStore.from_json()` 末尾で `states/meta` から復元して保持する。
- 実行中に観測したグループ集合: `ParamStore._observed_groups`
  - `ParamStore.store_frame_params()` がフレームごとに `(op, site_id)` を `add()` する。

ここでの “グループ” は `(op, site_id)`（= callsite 単位）で、GUI のヘッダ増殖や永続化の単位でもある。

### 2.2 stale を隠す/削除する条件が「fresh_ops に属する stale のみ」になっている

現行の `ParamStore.snapshot_for_gui()` / `ParamStore.prune_stale_loaded_groups()` は概ね次の方針:

- `loaded_targets = loaded_groups - {style/layer_style}`
- `observed_targets = observed_groups - {style/layer_style}`
- `fresh_ops = {op | (op,site_id) ∈ observed_targets - loaded_targets}`
- `stale = loaded_targets - observed_targets`
- stale のうち `op ∈ fresh_ops` のものだけを
  - GUI 表示から隠す（`snapshot_for_gui()`）
  - 保存時に削除する（`prune_stale_loaded_groups()`）

この `fresh_ops` 制約は、「同じ op の新旧グループが同居したとき（site_id がズレたとき）」にだけ
“古い方を捨てる”ための安全弁として機能している。

しかし、primitive を差し替えて **op 自体が変わる**と、この条件では取りこぼす。

例:

- 1 回目: `("polyhedron", old_site_id)` がロードされている
- 2 回目: `("sphere", new_site_id)` が観測され、`polyhedron` は観測されない

このとき `fresh_ops` は `"sphere"` しか含まず、stale の `"polyhedron"` グループは:

- `snapshot_for_gui()` で隠れない（GUI に残る）
- `prune_stale_loaded_groups()` で削除されない（JSON に残る）

さらに、primitive を丸ごと削除して **fresh が 0** になった場合は `fresh_ops == ∅` で early return し、
stale が一切掃除されない。

## 3. 修正方針（おすすめ: observed-only に寄せる）

### 方針 A（おすすめ / 実装が最小）

primitive/effect（style/layer_style は除外）の ParamStore について:

- GUI 表示は「この実行で観測されたグループ（observed）」だけに限定する
- 保存前 prune は「ロードされたが今回観測されなかったグループ（stale）」をすべて削除する

これにより、op 差し替え/削除でも旧グループが残らない。

トレードオフ:

- 条件分岐などで「今回は出なかったが次回また出る」primitive/effect の状態も、
  出なかった実行で消える（= observed されない限り保持しない）。
- ただし「出したいならその実行で観測させる」という単純規則になり、挙動は説明しやすい。

### 方針 B（保守的 / 今回は非推奨）

- `fresh_ops` 制約を維持したまま、op 変更を検出する “callsite 同一視” を追加する。
- site_id が `f_lasti` 由来で揺れる前提では正規化や別シグネチャが必要になり、今回の目的に対して大げさになりやすい。

## 4. 実装チェックリスト（OK をもらったらここから着手）

- [x] 原因の特定: stale 掃除が `fresh_ops` 依存で、op 差し替え/削除が漏れている
- [x] `ParamStore.snapshot_for_gui()` を修正（primitive/effect の stale は `op` 無関係に隠す）
- [x] `ParamStore.prune_stale_loaded_groups()` を修正（primitive/effect の stale は `op` 無関係に prune）
- [x] pytest 追加: 「op を差し替えたとき、旧グループが GUI に出ない/保存で消える」
  - [x] `polyhedron` をロード → 次フレームで `sphere` を観測 → `snapshot_for_gui()` に `polyhedron` が残らない
  - [x] 同条件で `prune_stale_loaded_groups()` 後、`snapshot()` に `polyhedron` が残らない
- [ ] 手動スモーク（`main.py`）
  - [ ] `ply1` を `polyhedron → sphere` に差し替え → 再起動後に `polyhedron` 行が出ない/JSON から消える
  - [ ] `ply1` を `sphere → grid` に差し替え → 再起動後に `sphere` 行が出ない/JSON から消える

## 5. 事前確認したいこと（YES/NO）

- primitive/effect で「この実行で観測されなかったロード済みグループ」を保存時に削除して OK？；はい。案 A でお願いします。
  - （条件分岐で一時的に出ないものも消える）
