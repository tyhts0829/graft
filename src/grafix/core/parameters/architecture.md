<!--
どこで: `src/grafix/core/parameters/architecture.md`。
何を: `grafix.core.parameters` 配下モジュール群のアーキテクチャを説明する。
なぜ: 責務境界・データフロー・不変条件を共有し、変更時の踏み抜きを減らすため。
-->

# grafix.core.parameters アーキテクチャ

## TL;DR

- **Store はデータ**（永続データの核）。書き込みは **`*_ops.py` 経由**を原則とする。
- **snapshot は pure**（読むつもりが書く、を排除）。不足補完は merge/load 側の責務。
- 1 フレームの値解決は `parameter_context()` が固定した snapshot に基づき **決定的**に行う。
- 永続化（JSON）は `codec.py` に閉じる。ロード時に **修復・正規化**を行い、汚染を止める。

---

## 基本概念（何を扱っているか）

### Key / Group

- `ParameterKey`（`key.py`）: `(op, site_id, arg)` で GUI 行を一意に識別する。
- Group: `(op, site_id)` を 1 グループとして扱う（GUI ヘッダ単位）。
- `site_id` は基本的に呼び出し箇所由来（`make_site_id()`）で、コード変更で揺れ得る。

### Meta / State

- `ParamMeta`（`meta.py`）: GUI 表示/検証用メタ（`kind`, `ui_min/ui_max`, `choices`）。
- `ParamState`（`state.py`）: GUI 状態（`override`, `ui_value`, `cc_key`）。
  - `override=True` のとき GUI 値を採用し、`False` のときコードが与えた base を採用する（bool など例外あり）。

### Store

- `ParamStore`（`store.py`）: 永続データの入れ物。
  - `_states/_meta/_explicit_by_key`（key 単位）
  - `_labels/_ordinals/_effects`（group 単位）
  - `_runtime`（永続化しない実行時情報）
- `get_state()` は **コピーを返す**（外部へミュータブル参照を渡さない）。

---

## レイヤ構造（どこに何を書くべきか）

### 1) データ構造レイヤ（薄いクラス/型）

- `key.py`: `ParameterKey` と site_id 生成。
- `meta.py`: `ParamMeta`。
- `state.py`: `ParamState`。
- `labels.py`: `ParamLabels`（(op, site_id) -> label）。
- `ordinals.py`: `GroupOrdinals`（group の安定順 ordinal）。
- `effects.py`: `EffectChainIndex`（effect chain の step 情報と chain ordinal）。
- `runtime.py`: `ParamStoreRuntime`（loaded/observed/reconcile-applied）。
- `store.py`: `ParamStore`（永続データの核）。

この層は「データの表現」を担い、運用ロジック（reconcile/prune/永続化/採番方針など）は持たない。

### 2) pure 関数レイヤ（副作用なし）

- `view.py`
  - `normalize_input()`（UI 入力の正規化）
  - `canonicalize_ui_value()`（`ui_value` を immutable へ正規化。unknown kind は `str(value)`）
  - `rows_from_snapshot()`（snapshot -> GUI 行モデル）
- `reconcile.py`: group の fingerprint 化とマッチング（誤マッチを避けるアルゴリズム）。
- `snapshot_ops.py`: `store_snapshot()` / `store_snapshot_for_gui()`（副作用なし）

### 3) ops レイヤ（Store の唯一の書き込みルート）

原則として「store を mutate するなら ops に置く」。

- `merge_ops.py`: Frame で観測した `FrameParamRecord` を store に統合（観測→保存）。
  - group ordinal を確保
  - meta/state の初期化
  - effect step の記録
  - reconcile と override-follow-policy の適用
- `ui_ops.py`: UI 入力を state に反映（`normalize_input` + `canonicalize_ui_value`）。
- `labels_ops.py`: label 更新。
- `meta_ops.py`: meta 更新。
- `style_ops.py`: style エントリ生成（`__style__/__global__`）。
- `reconcile_ops.py`: loaded/observed の差分を再リンク（削除はしない）。
- `prune_ops.py`: 保存前の掃除（観測されなかった loaded group を削除、compact）。

### 4) 制御/永続化レイヤ

- `context.py`: フレーム境界を作る（snapshot と buffer を contextvars に固定）。
- `resolver.py`: base/GUI/CC から effective 値を決定し、Frame の観測ログ（record）を作る。
- `codec.py`: JSON encode/decode（スキーマ仕様の置き場）。
- `persistence.py`: ファイル入出力 + 保存前 prune。
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
3. context 終了時（finally）に
   - `merge_frame_labels(store, frame_params.labels)`
   - `merge_frame_params(store, frame_params.records)`（store へ保存）

重要: draw の途中で GUI が動いても、そのフレームの解決は「開始時点の snapshot」で固定される。

### 2) GUI 更新（パラメータ操作）

- 表示:
  - `store_snapshot_for_gui(store)` → `rows_from_snapshot(...)`
  - loaded/observed の差分がある場合、GUI 用 snapshot は stale group を隠す（増殖防止）。
- 更新:
  - `update_state_from_ui(store, key, ui_input_value, meta=..., override=..., cc_key=...)`
  - ラベルは `labels_ops.set_label()`。

### 3) 永続化（JSON）

- save:
  - `save_param_store()` が保存前に `prune_stale_loaded_groups(store)` を実行して掃除する。
  - `encode_param_store()` は **meta を持たない state を drop** して永続化しない。
- load:
  - `decode_param_store()` は
    - meta-less state を drop
    - meta.kind に従って `ui_value` を canonicalize（immutable）
    - effect chain ordinal の重複/不正値を検出したら修復（1..N 再採番）
    - snapshot が pure 前提で成立するよう ordinal を補完

---

## 不変条件（守りたい性質）

### write 経路

- store を更新する操作は **ops に集約**する（知識が散る事故を防ぐ）。
- `get_state()` の返り値を mutate しても store は変わらない（コピーなので）。

### snapshot の純度

- snapshot は副作用なし（採番や補完をしない）。
- snapshot 対象（meta あり）の group は **必ず ordinal を持つ**（不足は merge/load が補完）。

### `ui_value` の不変性

- snapshot 対象（meta あり）の `ui_value` は `canonicalize_ui_value()` で **immutable**（tuple/int/float/str/bool）に正規化される。
- unknown kind は `str(value)` に落とす（情報は落ち得るが参照リークを防ぐ）。

### effect chain ordinal

- `record_step()` は `max(existing)+1` で採番し、重複を避ける（穴は許容）。
- 既存 JSON の重複/不正値はロード時に修復して汚染源を止める。

---

## 変更ガイド（どこを触るべきか）

### kind を追加/変更したい

1. `view.normalize_input()` と `view.canonicalize_ui_value()` に正規化ルールを追加
2. 必要なら `resolver._choose_value()` に CC/GUI/base の選択ルールを追加
3. 追加した kind の roundtrip / snapshot 不変性のテストを追加

### Store を更新したい

- `store.py` にメソッドを増やすより、まず `*_ops.py` に手続きを追加する。
- snapshot の挙動を変えたい場合は `snapshot_ops.py`（pure のまま）で設計する。

---

## よくある落とし穴

- `store.get_state(...).ui_value = ...` は **無効**（コピーを更新しているだけ）。更新は `ui_ops.update_state_from_ui()` を使う。
- `store_snapshot()` は **採番しない**。ordinal が無いと例外になるので、観測/ロード側で確保する。
- meta-less state を残すと永続化が汚れやすい。GUI 対象外の state は原則 drop（仕様として固定）。
