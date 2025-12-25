# 任意の primitive/effect を「op 名引数」で呼び出す “組み込み風” API 追加案（2025-12-25）

## 結論（可能か？）

- 可能。
- すでに現状でも `getattr(G, name)(**params)` / `getattr(E, name)(**params)`（および `EffectBuilder` に対しても `getattr(builder, name)(**params)`）で任意名を呼べる。
- ただし「明示的な public API」を追加する場合、`getattr(...)` の薄いラッパにすると `caller_site_id` が API 内の固定箇所を指してしまい、GUI の ParamStore 行が衝突する（= 全呼び出しが同一 site_id 扱いになる）ため避ける。
- 代わりに、`G.<名前>(op="circle", **params)` / `E.<名前>(op="scale", **params)` のような **“組み込み primitive/effect っぽい” メソッド**を追加し、その中で `caller_site_id` を正しく取得して `Geometry.create(...)` / `EffectBuilder(...)` を組み立てるのが最小。

## 目的 / 背景

- 設定ファイルや GUI などから「文字列の op 名」で primitive/effect を選びたい。
- `getattr(...)` でも実現できるが、API として発見可能（補完/型/ドキュメント）にしたい。

## 追加する API（案）

### 命名候補（提案）

「G と E で同じ名前」に寄せると覚えやすい。

- 第一候補（短くて内部概念と一致）:
  - `G.op(op="circle", ...)` / `E.op(op="scale", ...)`（＋必要なら `EffectBuilder.op(...)`）
- 第二候補（動詞で意図が明確）:
  - `G.invoke(op="circle", ...)` / `E.invoke(op="scale", ...)`
- 例に近い（名詞・複数形）:
  - `G.primitives(op="circle", ...)` / `E.effects(op="scale", ...)`
- 代替:
  - `G.by_name(op="circle", ...)` / `E.by_name(op="scale", ...)`
  - `G.dispatch(op="circle", ...)` / `E.dispatch(op="scale", ...)`

### 使い方イメージ（例）

以下は名前を `op` とした場合の例（選んだ名前に置換する）。

- primitive:
  - `G.op(op="circle", r=10.0, center=(0, 0, 0)) -> Geometry`
  - `G("label").op(op="circle", ...)` も既存の label 仕様に従う（`_pending_label` を使う）。
- effect:
  - `E.op(op="scale", scale=(2, 2, 2)) -> EffectBuilder`
  - `E("label").op(op="scale", ...)` も既存の label 仕様に従う。
  - （任意）`E.op(op="scale", ...).op(op="rotate", ...)` のようにチェーン途中でも名前で追加できるよう `EffectBuilder.op(...)` を追加する。

## 実装方針（最小・単純）

### 方針 A（推奨）: API 層の薄いラッパ

- 追加するのは「呼び出し補助」だけで、DAG の新しい op（=新 primitive/effect 実装）は増やさない。
- 実装は `getattr(...)` の factory に “丸投げ” はしない（`caller_site_id` が壊れるため）。
- 代わりに、既存 factory の中身（`meta/defaults/resolve_api_params/set_api_label/Geometry.create`）を **ほぼ同じ手順で**実装する。
  - これにより、`caller_site_id` がユーザー呼び出し箇所を指し続け、GUI 側のキーが安定する。

### 方針 B（別案）: DAG レベルの「invoke」ノード（※要検討）

- `Geometry(op="invoke_primitive", args={"op":"circle","params":{...}})` のようなメタ primitive/effect を追加し、`realize` 時に内部で registry を引いて実行する方式。
- デメリットが大きい:
  - 内側の primitive/effect の `ParamMeta` が素直に UI に出ない（params が dict になりがち）。
  - wrapper op になるため GeometryId が内側 op と一致せずキャッシュが分断される（許容はできるが利点が薄い）。
- 「op 名を GUI パラメータで差し替えたい」など *DAG レベルでの動的ディスパッチ* が必要な場合のみ検討対象。

## 実施チェックリスト（承認後に実装）

- [ ] (1) 仕様確定:
  - [ ] 公開名（`op`/`invoke`/`primitives` など）を 1 つ選ぶ。
  - [ ] 引数名は `op` で固定するか（`name`/`primitive`/`effect` など別名にするか）。
  - [ ] effect は `E.<名前>(...)` だけで十分か、`EffectBuilder.<名前>(...)` も要るか。
- [ ] (2) `G.<名前>(op=..., **params)` を追加する。
  - 変更先: `src/grafix/api/primitives.py`
  - 実装要点:
    - `caller_site_id(skip=1)` で **ユーザー呼び出し箇所**の site_id を取る
    - `set_api_label(op=op, site_id=site_id, label=self._pending_label)`
    - `meta/defaults = primitive_registry.get_meta/get_defaults(op)`
    - `resolved = resolve_api_params(...)`
    - `return Geometry.create(op=op, params=resolved)`
- [ ] (3) `E.<名前>(op=..., **params)` を追加する。
  - 変更先: `src/grafix/api/effects.py`
  - 実装要点:
    - `caller_site_id(skip=1)` を step の site_id に使う
    - `return EffectBuilder(steps=((op, dict(params), site_id),), chain_id=site_id, label_name=self._pending_label)`
- [ ] (4) （必要なら）`EffectBuilder.<名前>(op=..., **params)` を追加する。
  - 変更先: `src/grafix/api/effects.py`
  - 実装要点:
    - `caller_site_id(skip=1)`
    - `new_steps = self.steps + ((op, dict(params), site_id),)`
    - `return EffectBuilder(steps=new_steps, chain_id=self.chain_id, label_name=self.label_name)`
- [ ] (5) スタブ更新:
  - `tools/gen_g_stubs.py` に `G.<名前>` / `E.<名前>` / （必要なら）`EffectBuilder.<名前>` を Protocol へ出力する処理を追加。
  - 型はまず `op: str, **params: Any` でよい（個別 param 型は動的なので諦める）。
  - `python -m tools.gen_g_stubs` で `src/grafix/api/__init__.pyi` を再生成。
- [ ] (6) 最小テスト追加:
  - `tests/api/test_dynamic_call.py`（新規）などで以下を確認:
    - `G.<名前>(op="line", ...)` が `Geometry(op="line")` を返す
    - `E.<名前>(op="scale", ...)(g)` が `Geometry(op="scale")` を返す
    - （追加するなら）`E.<名前>(op="scale", ...).<名前>(op="rotate", ...)` がチェーンできる
    - 未登録名は `AttributeError`
- [ ] (7) 最小検証:
  - `PYTHONPATH=src pytest -q tests/api/test_dynamic_call.py`
  - （任意）`ruff check ...` / `mypy ...` はリポ全体状況に合わせて判断

## 事前確認したいこと（あなたに質問）

1. 公開名はどれが良い？
   - 推し: `op` / `invoke` / `primitives`（他でも可）
2. 引数名は `op` で固定して良い？（`name` が良ければそれでも）
3. effect 側は `EffectBuilder.<名前>(...)` も必要？（`E.<名前>(...)` だけで足りる？）
