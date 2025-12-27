# drop_probability_base_slope_plan_2025-12-27.md
#
# どこで: `src/grafix/core/effects/drop.py`（+ stubs/tests）。
#
# 何を: drop effect に「位置ベースの確率勾配」を追加する（`probability_base` + `probability_slope(vec3)`）。
#
# なぜ: ジオメトリ中心から +X/+Y/+Z 側へ行くほど drop されやすい等、空間的な密度勾配を直感的に作るため。
#

## 前提 / 差分

- 本計画は「向きベース（line 方向 / face 法線）」案（`docs/memo/drop_probability_vec3_axis_plan_2025-12-27.md`）ではなく、
  **位置ベース（中心からの座標）**で確率を制御する。

## ゴール

- `drop` の確率制御を「中心からの位置」で行える。
- 各軸の寄与は独立に調整できる（X/Y/Z を別パラメータで指定）。
- `seed` で決定的（同じ入力/引数なら同じ結果）。
- GUI/CC:
  - `probability_base` は scalar CC で制御できる
  - `probability_slope` は vec3 CC で成分別に制御できる

## 非ゴール

- 旧 `probability: float` 互換の維持（ラッパー/シムを作らない）。
- 非線形カーブ（S字、ノイズ、距離減衰など）の導入。
- 「向き」で確率を変える（方向/法線ベース）の導入。

## 仕様（採用案）

### パラメータ

- `probability_base: float = 0.0`
  - 0..1 を想定（非有限は 0.0 扱い、範囲外は clamp）
  - 意味: **中心位置**（t=0）の drop 確率
- `probability_slope: tuple[float, float, float] = (0.0, 0.0, 0.0)`
  - 各成分は概ね -1..+1 を想定（範囲外でも可だが最終確率で clamp）
  - 意味: 正規化座標 `t∈[-1,+1]` に対して、`base` からの増減量（中心→端）
    - 例: `base=0.5, slope_x=0.5` なら、X- 端で 0.0、X+ 端で 1.0

### 代表点（line/face ごとの位置）

確率は「要素単位（line/face）」で決めるため、各要素の代表点 `c` を定義する。

- by="line": 対象ポリラインの頂点平均（centroid）を `c` とする
- by="face": 頂点数>=3 のリングの頂点平均を `c` とする（頂点数<3 の line は従来どおり常に残す）

### 正規化座標 `t`

全体ジオメトリの bbox から中心とスケールを決め、代表点を `-1..+1` に正規化する。

- bbox: `min_v = coords.min(axis=0)`, `max_v = coords.max(axis=0)`
- 中心: `center = (min_v + max_v) / 2`
- 半径: `extent = (max_v - min_v) / 2`
- 正規化:
  - `t = (c - center) / extent`
  - ただし `extent[k]` が極小（例: <1e-9）の軸は `t[k]=0.0`（割り算回避）
  - `t` は最終的に `[-1, +1]` に clamp

### 有効確率 `p_eff`

`t = (tx, ty, tz)` として、

- `p_eff_raw = probability_base + slope_x*tx + slope_y*ty + slope_z*tz`
- `p_eff = clamp(p_eff_raw, 0.0, 1.0)`

### 既存条件との合成

現状の drop と同様に OR（union）で `cond` を作る。

- interval 条件
- length 条件
- probability 条件（`rng.random() < p_eff`）

最後に `keep_mode` で反転。

### 乱数消費の決定性

現仕様踏襲:

- by="line": probability が有効な場合は **全 line で 1 回** `rng.random()` を消費（他条件の有無で RNG 消費量が変わらない）
- by="face": face 判定対象（頂点数>=3）に対して 1 回 `rng.random()` を消費

## 実装チェックリスト

### 1) API と meta の更新

- [ ] `src/grafix/core/effects/drop.py` の meta を更新
  - [ ] `probability` を廃止
  - [ ] `probability_base: ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)` を追加
  - [ ] `probability_slope: ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)` を追加
- [ ] `src/grafix/core/effects/drop.py:drop()` のシグネチャ更新
  - [ ] `probability_base: float = 0.0`
  - [ ] `probability_slope: tuple[float, float, float] = (0.0, 0.0, 0.0)`
- [ ] docstring 更新（中心/端での意味、式）

### 2) `p_eff` 計算の実装

- [ ] `src/grafix/core/effects/drop.py` に小さな純関数ヘルパを追加
  - [ ] bbox から `center/extent` を計算
  - [ ] ポリライン（start:end）から代表点 `c`（頂点平均）を計算
  - [ ] `t` を計算（extent 0 回避 + clamp）
  - [ ] `p_eff` を計算（base + dot(slope, t) を 0..1 clamp）
- [ ] 既存の loop に組み込み、`rng.random() < p_eff` を cond に反映

### 3) テスト更新（+ 追加）

- [ ] `tests/core/effects/test_drop.py` を更新（引数名/型の変更）
  - [ ] deterministic: `probability_base=0.5, probability_slope=(0,0,0), seed=42`
  - [ ] clamp: `probability_base<0` は noop、`probability_base>1` は全 drop（line/face の既存期待に合わせる）
  - [ ] non-finite: `probability_base=nan/inf` は noop（impl 直呼びテスト）
  - [ ] face: `by="face", probability_base=1.0` で「faces は全 drop / lines は残る」
- [ ] 追加テスト: 位置勾配が効く最小例
  - [ ] x=-1 と x=+1 にそれぞれ line/face を置く primitive を用意
  - [ ] `probability_base=0.5, probability_slope=(0.5,0,0)` で
    - x=- 側は `p_eff=0`（drop されない）
    - x=+ 側は `p_eff=1`（必ず drop）
    を確認

### 4) stubs 再生成

- [ ] `python -m tools.gen_g_stubs` を実行し、`src/grafix/api/__init__.pyi` を更新
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py` を通す

### 5) 最小の検証コマンド

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_drop.py`
- [ ] `ruff check src/grafix/core/effects/drop.py tests/core/effects/test_drop.py`
- [ ] `mypy src/grafix/core/effects/drop.py`

## 事前確認したい点（この仕様で進めてよい？）

- 代表点は「頂点平均（centroid）」で良い？（長い polyline が中心を跨ぐ場合は、中心寄りに評価される）
- `p_eff` の合成は `base + dot(slope, t)`（3軸の線形和）で良い？（別案: `max` や `1-Π(1-p_axis)`）

