# plan_effect_displace_port.md

どこで: `src/effects/displace.py`（追加予定）/ `src/api/effects.py`（登録 import 追加予定）/ `tests/test_displace.py`（追加予定）。
何を: 旧 `src/effects/from_previous_project/displace.py` の Perlin ノイズ変位を、現行の `@effect` + `RealizedGeometry` 仕様に合わせて移植する。
なぜ: 現行は「Geometry はレシピ」「実体変換は realize 時」という責務分離になっており、旧実装（Geometry 配列を直接加工）をそのまま使えないため。

---

## ゴール

- `E.displace(...)(g)` が使え、`realize()` で Perlin ノイズ変位が適用される。
- Parameter GUI/CC が引数を発見・解決できる（`ParamMeta` 提供、defaults あり）。
- 同一入力/同一引数で決定的に同じ出力になる（乱数なし）。
- 空入力・変位 0 は no-op（不要なコピーはしない）。

## 非ゴール

- 旧プロジェクト由来の `common.*` / `util.constants` 互換レイヤの実装（シム/ラッパーは作らない）。
- 旧 `__param_meta__` 形式の維持（現行は `ParamMeta` に統一する）。
- 別ノイズ実装の導入（依存追加はしない）。

---

## 先に確定したい仕様（確認項目）

- [ ] 実装場所
  - [ ] 案A: `src/effects/displace.py` を新規に作り、旧ファイルは参照用に残す（推奨）
  - [ ] 案B: 旧 `src/effects/from_previous_project/displace.py` を上書きして現仕様化し、そのまま登録する
- [ ] 引数名（API/GUI 表示）
  - [ ] 案A: 旧名維持（`amplitude_mm`, `spatial_freq`, `t_sec` など）
  - [ ] 案B: 現行寄せ（`amplitude`, `frequency`, `t` など）
- [ ] `amplitude` / `spatial_freq` の入力型
  - [ ] 案A: `vec3`（`tuple[float, float, float]`）のみ
  - [ ] 案B: `float | vec3` を許可（float は等方として (v,v,v) に展開）
- [ ] 勾配・係数の取り扱い（旧は係数の clamp を含む）
  - [ ] 案A: 旧挙動を踏襲（負値や過大値は内部で抑制）
  - [ ] 案B: クランプ最小化（入力は GUI レンジに委ね、仕様として“正しい値が来る”前提）

---

## 実装チェックリスト

- [ ] `src/effects/displace.py`（新規）
  - [ ] ファイルヘッダ（どこで/何を/なぜ）を付与
  - [ ] `displace_meta: dict[str, ParamMeta]` を定義（`vec3/float/bool` と UI レンジ）
  - [ ] Perlin ノイズの定数（perm/grad3）をモジュール内に同梱（`util.constants` 参照は削除）
  - [ ] 旧実装の Perlin コアを「入力: `coords(float32, Nx3)` / 出力: `new_coords(float32, Nx3)`」の純関数として整理
  - [ ] `@effect(meta=displace_meta)` で `displace(inputs: Sequence[RealizedGeometry], *, ...) -> RealizedGeometry` を実装
    - [ ] `inputs` が空なら empty を返す（`coords=(0,3)`, `offsets=(1,)`）
    - [ ] `coords` が空なら base を返す
    - [ ] `amplitude` が実質 0 なら base を返す
    - [ ] `offsets` は入力のものをそのまま保持し、`coords` のみ新規生成する
    - [ ] 返り値は `RealizedGeometry(coords=new_coords, offsets=base.offsets)`
- [ ] `src/api/effects.py`
  - [ ] 登録のための import を追加（例: `from src.effects import displace as _effect_displace`）
- [ ] `tests/test_displace.py`（新規）
  - [ ] テスト用 primitive を定義（短いポリラインの固定座標）
  - [ ] no-op: `amplitude=(0,0,0)` で `coords` が不変、`offsets` も不変
  - [ ] 変位: `amplitude>0` で少なくとも 1 点が変化し、shape/dtype が維持される
  - [ ] 決定性: 同一 params で 2 回 `realize()` して同一結果（`assert_allclose`）
  - [ ] `t_sec` を変えると出力が変わる（位相が進むことの確認）
- [ ] 仕上げ（対象限定）
  - [ ] `ruff check src/effects/displace.py tests/test_displace.py`
  - [ ] `pytest -q tests/test_displace.py`

---

## 片付け（任意）

- [ ] 旧 `src/effects/from_previous_project/displace.py` の扱いを決める
  - [ ] 残す（参照用） / `docs/done/` に移す / 削除（混乱防止） のどれにするか

