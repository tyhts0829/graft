# parameter_gui_header_collapse_state_persistence_plan_2025-12-27.md

#

# どこで:

# - UI 入力: `src/grafix/interactive/parameter_gui/table.py`

# - 永続化: `src/grafix/core/parameters/codec.py` / `src/grafix/core/parameters/store.py`

# - 付随: reconcile/prune（`src/grafix/core/parameters/reconcile_ops.py` / `src/grafix/core/parameters/prune_ops.py`）

#

# 何を: Parameter GUI のヘッダ（Style / Primitive / Effect chain）の折りたたみ状態を ParamStore に永続化する実装計画。

# なぜ: セッションを跨いでも「作業中の見通し（開閉状態）」を維持し、反復調整の復帰コストを下げるため。

#

# 前提: `data/output/param_store/{script}.json`（= ParamStore）単位で永続化する（= スクリプトごと）。

## ゴール

- Parameter GUI の collapsing header open/close を **ParamStore JSON に保存**し、次回起動で復元する。
- 対象ヘッダ:
  - Style（global + layer をまとめた 1 ブロック）
  - Primitive（(op, site_id) グループ）
  - Effect chain（chain_id グループ）
- 既定は **open**（保存が無い/不明なら open）。

## 非ゴール

- ImGui 全体設定（ウィンドウ位置/サイズ等）を永続化する（= `imgui.ini` 方式）。
- 起動中に都度ディスクへ保存する（保存タイミングは現状どおり run 終了時）。
- header 表示/グルーピング仕様の刷新（あくまで状態の保存/復元のみ）。

## 仕様（採用）

### 永続化データ

- ParamStore に `ui` セクションを追加し、以下のみ保存する:
  - `ui.collapsed_headers: list[str]`
- 保存は「collapsed のみ」。
  - open は既定なので、リストに無ければ open とみなす（サイズを小さく保つ）。

### 永続キー（採用: 安定 ID / site_id ベース）

折りたたみ状態は **表示 ordinal に依存させない**（ordinal compaction 等で誤適用し得るため）。

- Style: `style:global`
- Primitive: `primitive:{op}:{site_id}`（例: `primitive:circle:c:1`）
- Effect chain: `effect_chain:{chain_id}`（例: `effect_chain:chain:1`）

### 更新ルール

- 描画時に復元:
  - `collapsed_headers` に含まれる → `set_next_item_open(False)`（collapsed）
  - 含まれない → `set_next_item_open(True)`（open）
- 描画後に同期:
  - `collapsing_header()` の返す `group_open` を見て、`collapsed_headers` を追加/削除して同期する
  - 変更検出が不要なら「毎フレーム同期」でも可（idempotent）

## 実装方針（提案）

### 1) ParamStore に UI 状態を持たせる（永続）

- `src/grafix/core/parameters/store.py`
  - `ParamStore` に `collapsed_headers: set[str]` を保持する（内部フィールドで可）
  - 直接参照でよいか、最小の getter/setter を用意するか決める

### 2) codec に `ui.collapsed_headers` を追加

- `src/grafix/core/parameters/codec.py`
  - `encode_param_store()`:
    - `ui: {"collapsed_headers": sorted(list(store.collapsed_headers))}` を追加
  - `decode_param_store()`:
    - `obj.get("ui", {})` から `collapsed_headers` を読み、`set[str]` にする
    - 型が壊れている場合は無視して空にする（現状の decode と同程度の寛容さ）

### 3) table で復元/更新する

`collapsing_header` を呼んでいるのは `src/grafix/interactive/parameter_gui/table.py`。

- 追加するヘルパ（案）:
  - `def _collapse_key_for_block(block: GroupBlock) -> str | None`
    - Style: `"style:global"`
    - Effect chain: `block.group_id` から `chain_id` を取り出して `"effect_chain:{chain_id}"`
    - Primitive: `block.items[0].row.op/site_id` から `"primitive:{op}:{site_id}"`
    - block.header が falsy の場合は `None`（そもそも折りたためない）
- `render_parameter_table()` に「状態の入れ物」を渡す:
  - 例: `collapsed_headers: set[str]`（参照を受け取り、その場で更新する）
  - `render_store_parameter_table()`（`store_bridge.py`）が `store` の set を渡す
- `imgui.set_next_item_open()` の pyimgui API を確認し、無ければ代替（`TREE_NODE_DEFAULT_OPEN` の扱い含む）を選ぶ

### 4) reconcile/prune への追従（reconcile は採用 / prune は任意）

#### reconcile（採用）

site_id 変更を吸収する reconcile が既にあるため、Primitive の折りたたみ状態も追従させると一貫する。

- `src/grafix/core/parameters/reconcile_ops.py:migrate_group()`
  - old: `primitive:{op}:{old_site_id}` が collapsed なら
  - new: `primitive:{op}:{new_site_id}` へ移す（コピー or 移動。移動でよい）

#### prune（任意）

削除したグループの collapse state は残っても実害は少ないが、ファイル肥大化を避けたいなら掃除する。

- `src/grafix/core/parameters/prune_ops.py:prune_groups()`
  - 削除対象 (op, site_id) から `primitive:{op}:{site_id}` を削除
  - effect chain は steps から逆引きが必要になるので、ここでは触らない（必要なら別途）

## テスト計画

- `tests/core/parameters/`
  - [ ] codec roundtrip: `ui.collapsed_headers` が保存/復元される
  - [ ] `migrate_group()` で Primitive collapse state が追従する（reconcile は採用）
  - [ ] `prune_groups()` で Primitive collapse state が掃除される（prune を入れる場合）
- `tests/interactive/parameter_gui/`
  - [ ] `_collapse_key_for_block()` の単体テスト（ImGui を起動せずに検証）

## 作業チェックリスト（実装順）

- [x] 永続キー仕様を確定（`primitive:{op}:{site_id}` / `effect_chain:{chain_id}` / `style:global`）
- [ ] ParamStore に `collapsed_headers` を追加（永続データ）
- [ ] codec に `ui.collapsed_headers` を追加（encode/decode）
- [ ] table に復元/更新を実装（pyimgui API 確認含む）
- [ ] reconcile 追従を実装（採用）
- [ ] prune 掃除を入れるか決めて実装（任意）
- [ ] テスト追加（core + interactive）
- [ ] `PYTHONPATH=src pytest -q tests/core/parameters tests/interactive/parameter_gui` を通す

## 決定事項 / 未決事項

- [x] 単位は「スクリプトごと（ParamStore ごと）」
- [x] Primitive は site_id ベースで永続化（ordinal は使わない）
- [x] reconcile に追従する（site_id 変更時も折りたたみ状態を維持）
- [ ] prune の掃除は必要？（残っても実害は小さいので任意）
