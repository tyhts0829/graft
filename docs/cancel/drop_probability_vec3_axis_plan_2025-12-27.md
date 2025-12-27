# drop_probability_vec3_axis_plan_2025-12-27.md
#
# どこで: `src/grafix/core/effects/drop.py`（+ stubs/tests）。
#
# 何を: drop effect の `probability` を `vec3` 化し、線/面の“向き”に応じて drop/keep の確率を変える。
#
# なぜ: X/Y/Z 方向に沿った要素密度を、直感的に（軸ごとのつまみで）調整できるようにするため。
#

## ゴール

- `E.drop(probability=...)` の `probability` を `vec3` として扱える（GUI/CC でも軸ごとに制御できる）。
- 「向き」ベースの確率:
  - by="line": ポリラインの軸方向成分に応じて `p_eff` が変わる
  - by="face": 面法線の軸方向成分に応じて `p_eff` が変わる
- `seed` で決定的（同じ入力/引数なら同じ結果）。

## 非ゴール

- 旧 `probability: float` 互換を保つラッパー/シムの追加（破壊的変更でよい）。
- 「位置（bbox 内の x/y/z 位置）」で確率が変わる機能（今回は“向き”のみ）。

## 仕様（採用案）

### パラメータ

- `probability: tuple[float, float, float] = (0.0, 0.0, 0.0)`
  - `probability = (px, py, pz)`（各成分は 0..1 を想定）
  - 各成分は **個別に** 0..1 にクランプ
  - 非有限（NaN/inf）は 0.0 とみなす（= その軸は無効）
- 有効判定:
  - `max(px, py, pz) == 0.0` のとき probability 条件は無効（rng も作らない）

### 有効確率 `p_eff`

線/面ごとに、軸方向の重み `w = (wx, wy, wz)`（0..1、合計 1）を計算し、

- `p_eff = wx * px + wy * py + wz * pz`

とする（`w` の合計が 1 のため `p_eff` は 0..1 に収まる）。

### 軸方向重み `w`

#### by="line"（ポリライン）

ポリラインの各セグメント差分 `diff = v[i+1] - v[i]` を使い、軸ごとの“移動量”を

- `mx = sum(abs(diff.x))`
- `my = sum(abs(diff.y))`
- `mz = sum(abs(diff.z))`

として `m = (mx, my, mz)` を得る。`m_sum = mx + my + mz` として、

- `m_sum > 0`: `w = m / m_sum`
- `m_sum == 0`（全点同一等）: `w = (1/3, 1/3, 1/3)`（向きが定義不能なので均等）

#### by="face"（面）

リング（頂点数>=3）を polygon と見なし法線 `n` を計算する（Newell 法を採用）。
`a = abs(n)`、`a_sum = ax + ay + az` として、

- `a_sum > 0`: `w = a / a_sum`
- `a_sum == 0`（退化）: `w = (1/3, 1/3, 1/3)`

### 既存条件との合成

現状どおり「条件は OR（union）」で `cond` を作る:

- interval 条件
- length 条件
- probability 条件（`rng.random() < p_eff`）

そのうえで `keep_mode` により `cond` の扱いを反転する。

### 乱数消費の決定性

- by="line":
  - 旧仕様コメントどおり「他条件の有無で結果が変わらない」よう、probability が有効な場合は **全 line で 1 回** `rng.random()` を消費する。
- by="face":
  - face 判定対象（頂点数>=3）に対して 1 回 `rng.random()` を消費する（現仕様踏襲）。

## 実装チェックリスト

### 1) drop effect を vec3 probability に変更

- [ ] `src/grafix/core/effects/drop.py` の `drop_meta["probability"]` を `ParamMeta(kind="vec3", ui_min=0.0, ui_max=1.0)` に変更
- [ ] `src/grafix/core/effects/drop.py:drop()` のシグネチャを `probability: tuple[float, float, float] = (0.0, 0.0, 0.0)` に変更
- [ ] docstring の `probability` 説明を更新（向きベース / (px,py,pz)）

### 2) 向き → 重み `w` の計算を実装

- [ ] `src/grafix/core/effects/drop.py` に小さな純関数ヘルパを追加
  - [ ] by="line": セグメント差分から `w` を計算（`sum(abs(diff))` 方式）
  - [ ] by="face": Newell 法で法線を計算して `w` を得る
- [ ] `w` の fallback（合計 0 のとき 1/3 均等）を入れる

### 3) probability 判定を `p_eff` に置き換え

- [ ] `src/grafix/core/effects/drop.py` の `eff_prob: float` まわりを撤去
- [ ] `(px, py, pz)` を個別にクランプし、`probability_enabled` を導入
- [ ] loop 内で `p_eff` を計算し、`rng.random() < p_eff` を `cond` に反映
- [ ] by="line" の「全行 rng 消費」挙動を維持

### 4) テスト更新（+ 追加）

- [ ] `tests/core/effects/test_drop.py` の引数を vec3 に更新
  - [ ] deterministic: `probability=(0.5, 0.5, 0.5)`（`p_eff` 一定化）に変更
  - [ ] clamp: `(-0.25, 0.0, 0.0)` は noop、`(2.0, 2.0, 2.0)` は全 drop を確認
  - [ ] non-finite: `(nan, 0.0, 0.0)` / `(inf, 0.0, 0.0)` が noop を確認
  - [ ] face probability: `probability=(1.0, 1.0, 1.0)` で「faces は全 drop / lines は残る」を確認
- [ ] 追加テスト: 軸方向制御が効くことの最小例
  - [ ] x 方向 line と y 方向 line を持つジオメトリを用意
  - [ ] `probability=(1,0,0)` で x line が必ず対象、y line は必ず対象外（`p_eff` が 1/0 になる）を確認

### 5) stubs 再生成

- [ ] `python -m tools.gen_g_stubs` を実行し、`src/grafix/api/__init__.pyi` を更新
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py` を通す

### 6) 最小の検証コマンド

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_drop.py`
- [ ] `ruff check src/grafix/core/effects/drop.py tests/core/effects/test_drop.py`
- [ ] `mypy src/grafix/core/effects/drop.py`

## 確認したい点（この計画で進めてよい？）

- `by="line"` の軸重みは「`sum(abs(diff))` の正規化」で良い？（折り返し/閉曲線でも破綻しにくい前提）
- `by="face"` の法線は Newell 法で良い？（退化時は 1/3 均等 fallback）

