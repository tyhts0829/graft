# polyhedron primitive リライトチェックリスト

目的: `src/primitives/polyhedron.py` が旧 API（`engine.*` / `.registry.shape` / `Geometry.from_lines` / `__param_meta__`）に依存しており、このプロジェクトの現行実装（`@primitive` + `RealizedGeometry` + `ParamMeta`）で動作しないため、作り直す。

## 仕様確認（先に決めたい）

- [x] どの正多面体をサポートするか決める
  - `data/regular_polyhedron/*_vertices_list.npz` を読み込み、存在する全種（現状 5 種）をサポートする。
    - `tetrahedron`, `hexahedron`, `octahedron`, `dodecahedron`, `icosahedron`
- [x] 選択引数をどうするか決める
  - A. 推奨: `kind: str`（`ParamMeta(kind="choice")`）で `"tetrahedron" | "cube" | ...` を選択
  - B. 採用: `type_index: int`（スライダー、範囲外はクランプ）
- [x] 出力のポリライン表現を決める（旧仕様）
  - `npz` 内の `arr_*` が「各面の閉ポリライン（最後の点=最初の点）」なので、それをそのまま 1 面=1 本のポリラインとして出力する。
- [x] 正規化（サイズ基準）を決める（旧仕様）
  - A. 推奨: `max(abs(coords)) == 0.5` になるようにスケールして原点中心にする（polygon と揃う）
  - B. 代替: 外接球半径が 0.5 になるようにスケールする
- [x] `center/scale` を primitive 側に持つ（旧仕様）
  - `center: vec3`, `scale: vec3` noted（polygon と同様に成分ごとに適用）。

## 追加確認（実装前に最終確認）

- [x] `type_index` の割当順（0..N-1）を次で固定する
  - `["tetrahedron", "hexahedron", "octahedron", "dodecahedron", "icosahedron"]`
- [x] `type_index` の引数名は `type_index` で確定する（旧コードは `polygon_index`）
- [x] `data/regular_polyhedron` が無い/壊れている場合の挙動は「例外」とする

## 実装 TODO

- [x] `src/primitives/polyhedron.py` を現行スタイルで書き直す
  - [x] ファイル先頭ヘッダ（どこで/何を/なぜ）
  - [x] `polyhedron_meta` を `ParamMeta` で定義（`type_index`, `center`, `scale`）
  - [x] `@primitive(meta=polyhedron_meta)` で登録し、返り値を `RealizedGeometry` に統一する
  - [x] `data/regular_polyhedron/*_vertices_list.npz` を読み込み、`coords/offsets` を構築する（面ポリライン列）
  - [x] `center/scale` を適用する（polygon と同様に成分ごと）
  - [x] 旧コード（`engine.*` / `.registry.shape` / `Geometry.from_lines` / `__param_meta__`）を削除する
- [x] `src/api/primitives.py` に polyhedron 実装モジュール import を追加し、`G.polyhedron` が利用できるようにする

## テスト TODO

- [x] `tests/test_polyhedron.py` を追加する
  - [x] `realize(Geometry.create("polyhedron", ...))` が例外なく動く（登録と評価の疎通）
  - [x] `offsets` の不変条件と、各面が「閉ポリライン」になっていること（先頭==末尾）を検証
  - [x] 面数の期待値を検証（tetra=4, hexa=6, octa=8, dodeca=12, icosa=20）
  - [ ] （任意）デフォルト出力が原点付近に収まり、サイズが正規化されていることを緩く検証
- [x] `pytest -q tests/test_polyhedron.py` を実行する
- [ ] （任意）`ruff` / `mypy` を対象限定で実行する（環境に無ければスキップ）

## メモ / 追加提案（作業中に追記）

- `data/regular_polyhedron` の `npz` は `arr_0..` 形式で、各 `arr` は shape `(K,3)` の float32・閉ポリライン（先頭==末尾）になっている。
- 5 種すべてで `max(abs(coords)) == 0.5` を満たしている（base 形状は既に正規化済み）。
