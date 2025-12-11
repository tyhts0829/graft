# どこで: docs/parameter_gui_phase3_labels.md
# 何を: label 機能を ParamStore ～ GUI に統合する実装計画。
# なぜ: primitive/effect から付与したラベルをヘッダ表示・永続化し、重複/長さ制約を明確にするため。

## 方針概要
- ラベルは ParamStore で永続化し、snapshot に含めて GUI へ渡す。
- primitive/effect チェーンのどこからでも `label(name=...)` を 1 回だけ呼べる。2 回目は例外。
- 同名が複数ある場合は GUI 側で `name#1`, `name#2` … のように連番を付与して表示。store には素の name を保存。
- name に長さ上限を設け、超過時はトリム（記録時点で実施）。空/未指定時はデフォルト（primitive: op 名、effect: effect#N）。

## 変更タスクリスト
- [ ] ParamStore にラベル辞書を追加し、to_json/from_json/snapshot へ組み込む。  
  - 例: `self._labels: Dict[Tuple[str, str], str]`（key: (op, site_id)）。  
  - snapshot 返却を `(meta, state, ordinal, label)` に拡張するか、別マップで同梱。
- [ ] primitive 側の label 受付  
  - 対象: `src/api/primitives.py` factory。  
  - `label: str | None = None` を受け取り、既存ラベルがあれば例外。長さ上限でトリム。ParamStore へ保存。
- [ ] effect チェーン側の label 受付  
  - 対象: `src/api/effects.py` / EffectBuilder。  
  - `label(name)` メソッド追加。チェーンで 1 回のみ許容、2 回目は例外。site_id と op（effect）で Store に登録。
- [ ] GUI 側のヘッダ生成で重複名に連番付与  
  - 対象: `src/parameters/view.py` または `parameter_gui.py` のヘッダ組み立て部。  
  - snapshot から素の name を受け取り、重複検出して `name#1` 形式に加工。未指定はデフォルト（primitive: op 名、effect: effect#ordinal）。
- [ ] 長さ・空文字処理  
  - name が空/None: デフォルト名をセット（上記）。  
  - 長さ上限（例: 64 文字）を超える場合はトリムし、警告ログ。  
  - 複数回 `label` 呼び出しはその場で例外を投げる。
- [ ] テスト追加/更新  
  - 対象: 新規 `tests/parameters/test_labels.py`（例: 重複時の連番付与、複数回 label 例外、永続化 round-trip）。  
  - GUI ヘッダ重複の表示ロジックをユニットで検証（view 側の重複解消関数）。
- [ ] ドキュメント更新  
  - 対象: `docs/parameter_gui_phase3_checklist.md` / `docs/parameter_gui_impl_plan.md` に label 処理の流れと制約（1 回のみ、長さ上限、連番付与、永続化）を反映。

## デフォルト表示ポリシー
- primitive: name 未指定 → op 名。  
- effect: name 未指定 → `effect#N`（N は ordinal を流用）。  
- 同名が複数存在 → `name#1`, `name#2`, … を GUI 側で付与。

## 例外ポリシー
- `label` の複数回呼び出し（同一 (op, site_id) で 2 回以上）: 例外。
- name が空/None は例外ではなくデフォルト名に置換。
- 文字数超過はトリム＋警告ログ（例外にはしない）。

