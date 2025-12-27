# partition_site_density_base_slope_pivot_plan_2025-12-27.md

#

# どこで: `src/grafix/core/effects/partition.py`（+ stubs/tests）。

#

# 何を: Voronoi サイトのサンプリング密度を「位置ベース」で制御する（base/slope=vec3 + pivot）。

#

# なぜ: ジオメトリ中心から +X/+Y/+Z 側へ行くほど細かく分割する等、空間的な分割密度勾配を作るため。

#

## ゴール

- `partition` の Voronoi サイト分布を、ワールド座標の x/y/z で独立に偏らせられる。
- `pivot(vec3)` により「どこを基準に勾配をかけるか」を指定できる。
  - `auto_center=True` のときは pivot を無視（bbox 中心を使う）
  - `auto_center=False` のときだけ pivot が有効
- `seed` による決定性（同じ入力/引数なら同じ結果）を維持する。
- 既定値では従来どおり「一様」なサイト分布になる（機能はオフ）。

## 非ゴール

- 非線形密度（S 字/ノイズ/距離減衰など）。
- 平面基底 u/v（ローカル座標）での勾配。あくまでワールド x/y/z の勾配。
- サイト数 `site_count` を確率で変動させる（最終的に `site_count` を満たす）。

## 仕様（採用案）

### 新規パラメータ（案）

- `site_density_base: tuple[float, float, float] = (0.0, 0.0, 0.0)`
  - 各成分は 0..1 を想定（範囲外/非有限はクランプ）
  - base 自体は「密度（採用確率）の切片」を表す
- `site_density_slope: tuple[float, float, float] = (0.0, 0.0, 0.0)`
  - 概ね -1..+1 を想定（非有限は 0.0 扱い）
  - 正規化座標 `t∈[-1,+1]` に対する勾配（中心 → 端）
- `auto_center: bool = True`
  - True: pivot は無視し、bbox 中心を pivot とする
  - False: pivot を使用する
- `pivot: tuple[float, float, float] = (0.0, 0.0, 0.0)`
  - `auto_center=False` のときだけ有効

※ 既定値（base/slope が全て 0）では「密度制御を無効化」し、従来の一様サンプリングを維持する。

### 正規化座標 `t`（ワールド座標）

- bbox は入力 `base.coords` のワールド bbox を使う（平面に射影する前の 3D 座標）。
  - `bbox_center = (min_v + max_v)/2`
  - `extent = (max_v - min_v)/2`
  - `inv_extent[k] = 0` if `extent[k] < 1e-9` else `1/extent[k]`
- pivot:
  - `auto_center=True`: `pivot = bbox_center`
  - `auto_center=False`: `pivot = user_pivot`
- 候補サイト点 `p`（ワールド座標）について:
  - `t = (p - pivot) * inv_extent`
  - `t` は各成分を `[-1,+1]` に clamp

### サイト採用確率 `p_eff`（OR 合成）

候補点 `p` から得た `t=(tx,ty,tz)` を使い、軸別確率を

- `p_x = clamp(base_x + slope_x * tx, 0..1)`
- `p_y = clamp(base_y + slope_y * ty, 0..1)`
- `p_z = clamp(base_z + slope_z * tz, 0..1)`

とし、合成は OR イメージで

- `p_eff = 1 - (1-p_x)(1-p_y)(1-p_z)`

候補点は `rng.random() < p_eff` のとき採用する。

### サイト生成アルゴリズム（方針）

- 密度制御が無効（base/slope が全て 0）:
  - 現行どおり「region 内に一様」で `site_count` 個まで採用する。
- 密度制御が有効:
  1. 現行の「region 内候補点生成」を維持
  2. region 内に入った候補点について `p_eff` でリジェクトサンプリング
  3. `site_count` に満たない場合は、残りを「一様採用（従来ルール）」で埋めて `site_count` を満たす
     - 目的: 過度に分割が粗くなる（点が集まらない）ケースを避け、`site_count` の意味を保つ
  4. それでも 0 点なら現行同様 `representative_point()` へフォールバック

## 実装チェックリスト

### 1) meta / シグネチャ / docstring 更新

- [x] `src/grafix/core/effects/partition.py` の `partition_meta` に追加
  - [x] `site_density_base: ParamMeta(kind="vec3", ui_min=0.0, ui_max=1.0)`
  - [x] `site_density_slope: ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)`
  - [x] `auto_center: ParamMeta(kind="bool")`
  - [x] `pivot: ParamMeta(kind="vec3", ui_min=-100.0, ui_max=100.0)`（仮レンジ）
- [x] `partition()` の引数に追加（型は `tuple[float,float,float]` / `bool`）
- [x] docstring に追加（auto_center と pivot の関係、密度制御が「サイト分布」を変える点）

### 2) `p_eff` 計算と候補点の採用

- [x] bbox（`base.coords`）から `bbox_center/extent/inv_extent` を計算
- [x] pivot を決定（auto_center なら bbox_center、そうでなければ user pivot）
- [x] region 内候補点（2D）をワールドへ lift して `p_eff` を計算
  - [x] `_lift_to_3d` をベースに batch vectorize（`o + x*u + y*v`）
- [x] `p_eff` による acceptance を追加し、`site_count` 個まで集める
- [x] `site_count` に満たない場合の top-up（一様採用）を追加

### 3) テスト

- [x] `tests/core/effects/test_partition.py` を追加
  - [x] shapely が無ければ skip
  - [x] 基本: 1 つの矩形ループ（XY 平面）を用意し、`seed` 固定で実行
  - [x] `site_density_*` 無効（既定）と有効（+X 側を強める）で、出力ループ centroid の x 分布が変わることを確認
  - [x] `auto_center=False` + `pivot` をずらすと、偏りの中心が変わることを確認

### 4) stubs 再生成

- [x] `python -m tools.gen_g_stubs` を実行し、`src/grafix/api/__init__.pyi` を更新
- [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py` を通す

### 5) 最小の検証コマンド

- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_partition.py`
- [ ] `ruff check src/grafix/core/effects/partition.py tests/core/effects/test_partition.py`（この環境では `ruff` が見つからない）
- [x] `mypy src/grafix/core/effects/partition.py`

## 事前確認したい点（この仕様で進めてよい？）

- パラメータ名は `site_density_base/site_density_slope` で良い？（代案: `site_probability_base/site_probability_slope`）；はい
- 「足りない分は一様で埋める」top-up の挙動は良い？（`site_count` の意味を保つ目的）；はい
