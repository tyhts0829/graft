# plan_effect_chain_label_normalization.md

どこで: `src/app/parameter_gui/labeling.py`（effect チェーンヘッダ名の決定）。
何を: `effect#N` の採番を「無名チェーンだけで 1..K に正規化」し、名前付きチェーン（`E(name=...)`）が存在しても無名が常に `effect#1` から始まるようにする。
なぜ: `E(name=...)` があるだけで無名チェーンが `effect#2` になったり、軽微な編集/永続化の影響で `effect#3`…と増えていくのはユーザビリティ的に困るため。

---

## ゴール

- `eff1 = E(name="...")...` + `eff2 = E.scale()...` のとき、`eff2` が常に `effect#1` になる。
- 永続化（`data/output/param_store/*.json`）の有無や、無関係なコード編集で `effect#N` が増殖しない（少なくとも「無名チェーンが1本なら常に `effect#1`」を満たす）。

## 非ゴール

- chain_id 自体の安定化や、chain_id の migration（旧→新）を実装すること。
- 無名チェーンが複数ある場合の “どれが #1 か” を完全に永続固定すること（必要なら `E(name=...)` で明示する）。

---

## 実装チェックリスト

- [x] `src/app/parameter_gui/labeling.py` の `effect_chain_header_display_names_from_snapshot()` を修正
  - [x] `label` があるチェーンは採番母集団から除外する（`effect#N` の N に影響させない）
  - [x] `label` が無いチェーンだけを、表示順（チェーン順）に従って `effect#1..K` へ割り当てる
  - [x] 既存の `dedup_display_names_in_order()` は維持し、表示名衝突（同名 label 等）は表示専用で解消する
- [x] pytest を追加/更新
  - [x] 「label あり 1 本 + 無名 1 本」で無名が `effect#1` になる
  - [x] chain_ordinal がギャップ/巨大値でも、無名が 1 本なら表示が `effect#1` のまま
  - [ ] （任意）無名が複数のとき `effect#1/#2` が 1..K で振られる
- [ ] 手動スモーク（`main.py`）
  - [ ] 初回起動: `eff1` は明示名、`eff2` は `effect#1`
  - [ ] 終了→`ply1` に引数追加→再起動: `eff2` が `effect#1` のまま
