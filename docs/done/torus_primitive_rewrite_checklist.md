# torus primitive リライトチェックリスト

目的: `src/primitives/from_previous_project/torus.py` は旧プロジェクト依存（`engine.*` / `numba` / `.registry.shape` / `Geometry.from_lines` / `__param_meta__`）のため、このプロジェクトの現行実装（`@primitive` + `RealizedGeometry` + `ParamMeta`）で動作しない。現仕様の primitive として `G.torus(...)` で使えるように作り直す。

## 仕様確認（先に決めたい）

- [x] 置き場所（ファイル）を決める
  - [x] A. 採用: `src/primitives/torus.py` を新規作成する（旧ファイルは残す）
  - [ ] B. 不採用: `src/primitives/from_previous_project/torus.py` を現行形式に置換する
- [x] primitive 名（op 名）を `torus` で固定する
  - `Geometry(op="torus")` / `G.torus(...)` を想定。
- [x] 引数セットを決める（UI/使い勝手）
  - [x] 採用: `major_radius`, `minor_radius`, `major_segments`, `minor_segments`, `center`, `scale`
- [x] 不正値の扱い（簡潔に統一）
  - `major_segments/minor_segments`:
    - [ ] A. `circle` と同様に `3` 未満は `ValueError`
    - [x] B. `polygon` と同様に `3` にクランプする
  - `major_radius/minor_radius`:
    - [x] A. そのまま通す（負値も許容する）
    - [ ] B. `0` 未満は `ValueError`
- [x] 出力ポリライン構成を固定する
  - [x] 旧実装踏襲: 子午線（major 方向）`major_segments` 本 + 緯線（minor 方向）`minor_segments` 本
  - [x] 各ポリラインは「先頭==末尾」の閉ポリライン（`+1` 点を持つ）

## 実装 TODO

- [x] torus primitive を現行スタイルで実装する
  - [x] `src/primitives/torus.py` を追加する
  - [x] ファイル先頭ヘッダ（どこで/何を/なぜ）を追加する
  - [x] `torus_meta` を `ParamMeta` で定義する
    - [x] `major_radius/minor_radius`: `kind="float"`
    - [x] `major_segments/minor_segments`: `kind="int"`
    - [x] `center/scale`: `kind="vec3"`
  - [x] `@primitive(meta=torus_meta)` で登録し、返り値を `RealizedGeometry` に統一する
  - [x] `coords/offsets` を構築する
    - `coords`: float32, shape `(N,3)`
    - `offsets`: int32, shape `(M+1,)`（M はポリライン本数）
  - [x] `center/scale` を `coords` に反映する
  - [x] 旧実装由来の依存を消す（新規実装に持ち込まない）
    - `numba` / `engine.*` / `.registry.shape` / `Geometry.from_lines` / `__param_meta__`
- [x] `src/api/primitives.py` に torus 実装モジュール import を追加し、`G.torus` が利用できるようにする
- [ ] 旧ファイルを整理する
  - [ ] （今回は保持）`src/primitives/from_previous_project/torus.py` は残す

## テスト TODO

- [x] `tests/test_torus.py` を追加する
  - [x] `realize(Geometry.create("torus", ...))` が例外なく動く（登録と評価の疎通）
  - [x] `offsets` の不変条件（`offsets[0]==0` / `offsets[-1]==len(coords)` / 単調非減少）を満たす
  - [x] 期待するポリライン本数になる（`major_segments + minor_segments`）
  - [x] 各ポリラインが閉じている（各区間で `coords[start] == coords[end-1]`）
  - [x] `center/scale` が座標に反映される
- [x] `pytest -q tests/test_torus.py` を実行する
- [ ] （任意）`ruff` / `mypy` を対象限定で実行する（環境に無ければスキップ）
  - メモ: 手元環境に `ruff` が無かったためスキップした

## メモ / 追加提案（作業中に追記）

- 実装は「純 NumPy」で十分（まずは依存を増やさず、読みやすさ優先）。必要になったら後でベクトル化や高速化を検討する。
- `offsets` は「各ポリラインの点数」を累積和して作るとミスしにくい（`[0, n0, n0+n1, ...]`）。
