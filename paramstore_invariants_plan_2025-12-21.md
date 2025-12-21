# ParamStore 不変条件 + API 純度 改善計画 / 2025-12-21

## 結論（どちらが筋が良いか）

「**Store はデータで、書き込みは ops 経由のみ**」の方が筋が良い。

- 不変条件（整合性の知識）を 1 箇所へ寄せられる。
- “どこからでも触れる” を許すと、知識が分散して漏れ・重複・順序バグが増えやすい。
- Python なので厳密な強制は難しいが、**正規ルートを定義して、踏み抜きを起こしにくい形**にできる。

## 前提（合意したい最低条件）

- “書き込み”を広めに定義する:
  - `state/meta/labels/ordinals/effects/runtime` の更新はすべて write（= ops 外で触ると不変条件が漏れる）。
  - dict の直アクセスだけでなく、`labels/ordinals/effects/runtime` 自体が public だと不変条件を踏み抜ける（= 封鎖が弱い）。
  - `ParamState` がミュータブルで参照を渡す限り、`get_state()` は “外部 write 経路” になり得る（= 方針決めが必要）。
- snapshot 系は純粋（read-only）に寄せる:
  - “読むつもりが内部状態を更新する” API は残さない（必要なら別名/別責務へ分離）。
- ops の責務境界（誰が何を保証するか）を先に固定する:
  - 分割はその後（先に割ると知識が散る事故が起きやすい）。

## 目的

- 「どこが書いていいか」を決め、**整合性の知識の所在**を固定する。
- `snapshot` の副作用（読むつもりが書く）を解消/明示し、**API の純度**を上げる。
- `store_ops` の God-module 化を防ぐ（関心ごとで分割し、変更理由（Why）でファイルが変わる形へ）。

## 方針（提案）

### 1) 書き込みルートを限定する

- **唯一の書き込みルート**: `src/grafix/core/parameters/*_ops.py`（または `ops/` 配下）だけが `ParamStore` を更新する。
- 呼び出し側（API/GUI/runner）は **ops 関数を呼ぶだけ**にする。
- “write” は「dict をいじる」だけでなく「既存オブジェクトの中身を更新」も含む（例: `ParamState.override` 変更）。

### 2) ParamStore は「外から直書きしづらい」形に寄せる

- `store.states/meta/explicit_by_key/labels/ordinals/effects/runtime` のような “直書きし放題” をやめる。
- 代替案（軽い順）:
  1. 属性を `_states` のように private にし、読み取り accessor だけ公開（破壊的変更）。
     - private 化対象は dict だけでなく、`labels/ordinals/effects/runtime` も同列に扱う。
  2. 直書きは残すが「ops 経由以外は未定義」と明記し、テストでのみ検知（弱い）。

※ 過度に防御的にはしない（実行時にガチガチ検査・複雑なラッパは作らない）。

### 2.1) `ParamState` のミュータビリティ（唯一の write 経路を崩しやすい点）

dict を隠しても `get_state()` がミュータブル参照を返す限り、外部が state を直接 mutate できる。

- 推奨: “読む” API は **コピー/ビューのみ返す**（外部が直接 mutate できない）
  - 呼び出し側は基本 snapshot（pure）経由で読む。ピンポイント read も view/copy を返す。
  - 更新は ops のみが内部状態を mutate/replace できる。
- 代替: “禁止は規約 + テストで検知” に寄せる
  - ただし参照を握って mutate は検知が難しく、運用が曖昧になりやすい。

### 3) snapshot は “副作用あり/なし” を明確にする

現状: `store_snapshot()` が ordinal を採番しており、読む操作が書く操作になっている。

- 目標: snapshot は **pure snapshot**（副作用なし）をデフォルトにする。
- ordinal 付与は以下のいずれかに寄せる:
  - `merge_frame_params`（観測したグループは必ず採番）
  - `loads_param_store`（ロード直後に不足分を補完）
  - `reconcile/migrate`（移設時の付け替え）

## ops の責務境界（先に固定する）

「どの不変条件を誰が握るか」を先に決めてから分割する。

- `merge_ops`:
  - 観測（FrameParamRecord）を store に統合する唯一のルート
  - 観測された (op, site_id) の ordinal を必ず確保する（snapshot が採番しない前提）
  - `explicit_by_key` の追従ポリシーを適用する
  - effect step 情報と runtime.observed_groups を更新する
