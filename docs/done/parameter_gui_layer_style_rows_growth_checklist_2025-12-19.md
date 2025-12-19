# どこで: `src/grafix/core/scene.py`（暗黙 Layer の site_id）、
#         `src/grafix/core/geometry.py`（Geometry.id の内容署名）、
#         `src/grafix/core/pipeline.py`（Layer style の観測）、
#         `src/grafix/core/parameters/store.py`（観測キーの保持）、
#         `src/grafix/interactive/parameter_gui/store_bridge.py`（Style セクションへの表示）、
#         `tests/core/test_scene.py`（現状の期待値）。
# 何を: parameter_gui でパラメータ変更すると Style セクションの Layer style（line_color/line_thickness）行が増殖する問題の原因整理と、実装改善チェックリストをまとめる。
# なぜ: GUI が汚れて操作できず、ParamStore(JSON) も肥大化しやすいため。

# parameter_gui: Style の layer 行が増殖する（チェックリスト）

## Update（修正状況）

- 2025-12-19: 暗黙 Layer の `site_id` を `implicit:{geometry.id}` から `implicit:{index}`（出現順）へ変更し、増殖の根本原因を除去（手動確認は未実施）。

## 現象（再現）

- `python sketch/main.py`（`parameter_gui=True`）で起動する
- Parameter GUI から Primitive/Efffect の任意パラメータを変更する（例: polyhedron の `type_index`）
- Style セクション内の layer 行（`line_thickness` / `line_color`）が増え続ける（同じ見た目の行が複数並ぶ）

補足:

- 値を変えない間は増えず、「パラメータ変更のタイミング」で増殖が目立つ想定。

## 原因（実装・結論）

Layer style 行のキーが `Layer.site_id` で決まり、暗黙 Layer の `site_id` が **Geometry.id に依存**しているため。

流れ:

- 修正前の `src/grafix/core/scene.py:normalize_scene()` は、`draw(t)` が `Geometry` を返した場合に暗黙 `Layer` を作り、`site_id = f"implicit:{geometry.id}"` を採用していた（これが増殖の直接原因）。
- `src/grafix/core/geometry.py:compute_geometry_id()` の `Geometry.id` は内容署名（`op/inputs/args`）で、**ParamStore で解決された値が変わると id も変わる**。
- `src/grafix/core/pipeline.py:realize_scene()` は各 Layer について `__layer_style__`（`line_thickness`/`line_color`）を毎フレーム観測し、`ParameterKey(op="__layer_style__", site_id=layer.site_id, arg=...)` を ParamStore に登録する。
- したがって、GUI で Primitive/Efffect の値を変えて Geometry が作り直されるたびに暗黙 Layer の `site_id` が変化し、**新しい Layer style グループ**が作られる。過去のグループは ParamStore 内に残るため、Style セクションに行が増殖する。

## 方針（改善案）

### 案A（推奨）: 暗黙 Layer の site_id を「シーン内の位置（slot）」にする

- `normalize_scene()` が `Geometry` を暗黙 `Layer` に包むとき、出現順の連番で `implicit:{index}`（例: `implicit:1`）を付与する。
- Geometry の内容が変わっても同じ slot は同一 Layer とみなせるため、Layer style 行が増殖しない。
- 破壊的変更:
  - 既存の `implicit:{geometry.id}` 由来の `data/output/param_store/*.json` は一致しなくなる（次回保存時に stale として prune される想定）。

### 案B: 現状のまま、利用側を `L(...)` に寄せる

- `sketch/main.py` 等の例を `return L(geometry, name=...)` に変える。
- ライブラリ側の挙動はそのままなので、「裸 Geometry を返す」利用では再発しうる。

（参考）案C: Geometry に callsite/slot を埋め込む、wrapper を導入する  
→ 設計が重くなるため今回は避けたい。

## 実装チェックリスト（案A）

- [x] `src/grafix/core/scene.py` の暗黙 `Layer.site_id` 生成を `implicit:{index}`（暗黙 Layer の出現順 1..N）へ変更する
- [x] docstring/型コメントに仕様を明記する（「暗黙 Layer は slot-based。Geometry.id とは無関係」）
- [x] `tests/core/test_scene.py` を更新する（`implicit:{g.id}` 期待を削除し、slot-based を期待する）
- [x] ネスト列の flatten でも site_id が決定的になるテストを追加する
- [x] `PYTHONPATH=src pytest -q tests/core/test_scene.py`
- [ ] 手動確認: `python sketch/main.py` で Parameter GUI の値を変えても Style の layer 行が増殖しない
- [ ] （任意）`docs/` の `implicit:{geometry.id}` 前提の記述を更新する（設計意図の再整理）

## 事前確認したいこと（確認結果）

- 暗黙 Layer の site_id を「暗黙 Layer の出現順 index（slot）」に変更して良い？ → OK（実施済み）
  - 良い点: パラメータ調整中に layer style 行が安定する
  - 注意点: シーンの並び/個数が変わると style の紐づきがずれる（ただし現状も別の形で破綻する）
