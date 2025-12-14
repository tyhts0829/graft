# plan_explicit_override_follow_policy.md

どこで: `src/parameters/store.py`（frame 観測のマージ/永続化/再リンク）と `src/parameters/resolver.py`（FrameParamRecord.explicit の記録）。
何を: 「明示 kwargs（explicit=True）」にした引数は GUI override を False に戻したい、という要求に対し、永続化された override を盲目的に優先せず、**explicit/implicit の変化に追従して override を条件付きで更新する**仕組みを追加する。
なぜ: `G.polyhedron(type_index=1)` のようにコードが意図して固定した値より、過去の GUI 調整（implicit 時代の override=True）が勝ってしまうと編集体験が崩れるため。

---

## ゴール

- 以前 implicit（省略）だった引数を explicit（明示）に変えたとき、GUI override が “ポリシー通り” False に戻る。
- ただしユーザーが override を明示的に操作したと推定できる場合は、勝手に上書きしない。
- reconcile（site_id 変化の再リンク）後でも、この追従が期待通り効く。

## 非ゴール

- 「常に explicit なら override=False」に強制すること（ユーザーが明示的に override を ON にした場合は尊重する）。
- 過去の古い JSON（explicit 情報なし）に対して完全自動に“正しい推定”をすること（不明な場合は安全側で何もしない）。

---

## 仕様（最小）

### A) 追加で記録する永続データ

- `explicit_by_key: dict[ParameterKey, bool]`（前回観測した explicit）
  - JSON には `{"op","site_id","arg","explicit"}` の配列で持つ
  - 旧 JSON（このフィールド無し）は “unknown” 扱いで移行しない

### B) override 追従ルール（曖昧なら触らない）

フレーム観測で `new_explicit` を得たとき:

- `prev_explicit` が無い場合: `explicit_by_key[key]=new_explicit` を保存するだけ（override は触らない）
- `prev_explicit == new_explicit`: 何もしない
- `prev_explicit != new_explicit` の場合のみ、次の条件で override を更新する:
  - `default_override(prev) = (not prev_explicit)`
  - `default_override(new) = (not new_explicit)`
  - **現在の `state.override` が `default_override(prev)` と一致する場合のみ**、`state.override = default_override(new)` に更新する
    - 一致しない場合は「ユーザーが override を自分で変えた」とみなして維持する

### C) reconcile（site_id 変化の再リンク）との整合

- `migrate_group(old_group -> new_group)` の中で、該当キーの `explicit_by_key` も新キーへ移す
  - これをしないと「旧は implicit で override=True」→「新は explicit」へ移ったときに、追従の判断材料が欠ける

---

## 実装チェックリスト

- [x] `src/parameters/store.py`
  - [x] `ParamStore` に `self._explicit_by_key: dict[ParameterKey, bool]` を追加
  - [x] `to_json()` に `explicit` を保存（配列）
  - [x] `from_json()` で `explicit` を復元（無ければ空のまま）
  - [x] `store_frame_params()` で explicit 変化を検知し、上記ルールで `state.override` を条件付き更新
  - [x] `migrate_group()` で `explicit_by_key` を old_key -> new_key へ移す（kind 一致のものだけ）
- [x] pytest を追加
  - [x] implicit→explicit で override が False に戻る
  - [x] site_id がズレて migrate が発生したケースでも、explicit 追従が効く
  - [x] 旧 JSON（explicit 無し）では override を勝手に変更しない
- [ ] 手動スモーク（`main.py`）
  - [ ] 1 回目: `type_index` を省略して GUI で override=True にする → 終了
  - [ ] 2 回目: `G.polyhedron(type_index=1)` に編集 → 起動時に override が False になっている
