# collapse effect（旧 `from_previous_project`）の現仕様ポーティング計画

対象: `src/effects/collapse.py`（旧: `src/effects/from_previous_project/collapse.py` は保持）

## 目的

- 旧実装（`engine.core.geometry.Geometry` + 旧 `registry`）の `collapse` を、現行の effect 仕様（`RealizedGeometry` + `src.core.effect_registry` + `ParamMeta`）に合わせて書き換え、`E.collapse(...)` として利用できる状態にする。

## 前提（現仕様の要点）

- effect 実装は `@effect(meta=...)` で登録し、シグネチャは `func(inputs: Sequence[RealizedGeometry], *, ...) -> RealizedGeometry` とする。
- Parameter GUI は `ParamMeta(kind=..., ui_min/ui_max=...)` を参照し、値は resolver 側で量子化される（effect 側での過剰なクランプは避ける）。
- `RealizedGeometry` は不変（`writeable=False`）として扱い、effect は入力配列を破壊しない。

## 仕様整理（旧 collapse の挙動）

- 各線分を `divisions=subdivisions` 個のサブセグメントに分割し、各サブセグメントを「線分方向に直交する平面」内のランダム方向へ `intensity` だけ平行移動する。
- 出力は「各サブセグメントが 2 点からなる独立ポリライン（非接続）」。
- 0 長/非有限の線分は、端点 2 点をそのまま 1 本のポリラインとして出力する。
- 乱数は `np.random.default_rng(0)` に固定（同一入力なら決定的）。

## 要確認（決めてから実装）

基本的に旧仕様踏襲してください。かなり頑張って最適化されているコードなので。

- [x] 出力の「非接続（2 点ポリラインの集合）」仕様は維持（旧仕様踏襲）
- [x] 乱数シードはパラメータ化しない（`np.random.default_rng(0)` 固定、旧仕様踏襲）
- [x] `subdivisions` はクランプしない（UI レンジ指定のみ、旧仕様踏襲）
- [x] 新規に `src/effects/collapse.py` を作って正規 effect に昇格（旧ファイルは保持）
- [x] Numba 経路も実装し、`collapse` は Numba 経路を使用（旧仕様踏襲）

## 実装チェックリスト

- [x] （設計）現行 effect 実装の最小パターン（`src/effects/scale.py` 等）に合わせた I/O とメタ定義を決める
- [x] （移植）`collapse` を `RealizedGeometry` ベースに書き換える
  - [x] `inputs` 空なら空ジオメトリを返す
  - [x] `base = inputs[0]` のみを対象（他 effect と整合）
  - [x] `intensity==0` または `subdivisions<=0` は no-op（入力をそのまま返す）
  - [x] 出力 `coords=float32`, `offsets=int32` を満たす（`RealizedGeometry` が最終検証）
- [x] （メタ）`collapse_meta = {"intensity": ParamMeta(...), "subdivisions": ParamMeta(...)}`
  - [x] `intensity`: `kind="float"`, `ui_min=0.0`, `ui_max=10.0`
  - [x] `subdivisions`: `kind="int"`, `ui_min=0`, `ui_max=10`
- [x] （内部実装）旧 `_collapse_numpy_v2` / Numba 経路をベースに、2-pass（count/fill）で生成する
  - [x] 乱数生成は `np.random.default_rng(0)` に固定
  - [x] EPS/非有限チェックの扱いを旧仕様踏襲
  - [x] Numba 経路（count + njit fill）を実装する
- [x] （登録）`E` から見えるように `src/api/effects.py` に import を追加する（`# noqa: F401` 方式）
- [x] （テスト）`tests/test_collapse.py` を追加する
  - [x] `subdivisions=0` / `intensity=0` が no-op になる
  - [x] 2 点線分 + `subdivisions=D` で「出力ポリライン本数 == D」かつ各線が 2 点になる
  - [x] 決定性（同一入力で出力が一致）
  - [x] 0 長線分の扱い（端点 2 点をそのまま出す）
- [x] （動作確認）最小の対象テストだけ回す: `pytest -q tests/test_collapse.py`

## 完了条件

- `E.collapse(...)` が AttributeError にならず、`realize()` で `RealizedGeometry` が得られる
- 追加したテストが通る
- meta があるため Parameter GUI に `intensity/subdivisions` が出現する（範囲は ui_min/ui_max）

## 実施結果

- 実装: `src/effects/collapse.py`
- 登録: `src/api/effects.py`（import 追加）
- テスト: `tests/test_collapse.py`（`pytest -q tests/test_collapse.py`）
