# twist effect 移植チェックリスト（2025-12-18）

# どこで: `twist_effect_migration_checklist_2025-12-18.md`。

# 何を: `src/grafix/core/effects/from_previous_project/twist.py`（旧 twist）を現行コア（RealizedGeometry/effect_registry/ParamMeta）へ移植するためのチェックリスト。

# なぜ: 旧プロジェクト依存（`engine.*` / 旧 registry / 旧 Geometry API）を排除し、`grafix.api.E.twist(...)` をこのリポジトリで利用可能にするため。

## ゴール

- `E.twist(angle=..., axis=...)(g)` が登録済み effect として利用できる。
- `realize()` によりねじり適用済み `RealizedGeometry` が得られる（offsets は保持）。
- Parameter GUI 用に `ParamMeta` が定義され、デフォルト値も観測される。
- 最小限のユニットテストで旧仕様が固定される。

## 旧仕様（`src/grafix/core/effects/from_previous_project/twist.py` から読み取れること）

- 位置依存ねじり（twist）:
  - 指定軸（x/y/z）の座標を min/max で正規化して `t∈[0,1]` を作る。
  - ねじれ角は `twist_rad = (t - 0.5) * 2 * max_rad`（中心 `t=0.5` は 0、端で `±max`）。
  - 軸回り回転を各頂点へ適用（y 軸なら x/z、x 軸なら y/z、z 軸なら x/y を回す）。
- パラメータ:
  - `angle`（degree 入力、`math.radians()` で rad に変換）
  - `axis`（`"x"|"y"|"z"`、不正は `"y"` にフォールバック）
- 軸方向の範囲（hi-lo）が 0 の場合は no-op。

## 新仕様へのマッピング（現行 grafix の規約）

- 入力: `Sequence[RealizedGeometry]`（通常 1 要素）。`inputs[0]` を対象にする。
- 出力: `RealizedGeometry(coords=float32 (N,3), offsets=int32 (M+1,))`。
- built-in effect は `@effect(meta=...)` が必須（`src/grafix/core/effect_registry.py` の制約）。
- `src/grafix/core/effects/` 配下は「各モジュールが独立」を保つ（`src/grafix/core/effects/AGENTS.md`）。

## 仕様確定（あなたの確認が必要）

- [ ] 公開引数名は旧仕様踏襲で `angle` / `axis` のままにする（`angle_deg` 等へは改名しない）。；はい
- [ ] `axis` 不正値は旧仕様踏襲で `"y"` にフォールバックする（例外にしない / no-op にしない）；raise で
- [ ] `angle` は旧仕様同様「degree 入力」とし、値域クランプはしない（`ParamMeta.ui_min/ui_max` は UI 範囲のみ）。；はい
- [ ] `rng <= 1e-9` のとき no-op（旧実装の閾値踏襲）。；はい
- [ ] 軸方向の min/max は「全頂点の一括 min/max」（ポリラインごとのリセットはしない）。；はい

## 作業チェックリスト

- [ ] 現状整理
  - [ ] 現行 effect 実装の書式/慣習を確認（例: `src/grafix/core/effects/rotate.py`, `src/grafix/core/effects/translate.py`）
  - [ ] 旧 `from_previous_project/twist.py` の no-op 条件と回転符号（右手系）を確認してメモ化
- [ ] 実装（新スタイルへ移植）
  - [ ] `src/grafix/core/effects/twist.py` を新規作成
    - [ ] 冒頭 docstring は「効果の説明」のみにする（effects 配下の規約）
    - [ ] `twist_meta = { "angle": ParamMeta(kind="float", ui_min=0.0, ui_max=360.0), "axis": ParamMeta(kind="choice", choices=("x","y","z")) }`
    - [ ] `@effect(meta=twist_meta)` で `def twist(inputs, *, angle=60.0, axis="y") -> RealizedGeometry` を実装
  - [ ] no-op:
    - [ ] `not inputs` は空ジオメトリを返す（他 effect と同様）
    - [ ] `base.coords.shape[0] == 0` は no-op（`base` を返す）
    - [ ] `rng <= 1e-9` は no-op（`base` を返す）
    - [ ] `angle == 0.0` は no-op（`base` を返す）
  - [ ] ねじり:
    - [ ] 軸インデックス選択（x=0,y=1,z=2）
    - [ ] `t=(coord-lo)/rng` → `twist_rad=(t-0.5)*2*max_rad`
    - [ ] 各軸ごとの回転（np.cos/np.sin をベクトルで計算、float64 計算 →float32 出力）
    - [ ] offsets は `base.offsets` をそのまま保持
- [ ] 登録
  - [ ] `src/grafix/api/effects.py` に `from grafix.core.effects import twist as _effect_twist  # noqa: F401` を追加
- [ ] テスト
  - [ ] `tests/core/effects/test_twist.py` を追加
    - [ ] y 軸 twist（`angle=90`）で端点が ±90° 回ることを固定（例: (1,0,0)->(0,0,-1), (1,1,0)->(0,1,1)）
    - [ ] 中央（t=0.5）の点は不変（ねじれ 0）を固定
    - [ ] `angle=0` が no-op を固定
    - [ ] 軸方向の範囲 0 が no-op を固定
    - [ ] 空ジオメトリが no-op を固定
    - [ ] offsets が保持されることを固定
- [ ] 検証
  - [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_twist.py`
  - [ ] `ruff check src/grafix/core/effects/twist.py tests/core/effects/test_twist.py`
  - [ ] `mypy src/grafix`
- [ ] 旧ファイルの扱い（破壊的変更を含むので要確認・今回は保留）
  - [ ] `src/grafix/core/effects/from_previous_project/twist.py` を削除/退避するか決める

## 追加で気づいた点（今回はやらないが、必要なら次）

- twist の “中心位置”（t=0.5 をどこに置くか）をユーザー指定したい場合は、`pivot` / `center_mode` のような追加パラメータが必要。
- “軸” をベクトル指定に拡張（任意軸回りのねじり）する場合は、回転の基底を組む処理が必要（現状は xyz のみ）。
