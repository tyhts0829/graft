# どこで: docs/parameter_gui_phase3_label_namespace.md

# 何を: G/E に name 指定でサブ名前空間を返し、primitive/effect へラベルを付与する現行方針の計画。

# なぜ: `.label()` なしで Layer と同じ書式（G(name=...), E(name=...)）でラベルを付けられるようにするため。

## 方針

- `PrimitiveNamespace.__call__(name: str | None)` を追加し、name を保持するラッパ（サブ名前空間）を返す。
  - `G.circle(...)` は現行どおり（name=None）。
  - `G(name="foo").circle(...)` と書いたとき、最初の primitive 呼び出しで (op, site_id) に label="foo" を ParamStore へ記録する。
- `EffectNamespace.__call__(name: str | None)` を追加し、返す `EffectBuilder` にチェーン名を設定（旧 `.label()` メソッドは廃止）。
  - `E.scale(...)` は現行どおり（name=None）。
  - `E(name="chain1").scale(...).translate(...)` のチェーンヘッダに "chain1" を表示。
- ラベルは上書き可（最終値採用）。name が None/空ならデフォルト（primitive: op 名、effect: effect#N）。

## 変更タスク

- [ ] `src/api/primitives.py`
  - `PrimitiveNamespace.__call__(self, name: str | None = None) -> PrimitiveNamespace` を実装し、内部に name を保持。
  - factory 呼び出し時に name があれば ParamStore.set_label(op, site_id, name) を実行（上書き可）。
  - 既存 `label` キーワードは削除。
- [ ] `src/api/effects.py`
  - `EffectNamespace.__call__(self, name: str | None = None) -> EffectBuilder` を実装し、Builder に chain 名をセット。
  - `EffectBuilder` は steps を増やしても chain 名を保持し、最初の適用で ParamStore に label を保存。
  - `.label()` チェーン方式は廃止し、name=... 指定を唯一のラベル付与手段とする。
- [ ] ParamStore / snapshot
  - ラベル永続化は未実装。`set_label` 等を追加し、snapshot に label を含める。
- [ ] View / GUI ヘッダ
  - snapshot から label を取り、重複時は `name#1` 付与。name 未指定はデフォルト（primitive: op、effect: effect#ordinal）。
- [ ] テスト
  - `tests/parameters/test_label_namespace.py` を追加。
    - `G(name="foo").circle()` で label が保存される。
    - `E(name="chain").scale()` で chain ヘッダに label が保存される。
    - 上書き可（同じ site_id で 2 回 name 指定したら最後が残る）を確認。
    - name=None ではラベル未設定。
- [ ] ドキュメント更新
  - `docs/parameter_gui_impl_plan.md` / `docs/parameter_gui_phase3_checklist.md` に name 付きサブ名前空間の使い方を追記。

## 例外・警告ポリシー

- 同一 (op, site_id) への複数ラベル設定は上書き最終値採用。警告ログを検討（例: 「ラベルを上書きしました: old->new」）。
- name が空/None はデフォルト名に置換（例外なし）。
- 文字数超過はトリム（64 文字）＋警告ログ。
