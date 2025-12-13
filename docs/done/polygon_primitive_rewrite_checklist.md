# polygon primitive リライトチェックリスト

目的: `src/primitives/polygon.py` が旧 API（`engine.core.geometry.Geometry` / `.registry.shape`）に依存しており、このプロジェクトの現行実装（`@primitive` + `RealizedGeometry` + `ParamMeta`）で動作しないため、作り直す。

## 仕様確認（先に決めたい）

- [x] 引数セットをどれにするか決める
  - A. 最小（旧に寄せる）: `n_sides`, `phase`（サイズ固定）；これに center[vec3], scale[vec3]を追加して。
  - B. circle と揃える: `n_sides`, `r`, `cx`, `cy`, `phase`
- [x] `phase` の単位（度数法でよいか）と 0° の向き（+X に頂点でよいか）を確定；度でいいよ。0 度が+X でいいよ。
- [x] `n_sides < 3` の扱い（例外にするか / クランプするか）を確定；クランプして。

## 実装 TODO

- [x] `src/primitives/polygon.py` を現行スタイルで書き直す
  - [x] ファイル先頭ヘッダ（どこで/何を/なぜ）
  - [x] `@primitive(meta=...)` + `ParamMeta` の定義
  - [x] `RealizedGeometry(coords, offsets)` を返す（閉ポリライン）
- [x] `src/api/primitives.py` で polygon 実装モジュールを import してレジストリ登録されるようにする

## テスト TODO

- [x] `tests/test_polygon.py` を追加する
  - [x] 頂点数（`n_sides + 1`）と `offsets` を検証
  - [x] 閉じていること（先頭 == 末尾）を検証
  - [x] `phase` の回転が効いていることを最低 1 ケースで検証
- [x] `pytest -q tests/test_polygon.py` を実行する
- [ ] （任意）`ruff` / `mypy` を対象限定で実行する（環境に無ければスキップ）

## メモ / 追加提案（作業中に追記）

- 実装仕様: 単位円（直径 1）に内接する正多角形を生成し、`phase` 回転→`scale`→`center` の順で変換する。
