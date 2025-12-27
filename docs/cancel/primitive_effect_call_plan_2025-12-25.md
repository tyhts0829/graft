# 任意の primitive/effect を「op 名引数」で呼び出す “組み込み風” API 追加案（2025-12-25）

## 結論（可能か？）

- 可能。
- すでに現状でも `getattr(G, name)(**params)` / `getattr(E, name)(**params)`（および `EffectBuilder` に対しても `getattr(builder, name)(**params)`）で任意名を呼べる。
- ただし「明示的な public API」を追加する場合、`getattr(...)` の薄いラッパにすると `caller_site_id` が API 内の固定箇所を指してしまい、GUI の ParamStore 行が衝突する（= 全呼び出しが同一 site_id 扱いになる）ため避ける。
- 代わりに、`G.op("circle", **params)` / `E.op("scale", **params)` のような **“組み込み primitive/effect っぽい” メソッド**を追加し、その中で `caller_site_id` を正しく取得して `Geometry.create(...)` / `EffectBuilder(...)` を組み立てるのが最小。

## 目的 / 背景

- 設定ファイルや GUI などから「文字列の op 名」で primitive/effect を選びたい。
- `getattr(...)` でも実現できるが、API として発見可能（補完/型/ドキュメント）にしたい。

## 追加する API（案）

### 命名（決定）

- `G.op(...)` / `E.op(...)` を採用する。

### 使い方イメージ（例）

- primitive:
  - `G.op("circle", r=10.0, center=(0, 0, 0)) -> Geometry`
  - `G("label").op("circle", ...)` も既存の label 仕様に従う（`_pending_label` を使う）。
- effect:
  - `E.op("scale", scale=(2, 2, 2)) -> EffectBuilder`
  - `E("label").op("scale", ...)` も既存の label 仕様に従う。
  - （任意）`E.op("scale", ...).op("rotate", ...)` のようにチェーン途中でも名前で追加できるよう `EffectBuilder.op(...)` を追加する。

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
- 「op 名を GUI パラメータで差し替えたい」など _DAG レベルでの動的ディスパッチ_ が必要な場合のみ検討対象。

## 実施チェックリスト（承認後に実装）

- [ ] (1) 仕様確定:
  - [x] 公開名は `op`。
  - [ ] `G.op("circle", ...)` / `E.op("scale", ...)` のように、op 名は **第 1 引数（positional）**で受ける。
  - [ ] effect は `E.op(...)` だけで十分か、`EffectBuilder.op(...)` も要るか。
- [ ] (2) `G.op(op_name, **params)` を追加する。
  - 変更先: `src/grafix/api/primitives.py`
  - 実装要点:
    - `caller_site_id(skip=1)` で **ユーザー呼び出し箇所**の site_id を取る
    - `set_api_label(op=op_name, site_id=site_id, label=self._pending_label)`
    - `meta/defaults = primitive_registry.get_meta/get_defaults(op_name)`
    - `resolved = resolve_api_params(...)`
    - `return Geometry.create(op=op_name, params=resolved)`
- [ ] (3) `E.op(op_name, **params)` を追加する。
  - 変更先: `src/grafix/api/effects.py`
  - 実装要点:
    - `caller_site_id(skip=1)` を step の site_id に使う
    - `return EffectBuilder(steps=((op_name, dict(params), site_id),), chain_id=site_id, label_name=self._pending_label)`
- [ ] (4) （必要なら）`EffectBuilder.op(op_name, **params)` を追加する。
  - 変更先: `src/grafix/api/effects.py`
  - 実装要点:
    - `caller_site_id(skip=1)`
    - `new_steps = self.steps + ((op_name, dict(params), site_id),)`
    - `return EffectBuilder(steps=new_steps, chain_id=self.chain_id, label_name=self.label_name)`
- [ ] (5) スタブ更新:
  - `tools/gen_g_stubs.py` に `G.op` / `E.op` / （必要なら）`EffectBuilder.op` を Protocol へ出力する処理を追加。
  - 型はまず `op_name: str, **params: Any` でよい（個別 param 型は動的なので諦める）。
  - `python -m tools.gen_g_stubs` で `src/grafix/api/__init__.pyi` を再生成。
- [ ] (6) 最小テスト追加:
  - `tests/api/test_dynamic_call.py`（新規）などで以下を確認:
    - `G.op("line", ...)` が `Geometry(op="line")` を返す
    - `E.op("scale", ...)(g)` が `Geometry(op="scale")` を返す
    - （追加するなら）`E.op("scale", ...).op("rotate", ...)` がチェーンできる
    - 未登録名は `AttributeError`
- [ ] (7) 最小検証:
  - `PYTHONPATH=src pytest -q tests/api/test_dynamic_call.py`
  - （任意）`ruff check ...` / `mypy ...` はリポ全体状況に合わせて判断

## 事前確認したいこと（あなたに質問）

1. `G.op("circle", ...)` / `E.op("scale", ...)` のように op 名は **第 1 引数（positional）**で良い？；はい
2. effect 側は `EffectBuilder.op(...)` も必要？（`E.op(...)` だけで足りる？）;E.op だけでいい。
