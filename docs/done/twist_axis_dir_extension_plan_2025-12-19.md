# twist の回転軸（axis_dir）拡張 実装計画（2025-12-19）

## どこで

- 対象 effect: `src/grafix/core/effects/twist.py`
- 対象テスト: `tests/core/effects/test_twist.py`
- （必要なら）仕様メモ: `twist_effect_migration_checklist_2025-12-18.md`

## 何を

既存の `twist`（axis が `"x"|"y"|"z"` の排他選択）を、**回転軸の向きをベクトルで指定できる**ように拡張する（案 A）。

## なぜ

- `"x"|"y"|"z"` だと軸の向きを連続的に調整できない
- GUI/CC での操作も含めると、`axis_dir: vec3` は現状の ParamMeta/GUI 型と整合しやすい

---

## 仕様案（案 A）

### 公開パラメータ（twist）

- `auto_center: bool`（既存）
- `pivot: vec3`（既存）
- `angle: float`（degree、既存）
- `axis_dir: vec3`（新規、回転軸の方向）

### 意味

- 回転軸は「`axis_dir` に平行で、`pivot`（または auto_center の中心）を通る直線」。
- ねじり強度の分布（t）は「全頂点の `axis_dir` 方向への射影値 s の min/max」で正規化して決める。
  - `k = normalize(axis_dir)`
  - `s_i = dot(p_i, k)`（平行移動しても範囲 rng は不変なので pivot は不要）
  - `t_i = (s_i - min(s)) / (max(s) - min(s))`
  - `twist_rad_i = (t_i - 0.5) * 2 * deg2rad(angle)`
- 座標回転は任意軸回り回転（Rodrigues）で行う。
  - `v = p - center`
  - `v_rot = v*cosθ + (v×k)*sinθ + k*(k·v)*(1-cosθ)`（row-vector 規約）
  - `p_rot = center + v_rot`

### no-op / 例外

- `inputs` なし → 空ジオメトリ
- `coords` 空 → no-op
- `angle == 0` → no-op
- `rng <= 1e-9` → no-op
- `||axis_dir|| <= 1e-9` → **ValueError**（決定不能）

---

## 仕様確定（あなたの確認が必要）

- [x] `axis`（`"x"|"y"|"z"`）を **削除して** `axis_dir` のみにする（破壊的だが最もシンプル）；OK
  - 代案: `axis` を残す場合、`axis_dir` と両立の優先順位/モード切替が必要（GUI 表示も複雑化）
- [x] `axis_dir` の UI レンジ: `ui_min=-1, ui_max=1`（方向ベクトル用途として妥当）;OK
- [x] `axis_dir` の正規化は effect 内で行い、**GeometryId の正規化（=同一直線方向の別スケール統一）はしない**（実装は単純だがキャッシュは分散し得る）；OK

---

## 作業チェックリスト（実装手順）

- [x] API/メタ定義
  - [x] `twist_meta` に `axis_dir: ParamMeta(kind="vec3", ui_min=-1.0, ui_max=1.0)` を追加
  - [x] `axis` を削除（`axis_dir` のみに統一）
  - [x] `twist()` シグネチャを更新（`axis_dir` を受け取る）
- [x] 実装（任意軸回転）
  - [x] `k = axis_dir / ||axis_dir||` を作る（||k|| が小さい場合は ValueError）
  - [x] `center` を `auto_center/pivot` で決める（既存の rotate/scale と同様）
  - [x] `s = coords @ k`（float64）で射影
  - [x] `t` と `twist_rad` を計算
  - [x] Rodrigues を **ベクトル化**して計算（for ループにしない）
  - [x] `RealizedGeometry(coords=float32, offsets=base.offsets)` を返す
- [x] テスト更新/追加（`tests/core/effects/test_twist.py`）
  - [x] `axis_dir=(0,1,0)` が旧 `axis="y"` 相当の結果になることを固定
  - [x] `pivot` が回転中心に効くことを固定
  - [x] `auto_center=True` が `pivot=mean(coords)` と一致することを固定
  - [x] `axis_dir=(0,0,0)` が例外になることを固定（`realize()` は `RealizeError` にラップされる点も含む）
  - [x] `axis_dir` と `-axis_dir` で出力が一致することを固定（方向反転の同値性）
- [ ] 検証
  - [x] `PYTHONPATH=src pytest -q tests/core/effects/test_twist.py`
  - [ ] `ruff check src/grafix/core/effects/twist.py tests/core/effects/test_twist.py`（ruff が未導入で未実行）
  - [ ] `mypy src/grafix`（mypy が未導入で未実行）

---

## 追加メモ（設計上の注意）

- `axis_dir` のスケールを無視して正規化するため、`axis_dir=(0,1,0)` と `(0,2,0)` は同じ見た目になるが GeometryId は別になる（キャッシュが分散し得る）。
  - これを避けるには「解決段階で axis_dir を正規化して canonical な値にする」仕組みが必要（resolver 側の拡張になるので今回は避けるのが無難）。