- `reconcile_ops`:
  - runtime.loaded_groups と runtime.observed_groups の差分に対して migrate を適用する（削除はしない）
  - migrate 時に label/ordinal/状態/明示フラグの移設を行う
- `prune_ops`:
  - 「観測されなかった loaded_groups」を削除する唯一のルート（保存前）
  - 削除後の compact、unused chain の掃除を行う
- `snapshot_ops`:
  - pure（read-only）な snapshot / snapshot_for_gui のみを提供する
  - 「不足データの補完」はしない（= 補完は merge/load/reconcile/prune の責務）

## 不変条件（所在を固定したい知識）

最低限、次を「ops が守る」と決めたい（= 直書き禁止の根拠）。

- `states/meta/explicit_by_key` の key は `ParameterKey` に限る。
- GUI 対象は `meta` があるキーのみ（snapshot 仕様）。
- ordinal:
  - op ごとに 1..N の連番
  - **states/meta/effects に存在する全グループは ordinal を必ず持つ**（snapshot は採番しない前提）
  - prune 後に compact（相対順維持）
  - migrate は可能なら ordinal を付け替え
- reconcile:
  - loaded/observed 差分の再リンク（削除は prune のみ）
  - Style（global）は scope 外
- effect chain:
  - step_info が消えた chain_id は chain_ordinals から消す
- labels:
  - (op, site_id) 単位、MAX_LABEL_LENGTH で trim

## モジュール分割案（store_ops の解体）

- `snapshot_ops.py`: `snapshot(store)` / `snapshot_for_gui(store)`（pure）
- `merge_ops.py`: `merge_frame_params(store, records)`（観測 → 更新）
- `reconcile_ops.py`: `reconcile_loaded_groups_for_runtime(store)` / `migrate_group(...)`
- `prune_ops.py`: `prune_stale_loaded_groups(store)` / `prune_groups(store, groups)`
- `invariants.py`: `assert_invariants(store)`（テストから呼ぶ。通常実行では必須にしない）

## 実装チェックリスト

- [ ] “write” の定義と、唯一の書き込みルート（ops）の合意を短く明文化（ADR 相当）
- [ ] ops の責務境界（merge/reconcile/prune/snapshot が何を保証するか）を箇条書きで固定
- [ ] `ParamStore` を “直書きしづらい” 形に変更（private 化 + read-only accessor）
  - [ ] private 化対象に `labels/ordinals/effects/runtime` も含める
  - [ ] `ParamState` の参照リークを潰す方針を決めて反映（view/copy or 規約）
- [ ] snapshot 系を pure にする（ordinal 採番を merge/load 側へ移す、必要なら別名を用意）
- [ ] `store_ops.py` を責務境界に沿って分割（snapshot/merge/reconcile/prune）
- [ ] `assert_invariants(store)` を追加し、主要テストの末尾で呼ぶ（漏れ検知）
- [ ] 呼び出し側（context/persistence/gui/tests）を “ops だけ書く” 前提へ追従
- [ ] `PYTHONPATH=src pytest -q tests/core/parameters tests/interactive/parameter_gui`

## 事前確認（あなたの判断が必要）

1. 「**ops 経由のみ書き込み**」を _原則ではなく唯一の経路_ にして、`ParamStore` の内部 dict 直アクセスを破壊して良い？（private 化）；賛成（破壊的変更 OK ならやる価値あり）。ただし private 化は states/meta/explicit_by_key だけでなく、少なくとも labels/ordinals/effects/runtime も同列で扱う方が “唯一の write 経路” が守りやすいです。
2. snapshot は **pure を標準**にし、「補完する snapshot」は別名/別責務へ分離する方針で良い？；賛成
3. `assert_invariants` は **テスト専用**で入れる（本番で常時実行はしない）で良い？；賛成

## 追加確認（決めないと運用が曖昧になる）

4. `ParamState` は外部へミュータブル参照を渡さない方針（= 読み取りはコピー/ビュー、更新は ops のみ）で良い？；はい

## 完了定義

- “どこが書いてよいか” が明文化され、呼び出し側が ops 以外で store を触らない
- snapshot が pure で、呼び出し順による状態変化が起きない
- `store_ops` 分割後も不変条件が `assert_invariants` + テストで固定される
