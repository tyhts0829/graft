# twist effect 移植チェックリスト（2025-12-18）

# どこで: `twist_effect_migration_checklist_2025-12-18.md`。

# 何を: `src/grafix/core/effects/from_previous_project/twist.py`（旧 twist）を現行コア（RealizedGeometry/effect_registry/ParamMeta）へ移植するためのチェックリスト。

# なぜ: 旧プロジェクト依存（`engine.*` / 旧 registry / 旧 Geometry API）を排除し、`grafix.api.E.twist(...)` をこのリポジトリで利用可能にするため。

## ゴール

- `E.twist(angle=..., axis_dir=...)(g)` が登録済み effect として利用できる。
- `realize()` によりねじり適用済み `RealizedGeometry` が得られる（offsets は保持）。
- Parameter GUI 用に `ParamMeta` が定義され、デフォルト値も観測される。
- 最小限のユニットテストで旧仕様が固定される。
- ねじり軸の回転中心を `auto_center/pivot` で指定できる。
- 回転軸の向きを `axis_dir` で連続的に調整できる。

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

- [x] 公開引数名は `angle` / `axis_dir` とする（`axis` は削除）。
- [x] `angle` は旧仕様同様「degree 入力」とし、値域クランプはしない（`ParamMeta.ui_min/ui_max` は UI 範囲のみ）。；はい
- [x] `rng <= 1e-9` のとき no-op（旧実装の閾値踏襲）。；はい
- [x] 軸方向の min/max は「全頂点の射影値 s の一括 min/max」（ポリラインごとのリセットはしない）。
- [x] `auto_center/pivot` を追加し、他 effect と同様に `auto_center=True` をデフォルトとする。
- [x] `auto_center=True` は頂点平均を回転中心に使用し、`auto_center=False` は `pivot` を使用する。
- [x] `axis_dir` の UI レンジは `ui_min=-1, ui_max=1` とする。
- [x] `axis_dir` の正規化は effect 内で行い、GeometryId の正規化（同一直線方向の統一）は行わない。
- [x] `axis_dir` がゼロベクトル相当の場合は `ValueError` を raise する。

## 作業チェックリスト

- [x] 現状整理
  - [x] 現行 effect 実装の書式/慣習を確認（例: `src/grafix/core/effects/rotate.py`, `src/grafix/core/effects/translate.py`）
  - [x] 旧 `from_previous_project/twist.py` の no-op 条件と回転式を確認
- [x] 実装（新スタイルへ移植）
  - [x] `src/grafix/core/effects/twist.py` を新規作成
    - [x] 冒頭 docstring は「効果の説明」のみにする（effects 配下の規約）
    - [x] `twist_meta` を定義（`auto_center/pivot/angle/axis_dir`）
    - [x] `@effect(meta=twist_meta)` で `def twist(inputs, *, auto_center=True, pivot=(0,0,0), angle=60.0, axis_dir=(0,1,0)) -> RealizedGeometry` を実装
  - [x] no-op:
    - [x] `not inputs` は空ジオメトリを返す（他 effect と同様）
    - [x] `base.coords.shape[0] == 0` は no-op（`base` を返す）
    - [x] `rng <= 1e-9` は no-op（`base` を返す）
    - [x] `angle == 0.0` は no-op（`base` を返す）
  - [x] ねじり:
    - [x] `axis_dir` を正規化して軸単位ベクトル `k` を作る（ゼロは例外）
    - [x] `s = dot(coords, k)` の min/max を使って `t` を計算し、`twist_rad=(t-0.5)*2*max_rad`
    - [x] 回転中心の決定（`auto_center` または `pivot`）
    - [x] Rodrigues による任意軸回転（row-vector 規約に合わせ `cross(v, k)` を使用）
    - [x] offsets は `base.offsets` をそのまま保持
- [x] 登録
  - [x] `src/grafix/api/effects.py` に `from grafix.core.effects import twist as _effect_twist  # noqa: F401` を追加
- [x] テスト
  - [x] `tests/core/effects/test_twist.py` を追加
    - [x] `axis_dir=(0,1,0)` で端点が ±90° 回ることを固定
    - [x] 中央（t=0.5）の点は不変（ねじれ 0）を固定
    - [x] `angle=0` が no-op を固定
    - [x] 軸方向の範囲 0 が no-op を固定
    - [x] 空ジオメトリが no-op を固定
    - [x] offsets が保持されることを固定
    - [x] `axis_dir=(0,0,0)` が例外になることを固定（`realize()` は `RealizeError` にラップされる）
    - [x] `pivot` が回転中心に反映されることを固定
    - [x] `auto_center=True` が `pivot=mean(coords)` と一致することを固定
    - [x] `axis_dir` と `-axis_dir` で結果が一致することを固定
- [ ] 検証
  - [x] `PYTHONPATH=src pytest -q tests/core/effects/test_twist.py`
  - [ ] `ruff check src/grafix/core/effects/twist.py tests/core/effects/test_twist.py`（ruff が未導入で未実行）
  - [ ] `mypy src/grafix`（mypy が未導入で未実行）
- [ ] 旧ファイルの扱い（破壊的変更を含むので要確認・今回は保留）
  - [ ] `src/grafix/core/effects/from_previous_project/twist.py` を削除/退避するか決める

## 追加で気づいた点（今回はやらないが、必要なら次）

- twist の “中心位置”（t=0.5 をどこに置くか）をユーザー指定したい場合は、`pivot` / `center_mode` のような追加パラメータが必要。
- `axis_dir` は正規化して使うため、`axis_dir=(0,1,0)` と `(0,2,0)` は見た目が同じだが GeometryId は別になり得る（キャッシュが分散し得る）。
