<!--
どこで: `src/grafix/core/parameters/architecture.md`。
何を: `grafix.core.parameters` 配下モジュール群のアーキテクチャを説明する。
なぜ: 責務境界・データフロー・不変条件を共有し、変更時の踏み抜きを減らすため。
-->

# grafix.core.parameters アーキテクチャ

## TL;DR

- **`ParamStore` は aggregate owner**。論理 state、revision/history、snapshot/favorite cache、
  transient rollback を一つの境界で確定する。
- **`*_ops.py` は validate/plan**。copy/frozen read port から完成済み replacement を作り、
  private mutation port へ一度だけ渡す。
- **snapshot は pure**（読むつもりが書く、を排除）。不足補完は merge/load 側の責務。
- `ParamStore.revision` が同じ間は snapshot と GUI 静的モデルを再構築しない。
- 1 フレームの値解決は `parameter_context()` が固定した snapshot に基づき **決定的**に行う。
- 永続化（JSON）は `codec.py` に閉じる。ロード時に **修復・正規化**を行い、汚染を止める。

---

## 基本概念（何を扱っているか）

### Key / Group

- `ParameterKey`（`key.py`）: `(op, site_id, arg)` で GUI 行を一意に識別する。
- Group: `(op, site_id)` を 1 グループとして扱う（GUI ヘッダ単位）。
- `site_id` は基本的に呼び出し箇所由来（`make_site_id()`）で、コード変更で揺れ得る。
  G/E/L/P の `key=` は同一ファイル内の明示 ID として instruction location を固定できる。

### Meta / State

- `ParamMeta`（`meta.py`）: GUI 表示/検証用メタ（`kind`, `ui_min/ui_max`, `choices`）。
  - `kind` は `bool/int/float/str/font/choice/vec3/rgb` の 8 種だけを受け付ける。
  - `choice` は空でない一意な文字列列を必須とし、それ以外の kind は `choices=None` とする。
- `ParamState`（`state.py`）: GUI 状態（`override`, `ui_value`, `cc_key`）。
  - MIDI が未採用なら全 kind で `override=True` のとき GUI 値、
    `False` のときコードが与えた base を採用する。

### Store

- `ParamStore`（`store.py`）: parameter aggregate の owner。
  - `_states/_meta/_explicit_by_key`（key 単位）
  - `_labels/_ordinals/_effects`（group 単位）
  - `_runtime`（永続化しない実行時情報）
  - logical state の commit、revision/history integration、snapshot/favorite cache invalidation
  - owner-bound transient rollback
- `get_state()` は **コピーを返す**（外部へミュータブル参照を渡さない）。
- session recovery が既存 object identity を維持する場合だけ、
  `replace_contents_from()` で別 `ParamStore` の全内容を transactional に置換する。

---

## レイヤ構造（どこに何を書くべきか）

### 1) value と aggregate owner

- `key.py`: `ParameterKey` と site_id 生成。
- `meta.py`: `ParamMeta`。
- `state.py`: `ParamState`。
- `collapsed_header.py`: `CollapsedHeaderKey` と ParamStore v4 の tagged record codec。
- `labels.py`: `ParamLabels`（(op, site_id) -> label）。
- `ordinals.py`: `GroupOrdinals`（group の安定順 ordinal）。
- `effects.py`: `EffectChainIndex`（effect chain の step 情報と chain ordinal）。
- `runtime.py`: `ParamStoreRuntime`（loaded/observed/reconcile-applied）。
- `store.py`: `ParamStore`、参照を漏らさない `_ParamStoreRead`、完成済み plan を確定する
  `_ParamStoreMutation`、`ParamStoreRollback`。

`key.py` などの value type はデータ表現だけを担う。一方 `ParamStore` は薄い container ではなく、
aggregate 内の参照 swap、revision/history、cache invalidation、transient rollback を所有する。
reconcile/prune の domain validation と plan は sibling ops、JSON codec は `codec.py` に残す。

