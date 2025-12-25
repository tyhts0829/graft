# plan_effect_displace_port.md

どこで: `src/effects/displace.py`（追加済み）/ `src/api/effects.py`（登録 import 追加済み）/ `tests/test_displace.py`（追加済み）。
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

基本的に旧仕様踏襲してください。頑張って最適化したコードなので。

- [x] 実装場所
  - [x] 案 A: `src/effects/displace.py` を新規に作り、旧ファイルは参照用に残す（推奨）；こちらで
  - [ ] 案 B: 旧 `src/effects/from_previous_project/displace.py` を上書きして現仕様化し、そのまま登録する
- [x] 引数名（API/GUI 表示）
  - [x] 案 A: 旧名維持（`amplitude`, `spatial_freq`, `t` など）；旧仕様踏襲
  - [ ] 案 B: 現行寄せ（`amplitude`, `frequency`, `t` など）
- [x] `amplitude` / `spatial_freq` の入力型
  - [x] 案 A: `vec3`（`tuple[float, float, float]`）のみ；こちらで
  - [ ] 案 B: `float | vec3` を許可（float は等方として (v,v,v) に展開）
- [x] 勾配・係数の取り扱い（旧は係数の clamp を含む）
  - [x] 案 A: 旧挙動を踏襲（負値や過大値は内部で抑制）；旧仕様踏襲
  - [ ] 案 B: クランプ最小化（入力は GUI レンジに委ね、仕様として“正しい値が来る”前提）

---

## 実装チェックリスト

- [x] `src/effects/displace.py`（新規）
  - [x] ファイルヘッダ（どこで/何を/なぜ）を付与
  - [x] `displace_meta: dict[str, ParamMeta]` を定義（`vec3/float/bool` と UI レンジ）
  - [x] Perlin ノイズの定数（perm/grad3）をモジュール内に同梱（`util.constants` 参照は削除）
  - [x] 旧実装の Perlin コアを「入力: `coords(float32, Nx3)` / 出力: `new_coords(float32, Nx3)`」の純関数として整理
  - [x] `@effect(meta=displace_meta)` で `displace(inputs: Sequence[RealizedGeometry], *, ...) -> RealizedGeometry` を実装
    - [x] `inputs` が空なら empty を返す（`coords=(0,3)`, `offsets=(1,)`）
    - [x] `coords` が空なら base を返す
    - [x] `amplitude` が実質 0 なら base を返す
    - [x] `offsets` は入力のものをそのまま保持し、`coords` のみ新規生成する
    - [x] 返り値は `RealizedGeometry(coords=new_coords, offsets=base.offsets)`
- [x] `src/api/effects.py`
  - [x] 登録のための import を追加（例: `from src.effects import displace as _effect_displace`）
- [x] `tests/test_displace.py`（新規）
  - [x] テスト用 primitive を定義（短いポリラインの固定座標）
  - [x] no-op: `amplitude=(0,0,0)` で `coords` が不変、`offsets` も不変
  - [x] 変位: `amplitude>0` で少なくとも 1 点が変化し、shape/dtype が維持される
  - [x] 決定性: 同一 params で 2 回 `realize()` して同一結果（`assert_allclose`）
  - [x] `t` を変えると出力が変わる（位相が進むことの確認）
- [x] 仕上げ（対象限定）
  - [ ] `ruff check src/effects/displace.py tests/test_displace.py`（手元環境に ruff が無いため未実施）
  - [x] `pytest -q tests/test_displace.py`

---

## 片付け（任意）

- [x] 旧 `src/effects/from_previous_project/displace.py` の扱いを決める
  - [x] 残す（参照用） / `docs/done/` に移す / 削除（混乱防止） のどれにするか
