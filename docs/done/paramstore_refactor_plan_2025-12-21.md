# ParamStore 分割リファクタ計画（God-object 解体） / 2025-12-21

## 背景（現状）

`src/grafix/core/parameters/store.py` の `ParamStore` が、永続データと周辺ロジックを同居させている。

- state/meta 管理
- label 管理
- ordinal（グループ安定化）の割当・圧縮
- effect chain 情報（chain_id/step_index）管理
- reconcile（loaded/observed の突合）と migrate
- prune
- JSON serialize/deserialize

結果として、変更が横に波及しやすく（reconcile/ordinal/label が絡む）、仕様がコード奥に埋もれて読み手が追いづらい。

## 目的（この改善で得たい状態）

- `ParamStore` を「永続データの入れ物（最小の辞書）」に寄せる
- ordinal/reconcile/永続化の仕様を “見える場所” に分離し、依存方向を単純化する
- 変更の影響範囲を狭め、テスト可能な単位（純粋関数/小さなオブジェクト）を増やす

## 非目的（今回やらない）

- UI/表示仕様の刷新（並び順や既定値の変更を意図して行わない）
- param 解決アルゴリズム自体の変更（`resolver.py` の責務拡張など）

## 目標アーキテクチャ案（責務分離）

### 1) `ParamStore`（永続データの核）

「何を保持するか」に専念し、「どう維持するか」は外に出す。

- 例: `states`, `meta`, `labels`, `explicit_by_key`, `ordinals`, `effect_steps`, `chain_ordinals`
- `ensure_state` / `set_meta` などの “単純な CRUD” は残す（副作用で ordinal 付与や reconcile をしない）

### 2) `ParamStoreCodec`（JSON 変換）

- `encode(store) -> dict[str, Any]`
- `decode(payload: str) -> ParamStore`
- `persistence.py` は I/O（path/load/save）に専念し、JSON の形は codec に閉じる

### 3) `GroupOrdinals`（ordinal 管理）

- `get_or_assign(op, site_id)` / `compact(op)` / `compact_all()`
- 「採番」「圧縮」「移設（migrate 時の付け替え）」をここに集約

### 4) `ParamStoreRuntime`（実行時の追跡・reconcile/prune の材料）

永続化しない一時データをまとめる。

- `loaded_groups`（ロード直後の (op, site_id)）
- `observed_groups`（今回実行で観測した (op, site_id)）
- `reconcile_applied`（同一ペアの二重適用防止）

### 5) `merge_frame_params` / `reconcile_groups` / `prune_*`（手続き）

- `merge_frame_params(store, runtime, frame_records)`:
  - meta/state の登録・更新、effect_steps の更新、observed_groups の更新
  - explicit/implicit 追従ポリシー適用（現状 `_apply_explicit_override_follow_policy` 相当）
  - 必要なら `reconcile_groups(...)` を呼ぶ（“削除”はしない）
- `prune_stale_loaded_groups(store, runtime)`:
  - 実行終了時に、今回観測されなかったロード済みグループを削除
  - 併せて ordinals/chain_ordinals の圧縮・掃除

## 仕様を明文化して固定する（テスト対象）

- **ordinal**
  - op ごとに 1..N の連番
  - prune 後は連番に圧縮（相対順序は維持）
  - reconcile の migrate 時は可能なら ordinal を付け替える（UI グループ安定化）
- **label**
  - (op, site_id) 単位
  - 長さは `MAX_LABEL_LENGTH` でトリム
- **explicit/override**
  - 明示 kwargs（explicit=True）は「起動時はコードが勝つ」期待値のため、override=True を永続化しない
  - explicit/implicit の切り替えが起きたとき、override が “既定値のまま” なら追従して切り替える
- **reconcile**
  - loaded と observed の差分を再リンク（削除はしない）
  - Style（global）は常設キーとして scope 外（現状維持）
- **prune**
  - 保存直前に “旧 site_id の残骸” を掃除して肥大化を防ぐ（現状維持）

## 実装チェックリスト（作業手順）

- [x] 影響範囲を洗い出す（`ParamStore` 利用箇所と依存メソッドの一覧化）
- [x] ふるまい固定用のテストを更新（主に ordinal/reconcile/prune/codec）
- [x] `codec` を追加し、`to_json/from_json` を撤去して呼び出し側を更新
- [x] `ordinals` を追加し、採番/圧縮ロジックを `ParamStore` から移動（呼び出し側も更新）
- [x] `runtime` を追加し、loaded/observed/reconcile_applied を `ParamStore` から分離
- [x] `merge_frame_params`（旧 `store_frame_params`）を外出しし、`parameter_context` から呼ぶ形に変更
- [x] `reconcile` / `migrate_group` を `ParamStore` から外へ移動
- [x] `prune_stale_loaded_groups` / `prune_groups` を外へ移動し、`save_param_store` から呼ぶ
- [x] `ParamStore` の公開 API を “最小の永続辞書” へ整理（不要メソッド削除・責務の明文化）
- [x] `PYTHONPATH=src pytest -q tests/core/parameters tests/interactive/parameter_gui`
- [ ] `ruff check .`（この環境では `ruff` コマンド未導入）
- [ ] `mypy src/grafix`（既存エラーがあるため現状はグリーン化していない）

## 影響が出る見込みのあるファイル（初期見立て）

- `src/grafix/core/parameters/store.py`（縮小）
- `src/grafix/core/parameters/persistence.py`（codec 利用へ）
- `src/grafix/core/parameters/context.py`（merge 関数呼び出しへ）
- `src/grafix/interactive/parameter_gui/store_bridge.py`（ordinal/effect 情報取得元の変更に追従）
- `src/grafix/core/parameters/style.py` / `view.py`（`ensure_state` 等が残るなら軽微）
- `tests/`（新規追加）

## 事前確認したいこと（あなたの判断が必要）

1. **データの永続化範囲**: `effect_steps` / `chain_ordinals` は現状どおり JSON に残す想定で良い？；はい
2. **分割の粒度**: label/effect も `ParamStore` から完全分離（別オブジェクト化）までやる？（やるほど変更量は増えるが、責務は明瞭になる）；はい
3. **破壊的変更許容**: `ParamStore` の公開メソッドは整理して消す方針で進めて良い？（互換ラッパーは作らない）；はい

## 完了定義

- `ParamStore` が「永続データの核」として読める（責務が 1 つに見える）
- ordinal/reconcile/codec/prune のコードがそれぞれ単独で読める（別モジュール/別オブジェクト）
- 主要仕様（ordinal 圧縮、explicit/override、reconcile/prune）がテストで固定される

## 実装メモ（実際の分割）

- 永続データの核: `src/grafix/core/parameters/store.py`
- label: `src/grafix/core/parameters/labels.py`
- ordinal: `src/grafix/core/parameters/ordinals.py`
- effect chain: `src/grafix/core/parameters/effects.py`
- runtime: `src/grafix/core/parameters/runtime.py`
- JSON codec: `src/grafix/core/parameters/codec.py`
- 手続き（snapshot/merge/reconcile/prune）: `src/grafix/core/parameters/store_ops.py`
