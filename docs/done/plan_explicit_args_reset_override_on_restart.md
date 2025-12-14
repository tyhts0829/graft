# plan_explicit_args_reset_override_on_restart.md

どこで: `src/parameters/store.py`（永続化 JSON / ParamStore 復元）と `src/parameters/persistence.py`（保存呼び出し）。
何を: 明示 kwargs（explicit=True）の `override` を「セッション内だけの操作」とみなし、再起動時は必ず `override=False`（= draw 関数の引数が反映される状態）から開始する。
なぜ: `main.py` で `type_index=1` のようにコードが明示した値が、前回 GUI で override=True にした履歴に負けると「コードを編集したのに反映されない」状態になり、編集体験が崩れるため。

---

## 期待する挙動（要件）

- `G.polyhedron(type_index=1)` のように **draw 関数で明示した引数**は、起動直後は常に `override=False` で開始する。
- 実行中に GUI で `override=True` に切り替えることは可能（そのセッション中は反映されてよい）。
- ただし **一度閉じて再実行したら**、明示引数は `override=False` に戻る（前回の override=True を引き継がない）。

## 非ゴール

- 明示引数の `ui_value` まで毎回リセットすること（必要なら別途検討）。
- 明示/省略の推定をソース解析で行うこと（現状の `FrameParamRecord.explicit` を正とする）。

---

## 方針（最小）

### 明示引数の override は永続化しない

ParamStore はすでに `explicit_by_key`（前回観測した explicit）を JSON へ保存/復元できる。
これを使い、**保存する JSON では explicit=True のキーの `override` を常に False に書き出す**。

併せて、過去に保存済みの JSON に explicit=True かつ override=True が残っている可能性があるため、
**ロード直後にも explicit=True のキーは state.override を False に正規化する**。

これにより:

- 再起動直後（最初のフレーム）からコード指定が勝つ
- 前回セッションの override=True が次回へ持ち越されない

---

## 実装チェックリスト

- [x] `src/parameters/store.py` の永続化を更新
  - [x] `ParamStore.to_json()`:
    - [x] `states[].override` を出力するとき、`explicit_by_key[key] is True` なら常に `False` を出力する
    - [x] `explicit_by_key` が無い（unknown）場合は現状の override を出力する
  - [x] `ParamStore.from_json()`:
    - [x] `explicit` を復元した後、`explicit_by_key[key] is True` の state.override を `False` に正規化する
    - [x] 旧 JSON（explicit 無し）は unknown 扱いのまま（勝手に override を変えない）
- [x] pytest を追加
  - [x] explicit=True のキーで override=True を作り、`to_json → from_json` したら override=False になる
  - [x] explicit=False のキーは override がそのまま復元される
  - [x] explicit を削除すると（explicit=True → False）、override=True に戻れる
- [ ] 手動スモーク（`main.py`）
  - [ ] `G.polyhedron(type_index=1)` を入れた状態で起動 → `override=False` で開始する
  - [ ] GUI で `override=True` → 終了 → 再起動 → `override=False` に戻る

---

## 事前確認したいこと（YES/NO）

- 明示引数（explicit=True）について、`override` だけをリセットし、`ui_value` は保持で OK？（次回起動で override を ON にしたとき、前回の ui_value がすぐ使える）；はい。
