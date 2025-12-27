# pytest 失敗分析: primitive の `scale` (vec3 -> float)

- どこで: `tests/core/primitives/*` と `src/grafix/core/primitives/*`
- 何を: primitive の `scale` を `vec3` から `float` に変更した後に pytest が失敗する箇所の特定と原因整理
- なぜ: 次の修正（テスト/呼び出し側の更新）に向けて、失敗点と期待値のズレを明確化するため

## 実行結果

- 修正前: 2025-12-27 `PYTHONPATH=src pytest -q` → `5 failed, 299 passed`
- 修正後: 2025-12-27 `PYTHONPATH=src pytest -q` → `304 passed`

## 前提（作業ツリー差分）

`git status --porcelain` 時点で依頼範囲外と思われる未コミット差分・未追跡ファイルが複数あり、ここに書いた pytest 結果は「現在の作業ツリー」前提。

## 修正前の失敗テスト一覧

すべて共通して「`scale` に `tuple[float, float, float]` を渡している」ことがトリガーになっている。

1. `tests/core/primitives/test_grid.py::test_grid_applies_center_and_scale`

   - 渡している値: `scale=(2.0, 4.0, 6.0)`（`tests/core/primitives/test_grid.py:51`）
   - 例外: `ValueError: grid の scale は float である必要がある`（起点 `src/grafix/core/primitives/grid.py:101`）
   - 期待値の前提: `x/y/z` 各軸の非等方スケールを primitive `scale` が担う前提（例: `y` 方向だけ 4 倍など）

2. `tests/core/primitives/test_polygon.py::test_polygon_center_and_scale_affect_coords`

   - 渡している値: `scale=(2.0, 3.0, 4.0)`（`tests/core/primitives/test_polygon.py:44`）
   - 例外: `ValueError: polygon の scale は float である必要がある`（起点 `src/grafix/core/primitives/polygon.py:64`）
   - 期待値の前提: タプルを `vec3` スケールとして渡せる前提（ただしこのテストの検証点は x 成分のみ）

3. `tests/core/primitives/test_polyhedron.py::test_polyhedron_center_and_scale_affect_coords`

   - 渡している値: `scale=(float(scale[0]), float(scale[1]), float(scale[2]))`（`tests/core/primitives/test_polyhedron.py:60`）
   - 例外: `ValueError: polyhedron の scale は float である必要がある`（起点 `src/grafix/core/primitives/polyhedron.py:86`）
   - 期待値の前提: `expected = base.coords * scale_vec + center_vec`（非等方スケール）

4. `tests/core/primitives/test_sphere.py::test_sphere_center_and_scale_affect_coords`

   - 渡している値: `scale=(2.0, 3.0, 4.0)`（`tests/core/primitives/test_sphere.py:79`）
   - 例外: `ValueError: sphere の scale は float である必要がある`（起点 `src/grafix/core/primitives/sphere.py:388`）
   - 期待値の前提: `y` 成分のスケールが 3 倍になる前提（期待値が `21.5`）

5. `tests/core/primitives/test_torus.py::test_torus_center_and_scale_affect_coords`
   - 渡している値: `scale=(2.0, 3.0, 4.0)`（`tests/core/primitives/test_torus.py:73`）
   - 例外: `ValueError: torus の scale は float である必要がある`（起点 `src/grafix/core/primitives/torus.py:76`）
   - 期待値の前提: `expected = base.coords * scale_vec + center_vec`（非等方スケール）

## 原因（共通パターン）

- 現在の primitive 実装は `scale: float`（等方スケール）で、内部で `float(scale)` による変換を行っている。
- しかし、上記 5 テストは `scale` に `tuple` を渡しており `float(tuple)` ができないため例外になる。
- また、テストの期待値も `base.coords * scale_vec + center_vec`（非等方スケール）を前提にしており、新仕様（等方スケール）と整合しない。

## 影響範囲メモ

- テスト内で `scale` にタプルを渡している箇所は上記 5 件のみ（`rg '\"scale\"\\s*:\\s*\\(' tests` で確認）。
- したがって、pytest の失敗も現状はこの 5 件に収束している。

## 対応（完了）

- [x] 5 テストの `scale=(...)` を float に変更
- [x] 期待値を等方スケールの式に更新（`expected = base.coords * s + center_vec` 等）
- [x] 非等方スケールは primitive では扱わない（必要なら effect 側で別途）