### 2) pure 関数レイヤ（副作用なし）

- `view.py`
  - `normalize_input()`（kind ごとの canonical な UI 入力を検証）
  - `canonicalize_ui_value()`（検証済み `ui_value` を immutable へ正規化）
- `validation.py`: kind/meta/value/MIDI CC の共通 validator。
  - `rows_from_snapshot()`（snapshot -> GUI 行モデル）
- `reconcile.py`: group の fingerprint 化とマッチング（誤マッチを避けるアルゴリズム）。
- `snapshot_ops.py`: `store_snapshot()` / `store_snapshot_for_gui()`（副作用なし）

### 3) ops レイヤ（validate / plan）

domain command は `_ParamStoreRead` が返す copy/frozen value 上で validation、no-op 判定、allocation を
完了し、expected revision と完成済み replacement を `_ParamStoreMutation` へ渡す。mutation port は
revision の再確認、参照 swap、history/revision/cache の確定だけを行う。

mutation port へ渡した mutable replacement の ownership は commit 呼び出しで `ParamStore` へ移る。
呼び出し側は commit 後に同じ object を再利用・変更しない。

- `merge_ops.py`: Frame で観測した `FrameParamRecord` を store に統合（観測→保存）。
  - group ordinal を確保
  - meta/state の初期化
  - reconcile と override-follow-policy の適用
- `effect_order_ops.py`: `FrameEffectChainRecord` の完全 topology を store に統合し、
  GUI-owned order override と別に管理
- `ui_ops.py`: UI 入力を state に反映（`normalize_input` + `canonicalize_ui_value`）。
- `labels_ops.py`: label 更新。
- `meta_ops.py`: meta 更新。
- `style_ops.py`: style エントリ生成（`__style__/__global__`）。
- `reconcile_ops.py`: loaded/observed の差分を再リンク（削除はしない）。
- `prune_ops.py`: 明示的な掃除（指定 group や観測されなかった loaded group を削除）。

### 4) 制御/永続化レイヤ

- `context.py`: フレーム境界を作る（snapshot と buffer を contextvars に固定）。
- `resolver.py`: base/GUI/CC から effective 値を決定し、Frame の観測ログ（record）を作る。
- `codec.py`: JSON encode/decode（スキーマ仕様の置き場）。
- filesystem read/write、quarantine、recovery/finalize は core 外の
  `grafix.parameter_storage` が所有する。
- `invariants.py`: テスト専用の不変条件チェック（本番常時実行はしない）。

---

## データフロー（どう流れるか）

### 1) 1フレームの実行（draw 中）

1. `parameter_context(store, cc_snapshot)`（`context.py`）
   - `snapshot = store_snapshot(store)`（pure）
   - `frame_params = FrameParamsBuffer()` を作り、contextvars に固定
2. draw 内で `resolve_params(...)`（`resolver.py`）を呼ぶ
   - `snapshot` から既存 state/meta を参照
   - CC/GUI/base を統合して effective を決定（量子化もここで）
   - `FrameParamsBuffer.record(...)` へ `FrameParamRecord` を追加
3. draw が正常終了した context の終了時に
   - `merge_frame_effect_chains(store, frame_params.effect_chains, observation_complete=True)`
   - `merge_frame_labels(store, frame_params.labels)`
   - `merge_frame_params(store, frame_params.records)`（store へ保存）

重要: draw の途中で GUI が動いても、そのフレームの解決は「開始時点の snapshot」で固定される。
draw が例外で失敗した場合は、途中までの labels/records を破棄して
ParamStore には反映しない。

### 2) GUI 更新（パラメータ操作）

- 表示:
  - `store_snapshot_for_gui(store)` → `rows_from_snapshot(...)`
  - loaded/observed の差分がある場合、GUI 用 snapshot は stale group を隠す（増殖防止）。
- 更新:
  - `update_state_from_ui(store, key, ui_input_value, meta=..., override=..., cc_key=...)`
  - ラベルは `labels_ops.set_label()`。

