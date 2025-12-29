# buffer: distance_base / distance_slope 導入チェックリスト（2025-12-28）

目的: `E.buffer(...)` の距離指定を `distance_base: Vec3` / `distance_slope: Vec3` に置き換え、bbox 正規化座標 t∈[-1,+1] に対する距離場で buffer の強さ（outer/inner を含む）を制御できるようにする（破壊的変更）。

背景:

- 現状の `buffer` は `distance: float`（符号で outer/inner）で、空間勾配の表現ができない。
- `drop` / `partition` は `*_base` / `*_slope`（vec3）+ 正規化座標 t による「空間的に変化するパラメータ」を提供しており、同じ体験を `buffer` にも持ち込みたい。

方針（案）:

- `distance` を廃止し、`distance_base` と `distance_slope` を導入する（互換ラッパー/シムは作らない）。
- 各ポリラインごとに代表点 p（例: ポリライン重心）を取り、`t` を算出して距離ベクトル `d_vec` を評価する（距離は「ポリライン単位で一定」）。
- `d_vec` から (outer/inner の符号) と (2D バッファ距離 `du,dv`) を導出し、Shapely の等方 buffer を「スケール →`buffer(1)`→ 逆スケール」で擬似的に anisotropic にする。
- inner は現行同様「holes/interiors を返す」。存在しない場合は空ジオメトリ（`keep_original=True` なら元のみ）。

非目的:

- 既存の保存済みパラメータ/プリセットの移行（壊れてよい）
- `cap_style` 対応、join の追加仕様
- 3D の厳密なチューブ/オフセット（出力は引き続きポリライン）
- 1 本のポリライン内で距離が連続的に変化する“可変距離 buffer”（segment ごとに union 等）は今回はやらない

## 0) 事前に決める（あなたの確認が必要）

- [ ] 破壊的変更を許容する（`distance` を削除し `distance_base/distance_slope` に統一、互換なし）；OK
- [ ] 代表点 p の定義
  - 案 A: ポリライン頂点の平均（最小）；こちらで
  - 案 B: ポリライン bbox の中心
- [ ] `t` の定義（drop/partition 方式）
  - 案 A: pivot は bbox center 固定（drop 型、追加パラメータなし）
  - 案 B: `auto_center: bool` と `pivot: Vec3` を追加し pivot を選べる（partition 型）
- [ ] outer/inner の符号ルール（`d_vec` -> `sign`）
  - 案 A: `sign = sign(dx+dy+dz)`（0 は no-op、距離は `abs`）
  - 案 B: `sign = sign(max_abs_component)`（0 は no-op、距離は `abs`）
  - 案 C: components が全て同符号のときのみ有効、mixed sign は no-op
- [ ] 2D 距離 `(du,dv)` の導出式（`abs(d_vec)` と plane basis `u,v` から）
  - 案 A（L2 投影）: `du = sqrt((abs_dx*u_x)^2 + (abs_dy*u_y)^2 + (abs_dz*u_z)^2)`（`dv` も同様）
  - 案 B（L1 投影）: `du = abs_dx*|u_x| + abs_dy*|u_y| + abs_dz*|u_z|`（`dv` も同様）
- [ ] `du==0` または `dv==0` の扱い（no-op / 空ジオメトリ / 片軸だけ buffer）
- [ ] GUI 範囲（例）
  - `distance_base`: `ui_min=-25.0`, `ui_max=25.0`
  - `distance_slope`: `ui_min=-25.0`, `ui_max=25.0`（t が [-1,+1] なので同レンジで直感的）

## 1) 受け入れ条件（完了の定義）

