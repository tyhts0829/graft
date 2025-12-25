# 任意の primitive/effect を「名前引数」で呼び出す API 追加案（2025-12-25）

## 結論（可能か？）

- 可能。
- すでに現状でも `getattr(G, name)(**params)` / `getattr(E, name)(**params)`（および `EffectBuilder` に対しても `getattr(builder, name)(**params)`）で任意名を呼べる。
- これを「明示的な public API」として用意するなら、薄いラッパとして `G.call(name, **params)` / `E.call(name, **params)`（＋必要なら `EffectBuilder.call(...)`）を追加するのが最小。

## 目的 / 背景

- 設定ファイルや GUI などから「文字列の op 名」で primitive/effect を選びたい。
- `getattr(...)` でも実現できるが、API として発見可能（補完/型/ドキュメント）にしたい。

## 追加する API（案）

- primitive:
  - `G.call("circle", r=10.0, center=(0,0,0)) -> Geometry`
  - `G("label").call("circle", ...)` も既存の label 仕様に従う（`_pending_label` を使う）。
- effect:
  - `E.call("scale", scale=(2,2,2)) -> EffectBuilder`
  - `E("label").call("scale", ...)` も既存の label 仕様に従う。
  - （任意）`E.scale(...).call("rotate", ...)` のようにチェーン途中でも名前で追加できるよう `EffectBuilder.call(...)` を追加。

## 実装方針（最小・単純）

### 方針 A（推奨）: API 層の薄いラッパ

- 追加するのは「呼び出し補助」だけで、DAG の新しい op（=新 primitive/effect 実装）は増やさない。
- 実体は既存の `__getattr__` が返す factory を呼ぶだけにする。
  - これにより、既存の
    - 未登録名の検査（AttributeError）
    - `ParamMeta/defaults` による `resolve_api_params`
    - `caller_site_id` / label 保存
    をそのまま利用できる。

### 方針 B（別案）: DAG レベルの「invoke」ノード（※要検討）

- `Geometry(op="invoke_primitive", args={"op":"circle","params":{...}})` のようなメタ primitive/effect を追加し、`realize` 時に内部で registry を引いて実行する方式。
- デメリットが大きい:
  - 内側の primitive/effect の `ParamMeta` が素直に UI に出ない（params が dict になりがち）。
  - wrapper op になるため GeometryId が内側 op と一致せずキャッシュが分断される（許容はできるが利点が薄い）。
- 「op 名を GUI パラメータで差し替えたい」など *DAG レベルでの動的ディスパッチ* が必要な場合のみ検討対象。

## 実施チェックリスト（承認後に実装）

- [ ] (1) 仕様確定: 方針 A（API ラッパ）で行くか、方針 B（DAG invoke）が必要かを決める。
- [ ] (2) `G.call(name, **params)` を追加する。
  - 変更先: `src/grafix/api/primitives.py`
  - 実装: `factory = self.__getattr__(name)` → `return factory(**params)`
- [ ] (3) `E.call(name, **params)` を追加する。
  - 変更先: `src/grafix/api/effects.py`
  - 実装: `factory = self.__getattr__(name)` → `return factory(**params)`
- [ ] (4) （必要なら）`EffectBuilder.call(name, **params)` を追加する。
  - 変更先: `src/grafix/api/effects.py`
  - 実装: `factory = self.__getattr__(name)` → `return factory(**params)`
- [ ] (5) スタブ更新:
  - `tools/gen_g_stubs.py` に `call` を Protocol へ出力する処理を追加（型は `(**params: Any)` でよい）。
  - `python -m tools.gen_g_stubs` で `src/grafix/api/__init__.pyi` を再生成。
- [ ] (6) 最小テスト追加:
  - `tests/api/test_dynamic_call.py`（新規）などで以下を確認:
    - `G.call("line", ...)` が `Geometry(op="line")` を返す
    - `E.call("scale", ...)(g)` が `Geometry(op="scale")` を返す
    - （追加するなら）`E.scale(...).call("rotate", ...)` がチェーンできる
    - 未登録名は `AttributeError`
- [ ] (7) 最小検証:
  - `PYTHONPATH=src pytest -q tests/api/test_dynamic_call.py`
  - （任意）`ruff check ...` / `mypy ...` はリポ全体状況に合わせて判断

## 事前確認したいこと（あなたに質問）

1. ここで欲しいのは「API で `G.call("circle", ...)` と書ける」レベル（方針 A）で合っている？それとも「op 名自体をパラメータとして差し替えたい」= DAG レベルの動的ディスパッチ（方針 B）が必要？
2. `call` という名前で良い？（代替: `by_name` / `invoke` / `op`）
3. effect 側は `E.call(...)` だけで十分？それともチェーン途中用に `EffectBuilder.call(...)` も欲しい？

