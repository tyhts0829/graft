# ParamStore / EffectChain / Snapshot 安全性 改善計画 / 2025-12-22

## 背景（今回の指摘）

- EffectChainIndex の chain ordinal 採番が `len()+1` で、削除後に重複し得る。
- 「外部へミュータブル参照を渡さない」方針に対して、`get_state()` / `store_snapshot()` のコピーが shallow で、`ui_value` がミュータブルだと参照リークし得る。
- snapshot/contextvars の型が曖昧で、resolver に `# type: ignore` が残る。
- meta の無い state を永続化しており、仕様が曖昧（ゴミ state が残り得る）。

## 目的

- chain ordinal の **重複を起こさない**。
- 既に保存されている JSON に不整合があっても、ロード時に **修復して汚染を止める**。
- snapshot（meta あり）の `ui_value` を **不変（immutable）** に寄せ、shallow copy でも参照リークしない形にする（deep copy はしない）。
- snapshot と contextvars の型を固定し、`type: ignore` を消して読みやすくする。
- meta-less state の永続化方針を決めて、仕様として明文化する。

## 非目的

- GUI の表示仕様自体の大改造（例: effect#N のルール変更）。
- 実行時の常時検証の導入（テスト専用 `assert_invariants()` で検知する）。

## 事前確認（ここが決まらないと実装が分岐する）

1. **chain ordinal の方針**
   - [x] A) `record_step()` は **`max(existing)+1`** で採番（単純、重複しない。穴は許容）
   - [ ] B) `prune_unused_chains()` 後に **compact して 1..N** に詰め直す（穴を潰す。実装は少し増える）
   - 推奨: A
2. **`ui_value` の不変性**；推奨通り
   - [x] snapshot 対象（meta あり）は `ui_value` を kind に応じて正規化し、**常に immutable**（tuple/int/float/str/bool）
   - [ ] meta-less state の扱い:
     - [ ] (a) そもそも作らない（UI 経路を整理して禁止）
     - [x] (b) 作り得るが **永続化はしない**
     - [ ] (c) 作って永続化もする（理由が必要）
   - 推奨: snapshot 対象は strict、meta-less は (b)
3. **meta-less state の永続化仕様**；推奨通り
   - [x] 保存時に meta 無し state を drop する（次回保存で掃除される）
   - [ ] 保存時も残す（残す理由と掃除ポリシーが必要）
   - 推奨: drop
4. **型 alias の置き場所**；推奨通り
   - [x] `snapshot_ops.py` に `ParamSnapshot` 型 alias を置く（分散を避ける）
   - [ ] `types.py` を新設してまとめる
   - 推奨: `snapshot_ops.py`
5. **既存 JSON に chain ordinal の重複があった場合の修復方針（ロード時）**；推奨通り
   - [ ] A) 重複/不正値があるときだけ、chain_id を安定順（`(old_ordinal, chain_id)`）で並べて **1..N へ再採番**
   - [ ] B) 常に 1..N へ再採番（常時正規化。順序は安定だが、意図せず変わる可能性）
   - 推奨: A
6. **unknown kind の扱い（immutable 保証の抜け穴対策）**；推奨通り
   - [ ] A) unknown kind の `ui_value` は `str(value)` に正規化する（常に immutable。情報は落ち得る）
   - [ ] B) unknown kind のキーは snapshot 対象外にする（GUI から消えるが、安全）
   - 推奨: A

## 実装チェックリスト

### Step 1: Effect chain ordinal の重複防止

- [ ] `EffectChainIndex.record_step()` の新規採番を（上の方針に沿って）変更
- [ ] decode 時に chain_ordinals の重複/不正値を検出し、合意した方針で修復する（過去データの汚染源を止める）
- [ ] `assert_invariants()` に **chain ordinal 値の一意性**チェックを追加
- [ ] 「prune → 新 chain 追加」で重複しないことのテストを追加
- [ ] 「重複した chain_ordinals を含む JSON を load → 修復される」テストを追加

### Step 2: `ui_value` の canonicalize（immutable 化）

- [ ] **canonicalize を 1 箇所に閉じる**:
  - [ ] `canonicalize_ui_value(value, meta)` を新設（唯一の正規化ロジック）
  - [ ] decode/merge/ui_update は「同じ canonicalize」を呼ぶだけにする（ロジック重複を作らない）
- [ ] JSON decode 後に meta.kind を見て `ui_value` を canonicalize（vec/rgb は list -> tuple を必ず行う）
- [ ] merge/update の経路でも「store に入る `ui_value` は canonicalize 済み」を保証（= snapshot 時に困らない）
- [ ] unknown kind の扱いを合意し、canonicalize と invariants に反映する
- [ ] `assert_invariants()` に「snapshot 内の `ui_value` が list/dict ではない」チェックを追加

### Step 3: snapshot/contextvars の型付け

- [ ] `ParamSnapshotEntry`, `ParamSnapshot` を定義
- [ ] `context.py` の `ContextVar` と getter の戻り型を `ParamSnapshot` にする
- [ ] `resolver.py` の `# type: ignore` を削除できる状態にする
- [ ] （任意）labeling 等の引数型を `ParamSnapshot` に寄せる

### Step 4: 永続化仕様（meta-less state）

- [ ] encode 時に meta 無し state を drop（合意済み）
- [ ] decode 時にも「meta が無い state」を残す/捨てる方針を短く明記（推奨: 捨てる）
- [ ] `codec.py` に「残す/捨てる」仕様を短く明記
- [ ] テストを追加/更新（meta-less が保存されない、など）

### Step 5: 検証

- [ ] `PYTHONPATH=src pytest -q tests/core/parameters tests/interactive/parameter_gui`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`

## 追加提案（実装中に必要なら追記）

- [ ] （空欄。必要が出たら追記する）