### 3) 永続化（JSON）

- save:
  - `grafix.parameter_storage.write_param_store()` は、今回未観測の
    ロード済み group も保持する。
  - 不要 group を掃除するときは `prune_stale_loaded_groups(store)` または
    `prune_groups(store, groups)` を明示的に呼び出す。
  - `encode_param_store()` は **meta を持たない state を drop** して永続化しない。
- load:
  - `decode_param_store_result()` / `loads_param_store_result()` は
    - 現行 `schema_version` だけを受理し、旧・future・versionless は明示的に拒否
    - section ごとの typed parse で canonical value と破損診断を同時に生成
    - meta-less state を drop
    - meta.kind に従って `ui_value` を canonicalize（immutable）
    - effect chain ordinal の重複/不正値を検出したら修復（1..N 再採番）
    - snapshot が pure 前提で成立するよう ordinal を補完

---

## 不変条件（守りたい性質）

### write 経路

- sibling ops は validation/planning、`ParamStore` と private mutation port は commit と
  revision/history/cache 更新を所有する。
- mutation port へ渡した mutable plan は terminal ownership transfer とし、commit 後に caller が
  再利用・変更しない。
- `get_state()` の返り値を mutate しても store は変わらない（コピーなので）。

### snapshot の純度

- snapshot は副作用なし（採番や補完をしない）。
- snapshot 対象（meta あり）の group は **必ず ordinal を持つ**（不足は merge/load が補完）。

### `ui_value` の不変性

- snapshot 対象（meta あり）の `ui_value` は `canonicalize_ui_value()` で **immutable**（tuple/int/float/str/bool）に正規化される。
- 型違い、非有限値、選択肢にない `choice` は補正せず拒否し、UI 更新時は既存 state を変更しない。
- 保存済み `choice` が code reload 後に選択肢から外れた場合、GUI では unavailable として保持する。
  明示的に有効な選択肢を選ぶまで effective 値には採用しない。

### MIDI CC

- scalar CC は `float/int/choice`、3 要素 CC tuple は `vec3` だけが受け付ける。
- CC 番号は bool ではない厳密な int の `0..127` とする。
- `bool/str/font/rgb` と style/layer-style parameter には割り当てない。

### effect chain ordinal

- 完全な code topology を受け取る `record_chain()` が `max(existing)+1` で採番し、
  重複を避ける（穴は許容）。
- step index は topology と order override から導出し、parameter record には保持しない。
- 既存 JSON の重複/不正値はロード時に修復して汚染源を止める。

---

## 変更ガイド（どこを触るべきか）

### kind を追加/変更したい

1. `validation.py` の kind と meta/value/CC 契約を更新
2. `view.normalize_input()` と `view.canonicalize_ui_value()` の委譲を確認
3. 必要なら `resolver._choose_value()` に CC/GUI/base の選択ルールを追加
4. 追加した kind の roundtrip / snapshot 不変性のテストを追加

### Store を更新したい

- domain validation/planning はまず `*_ops.py` に置き、完成済み replacement を既存の
  `_ParamStoreMutation` commit へ渡す。新しい汎用 mutation API は追加しない。
- aggregate invariant、revision/history/cache の確定規則、owner-bound rollback を変える場合だけ
  `store.py` を変更する。
- snapshot の挙動を変えたい場合は `snapshot_ops.py`（pure のまま）で設計する。

---

## よくある落とし穴

- `store.get_state(...).ui_value = ...` は **無効**（コピーを更新しているだけ）。更新は `ui_ops.update_state_from_ui()` を使う。
- `_ParamStoreMutation.commit_*()` へ渡した dict/set/runtime object は store が所有する。commit 後に
  caller 側で mutate しない。
- `store_snapshot()` は **採番しない**。ordinal が無いと例外になるので、観測/ロード側で確保する。
- meta-less state を残すと永続化が汚れやすい。GUI 対象外の state は原則 drop（仕様として固定）。
