# グリッド頂点点配置エフェクト実装チェックリスト（2025-12-28）

目的: 既存の描画パイプライン（polyline）に乗る形で、規則的なグリッド頂点へ「点（ドット/マーカー）」を配置できる新規 effect を追加する。

背景:

- `G.grid()` は線分列のグリッドを生成できるが、グリッド頂点（格子点）へ点を並べる手段がない。
- 現状の export（例: `export_svg`）は「2 点未満の polyline を出力しない」ため、“1 点だけ” の表現は見えない可能性が高い。

方針（今回の決定案）:

- effect 名は `grid_points`（仮）とし、`E.grid_points(...)` で使えるようにする。
- 「点」は polyline として表現する（最小 2 点の短い線分=ドット/マーカー）ことで、既存レンダラ/エクスポータにそのまま載せる。
- 実装は `src/grafix/core/effects/` の新規モジュール 1 枚で完結させ、他 effect への依存はしない（`util.py` の利用は可）。

非目的:

- point sprite / GL_POINTS など「点専用レンダリング」の導入
- export 系（SVG/動画等）へ点要素を追加する大改修
- 互換ラッパー/シムで旧仕様を延命すること

## 0) 事前に決める（あなたの確認が必要）

- [ ] effect 名（案: `grid_points` / `grid_vertices` / `dot_grid`）
- [ ] 「グリッド頂点」の定義
  - [ ] A: `nx * ny` の格子点（交点）を生成（推奨）
  - [ ] B: `G.grid()` の頂点（線分の端点）を点化（入力依存）
- [ ] 点の見せ方（出力ジオメトリの表現）
  - [ ] A: 各点を「短い 2 点線分」で表現（推奨、export 互換が高い）
  - [ ] B: 各点を「十字（2 本の短線分）」で表現（見やすいが頂点数増）
- [ ] `keep_original`（元の入力を一緒に出力するか）
  - [ ] default False（点だけに置換） / default True（点を重ねる）
- [ ] 座標系
  - [ ] XY 平面固定（z は `center.z`） / 将来: 任意平面対応は今回はやらない
- [ ] サイズ系の意味
  - [ ] `marker_size` は「短線分の長さ」（単位は座標系そのまま）
  - [ ] 厚みは layer style に従う（effect は関与しない）

## 1) 受け入れ条件（完了の定義）

- [ ] `E.grid_points(...)` が未登録エラーにならず、realize まで到達する
- [ ] 生成数が期待どおり（例: `nx=3, ny=2` → 6 個のマーカー）
- [ ] `export_svg` で可視な要素として出力される（2 点 polyline であること）
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_grid_points.py`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`
- [ ] スタブ再生成（`python -m tools.gen_g_stubs`）が通る

## 2) 仕様案（API/パラメータ）

### effect シグネチャ（案）

- `grid_points(inputs, *, nx=20, ny=20, center=(0,0,0), scale=1.0, marker_size=0.01, marker="segment", keep_original=False) -> RealizedGeometry`

### ParamMeta（案）

- `nx`, `ny`: `kind="int"`（UI 上限はひとまず `G.grid` と同程度）
- `center`: `kind="vec3"`
- `scale`: `kind="float"`
- `marker_size`: `kind="float"`（0 以上）
- `marker`: `kind="choice"`, `choices=("segment","cross")`（採用する場合）
- `keep_original`: `kind="bool"`

## 3) 実装設計（アルゴリズム）

- [ ] `nx_i=int(nx)`, `ny_i=int(ny)` を確定（0 以下は空を返す）
- [ ] x 座標: `linspace(-0.5, 0.5, nx_i)`、y 座標: `linspace(-0.5, 0.5, ny_i)`
- [ ] 格子点配列を `meshgrid` で作り、`(nx_i*ny_i, 3)` の点群へ（z=0）
- [ ] `scale` と `center` を適用（`coords = coords*scale + center`）
- [ ] マーカー化:
  - [ ] segment: 各点 p に対し `p±(marker_size/2,0,0)` の 2 点を生成し 1 polyline にする
  - [ ] cross: 各点 p に対し x/y の 2 セグメント（計 2 polylines）を生成する
- [ ] offsets を決定的に構築（`[0,2,4,...]` など）
- [ ] `keep_original=True` の場合、入力（通常 `inputs[0]`）とマーカーを concat して返す

## 4) 変更箇所（ファイル単位）

- [ ] `src/grafix/core/effects/grid_points.py` を追加
  - [ ] `@effect(meta=...)` で登録（`bypass` は meta に書かない）
  - [ ] モジュール docstring は effects 仕様に従い「効果の説明」だけを書く（どこで/なにを/なぜ 形式は使わない）
- [ ] `src/grafix/api/effects.py` に import を追加（レジストリ登録目的）
- [ ] テスト追加: `tests/core/effects/test_grid_points.py`
- [ ] スタブ再生成: `python -m tools.gen_g_stubs`（差分レビューしてコミットは別）

## 5) テスト観点（最小）

- [ ] `nx=0` または `ny=0` で空ジオメトリ（coords=0, offsets=[0]）
- [ ] 小さい値での座標一致（例: `nx=2, ny=2, scale=2, center=(1,0,0)`）
- [ ] `marker_size` が反映される（セグメント端点が `±marker_size/2` だけズレる）
- [ ] `keep_original=True` で polyline 数/offsets が入力+追加分になっている

## 6) 実行コマンド（ローカル確認）

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_grid_points.py`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`
- [ ] `python -m tools.gen_g_stubs`（スタブ差分確認）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] 将来拡張: 入力閉曲線の内側だけに格子点を残す（point fill 的な用途）
- [ ] 将来拡張: `auto_center`（入力 bbox/平均に追従）や `spacing`（nx/ny ではなく間隔指定）
- [ ] 将来拡張: 3D 格子（`nx,ny,nz`）や任意平面向き（axis/normal）
