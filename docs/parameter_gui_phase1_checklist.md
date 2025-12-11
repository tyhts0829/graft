# どこで: docs/parameter_gui_phase1_checklist.md
# 何を: ParamStore が ParamMeta を保持・永続化するための実装計画。
# なぜ: GUI を再起動後もメタ情報込みで即座に生成できるようにするため。

## 方針
- ParamState は override/ui_value/cc だけを保持し、ui_min/ui_max は削除する（破壊的変更でクリーン化）。
- ParamMeta を ParamStore 側で key ごとに保持し、kind/choices/ui_min/ui_max の権威を一元化する。
- 互換は捨て、API をシンプルに再設計する。GUI 用には (key, meta, state, ordinal) を返す唯一の参照 API を持つ。

## チェックリスト（具体的な変更対象・内容）
- [ ] 現状確認  
  - 対象: `src/parameters/store.py`（`snapshot/merge_frame_params/to_json/from_json`）、`src/parameters/context.py`（`parameter_context`）、`tests/parameters/test_resolver.py` など snapshot 依存部。  
  - 内容: meta が落ちる経路を行番号付きでメモし、後続修正の抜け漏れ防止リストを作成。
- [ ] データ構造  
  - 対象: `ParamStore` クラス（`src/parameters/store.py`）。  
  - 内容: `self._meta: Dict[ParameterKey, ParamMeta]` を追加し、`__init__`/型アノテーションを更新。返却用の専用クラスは作らず、タプルか辞書で返す方針。
- [ ] マージ処理  
  - 対象: `ParamStore.merge_frame_params`。  
  - 内容: `rec.meta` を `_meta[key]` に保存するよう変更。`ParamState` への ui_min/ui_max 複製を削除し、override/ui_value/cc のみ維持。
- [ ] 参照 API（破壊的置換）  
  - 対象: `ParamStore.snapshot` のシグネチャと戻り値を置換し、(key, meta, state, ordinal) を返す唯一の API とする（名称は `snapshot` のまま）。戻り値型は「タプルの list」か「key を値にした dict」に統一（新クラスは作らない）。  
  - 対象呼び出し元: `src/parameters/context.py`（`parameter_context` がセットする値）、`src/parameters/resolver.py`（snapshot の読み取り部）、今後追加する GUI 層。  
  - 内容: contextvar に渡すスナップショット型を新仕様に合わせて全面更新し、旧仕様の利用箇所はすべて置換する。
- [ ] 永続化  
  - 対象: `ParamStore.to_json/from_json`。  
  - 内容: `_meta` を `{op, site_id, arg, kind, ui_min, ui_max, choices}` で保存・復元する。`ParamState` から ui_min/ui_max を削除したことを docstring/コメントで明示し、旧 JSON は「meta 欠落なら推定またはデフォルトで補う」等の扱いをコメントに記載（互換は不要でも挙動は明記）。
- [ ] テスト追加/更新  
  - 対象: `tests/parameters/test_resolver.py`、新規 `tests/parameters/test_store_meta.py`（仮）。  
  - 内容: meta を含む JSON round-trip、`descriptors()` の内容・順序（ordinal 単調増加）、context で新スナップショットが参照できること、旧 snapshot 型を前提にしたテストの差し替え。
- [ ] ドキュメント更新  
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ1節。  
  - 内容: ParamState から ui_min/ui_max を削除し、参照 API 置換の方針を反映。
- [ ] 確認ポイント CP1  
  - 対象: 上記変更一式後に手動確認してもらう項目を明記（例: JSON から meta を読み戻して GUI 行生成に使える、resolver が新スナップショットで動く）。
