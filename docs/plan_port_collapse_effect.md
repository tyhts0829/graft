# collapse effect（旧 `from_previous_project`）の現仕様ポーティング計画

対象: `src/effects/from_previous_project/collapse.py`

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

- [ ] 出力の「非接続（2点ポリラインの集合）」仕様は現プロジェクトでも維持する？（維持なら旧挙動踏襲で最短）
- [ ] 乱数シードを `seed: int = 0` としてパラメータ化する？（`GeometryId` に入るので決定性は維持しつつ見た目を変えられる）
- [ ] `subdivisions` の上限を設ける？（GUI はレンジ指定のみでクランプしない前提。必要なら最低限の安全策だけ入れる）
- [ ] 配置: 新規に `src/effects/collapse.py` を作って正規 effect に昇格する？それとも既存パスのまま現仕様へ書き換えて `src/api/effects.py` から import する？
- [ ] Numba 経路は当面捨てて NumPy のみでいく？（初期は NumPy のみがシンプル）

## 実装チェックリスト

- [ ] （設計）現行 effect 実装の最小パターン（`src/effects/scale.py` 等）に合わせた I/O とメタ定義を決める
- [ ] （移植）`collapse` を `RealizedGeometry` ベースに書き換える
  - [ ] `inputs` 空なら空ジオメトリを返す
  - [ ] `base = inputs[0]` のみを対象（他 effect と整合）
  - [ ] `intensity<=0` または `subdivisions<=0` は no-op（入力をそのまま返す）
  - [ ] 出力 `coords=float32`, `offsets=int32` を満たす（`RealizedGeometry` が最終検証）
- [ ] （メタ）`collapse_meta = {"intensity": ParamMeta(...), "subdivisions": ParamMeta(...)}`
  - [ ] `intensity`: `kind="float"`, `ui_min=0.0`, `ui_max=10.0`
  - [ ] `subdivisions`: `kind="int"`, `ui_min=0`, `ui_max=10`
  - [ ] （採用時）`seed`: `kind="int"`, `ui_min=0`, `ui_max=2**31-1`
- [ ] （内部実装）旧 `_collapse_numpy_v2` をベースに、`base.coords/base.offsets` から 2-pass（count/fill）で生成する
  - [ ] 乱数生成は `np.random.default_rng(seed)` で一元化（seed を入れない場合は 0 固定）
  - [ ] EPS/非有限チェックの扱いを現仕様で再確認（最低限の分岐に留める）
- [ ] （登録）`E` から見えるように `src/api/effects.py` に import を追加する（`# noqa: F401` 方式）
- [ ] （テスト）`tests/test_collapse.py` を追加する
  - [ ] `subdivisions=0` / `intensity=0` が no-op になる
  - [ ] 2点線分 + `subdivisions=D` で「出力ポリライン本数 == D」かつ各線が2点になる
  - [ ] 決定性（同一入力/同一 seed で出力が一致）
  - [ ] 0 長線分の扱い（端点2点をそのまま出す）
- [ ] （動作確認）最小の対象テストだけ回す: `pytest -q tests/test_collapse.py`

## 完了条件

- `E.collapse(...)` が AttributeError にならず、`realize()` で `RealizedGeometry` が得られる
- 追加したテストが通る
- meta があるため Parameter GUI に `intensity/subdivisions` が出現する（範囲は ui_min/ui_max）

