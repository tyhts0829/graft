# plan_parameter_gui_layer_ordinal_compaction.md

どこで: `src/parameters/store.py`（ordinal 管理と prune）、`src/app/parameter_gui/grouping.py`（行ラベルの `#{ordinal}`）、`data/output/param_store/*.json`（永続化）、`tests/parameters/test_param_store_reconcile.py`（回帰）。
何を: parameter_gui の Layer 行ラベルが `layer#3` のように飛び番になる問題を、ordinal の不変条件（常に 1..N の連番）を保つことで解消する。
なぜ: 現行の採番が `len(mapping)+1` であり、mapping が非連続だと「1 Layer しか無いのに #3」「新規が #2 で既存が #3 のように初出順が崩れる」を起こし得るため。

---

## 現象（今回）

- `main.py` を `parameter_gui=True` で実行したとき、Style セクションの Layer style 行が `layer#3 ...` と表示される（自然なのは `layer#1 ...`）。
- `data/output/param_store/main.json` の `ordinals["__layer_style__"]` が 1 件しか無いのに ordinal 値が 3 になっている。
  - 例: `{'.../main.py:19:174': 3}`

## 原因（実装からの結論）

1. `L(...)` の Layer 識別子は `caller_site_id()` 由来の `site_id`。
   - `site_id` は `f_lasti` を含むため、`main.py` の小さな編集でも別物になりやすい。
2. `ParamStore` は op 単位に `site_id -> ordinal` を永続化し、採番は `_assign_ordinal()` の `len(mapping)+1`。
3. site_id が変わると新グループとして追加され、古いグループは stale として prune で削除される。
4. ただし prune は stale の `site_id` エントリを消すだけで、残った ordinal 値を 1..N に正規化（詰め直し）しない。
   - その結果「古い 1,2 を消した後に 3 だけ残る」が発生し、単体 Layer でも `#3` 表示になる。
5. Layer style は（name を付けない限り）fingerprint が同一になりやすく、reconcile が曖昧としてマッチを拒否するため、旧 ordinal へ戻りにくい。

---

## 方針（最小・単純）

- `ParamStore._ordinals[op]` は常に **値が 1..len(mapping) の連番**である、という不変条件を導入する。
- その不変条件が崩れる経路（load と prune）で、**相対順序維持（旧 ordinal 昇順）だけ**を保証して 1..N へ compaction する。
- あいまい一致や推測での migrate 強化は行わない（誤マッチ回避の現行方針を維持）。

---

## 実装チェックリスト（OK をもらったら着手）

- [x] 1. 再現をテストで固定（まず failing を作る）
  - [x] `tests/parameters/test_param_store_reconcile.py` にテストを追加
    - [x] `from_json()` 入力として `ordinals["__layer_style__"] = {site_id: 3}` を含む payload を用意
    - [x] ロード直後に `store.get_ordinal("__layer_style__", site_id) == 1` になること
    - [x] 全 op に対して compaction が効くこと（例: `polyhedron` の `2,5` が `1,2` になる）
- [x] 2. `src/parameters/store.py` に ordinal compaction を実装
  - [x] private helper を追加（`_compact_ordinals_map_in_place` / `_compact_all_ordinals`）
    - [x] ソートキーを `(old_ordinal, site_id)` とし、1..N を再付与
  - [x] `from_json()` で `store._ordinals` を op ごとに compaction
  - [x] `prune_groups()` で更新した op の mapping を compaction
- [ ] 3. 手動スモーク
  - [ ] `python main.py` で `layer#1 ...` 表示になる
  - [ ] `data/output/param_store/main.json` の `__layer_style__` ordinal が 1 へ戻る

---

## 暫定回避策（実装前に今すぐ直したい場合）

- `data/output/param_store/main.json` の `ordinals["__layer_style__"]` を 1 に書き換える（または当該 json を削除してリセット）。
  - ※削除/上書きは破壊的なので、実行する場合は事前にバックアップ推奨。

---

## 事前確認したいこと（YES/NO）

- YES/NO: compaction を `__layer_style__` だけでなく、全 op の `ordinals` に適用してよい？；はい
  - 全 op へ適用すると表示の一貫性は上がる一方、番号が「詰まる」ことになる（ただし相対順序は維持）。