- [ ] `E.buffer(distance_base=(0.1,0.1,0.1), distance_slope=(0.0,0.0,0.0))` が realize まで到達する
- [ ] 2 本のリング（x<0 と x>0）に対し、`distance_slope.x > 0` で右側リングの bbox 拡張が大きくなる（drop/partition 的な「空間勾配」）
- [ ] 符号で outer/inner が切り替わる（inner は holes/interiors、無いなら空）
- [ ] `keep_original=True` が引き続き有効
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer.py`（呼び出し更新）
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer_negative_distance.py`（vec3 + base/slope 版に更新）
- [ ] 新規テスト: `PYTHONPATH=src pytest -q tests/core/effects/test_buffer_distance_base_slope.py`
- [ ] `python -m tools.gen_g_stubs` 後に `tests/stubs/test_api_stub_sync.py` が通る
- [ ] `mypy src/grafix`
- [ ] `ruff check .`（環境に ruff がある場合）

## 2) 仕様案（API/パラメータ）

- effect シグネチャ（案）
  - `buffer(inputs, *, join="round", quad_segs=12, distance_base=(5,5,5), distance_slope=(0,0,0), keep_original=False, ...)`
- `distance_base : Vec3`
  - `t=0`（pivot）での距離ベクトル [mm]
- `distance_slope : Vec3`
  - `t∈[-1,+1]` に対する距離ベクトル勾配（軸別）[mm]
- （任意、0) の決定次第）`auto_center/pivot`

## 3) 実装設計（アルゴリズム）

- [ ] 早期 no-op 条件
  - [ ] `distance_base==(0,0,0)` かつ `distance_slope==(0,0,0)` のとき no-op（入力を返す）
- [ ] bbox の `pivot/extent` を決める
  - [ ] pivot: 0) の方針（bbox center 固定 or auto_center/pivot）
  - [ ] `inv_extent` は drop/partition と同様に 0 除算回避（extent<1e-9 なら 0）
- [ ] 各ポリラインについて:
  - [ ] 代表点 p（0) の定義）を 3D で計算
  - [ ] `t = clip((p - pivot) * inv_extent, -1..1)`
  - [ ] `d_vec = distance_base + distance_slope * t`
  - [ ] `sign` を 0) のルールで決定（0 は no-op）
  - [ ] `abs(d_vec)` を作り、`(du,dv)` を 0) の導出式で算出（`u,v` は推定平面基底）
  - [ ] Shapely で anisotropic buffer（現 plan の最小形）:
    - [ ] `LineString(line2)` を `(1/du, 1/dv)` でスケール
    - [ ] `buffer(1, quad_segs=..., join_style=...)` を実行
    - [ ] 結果を `(du, dv)` で逆スケール
  - [ ] `sign>0` なら exterior、`sign<0` なら interiors を抽出して 3D へ戻す
- [ ] keep_original=True は現状どおり末尾へ append

## 4) 変更箇所（ファイル単位）

- [ ] `src/grafix/core/effects/buffer.py`
  - [ ] `buffer_meta` に `distance_base` / `distance_slope`（`kind="vec3"`）を追加
  - [ ] `distance` を削除し、シグネチャ/実装/Docstring を base/slope へ更新
  - [ ] `t` 計算（drop/partition の bbox 正規化）を追加
  - [ ] anisotropic buffer の実装（ローカル import で `shapely.affinity` を使用）
- [ ] 既存テスト更新: `tests/core/effects/test_buffer.py` / `tests/core/effects/test_buffer_negative_distance.py`
  - [ ] `distance=` 呼び出しを `distance_base/distance_slope` へ変更
- [ ] 新規テスト: `tests/core/effects/test_buffer_distance_base_slope.py`
  - [ ] 2 本リング + `distance_slope.x` で厚み差が出ること
- [ ] `tools/benchmarks/effect_benchmark.py`
  - [ ] `buffer` の overrides を `distance_base/distance_slope` へ更新
- [ ] スタブ再生成: `python -m tools.gen_g_stubs`（手編集しない）

## 5) 実行コマンド（ローカル確認）

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer.py`
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer_negative_distance.py`
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer_distance_base_slope.py`
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`
- [ ] `mypy src/grafix`
- [ ] `ruff check .`（任意）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] `distance_slope` を入れるなら、将来 `*_curve`（ease）を足すか（今回は無し）
- [ ] outer/inner の符号が空間で反転するケースを許容するか（許容するなら no-op 条件を設計する）
