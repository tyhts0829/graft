# line primitive（旧 `from_previous_project`）を現仕様へ移植する計画

## 目的

`src/primitives/from_previous_project/line.py` を、現行の primitive 仕様（`@primitive` + `RealizedGeometry` + `ParamMeta`）に合わせて作り直し、`G.line(...)` として利用できる状態にする。

## 現状

- 旧実装は `engine.core.geometry.Geometry` / `.registry.shape` / `Geometry.from_lines` / `__param_meta__` 前提で、現行の `src/core/*`（DAG + `RealizedGeometry`）と整合しない。
- `src/api/primitives.py` は `src/primitives/*` を明示 import して登録しているため、`from_previous_project` 配下を直接書き換えても `G.line` として公開されない。
- 以前の `spec.md` では `G.line(p0=(0,0), p1=(10,0))` の例があったが、現行の Parameter GUI は `vec3`（長さ 3）を前提にしている。

## ゴール（完了条件）

- `src/primitives/line.py` が現行流儀で実装されている（モジュールヘッダ + NumPy style docstring + 型）。
- `G.line(**params)` で `Geometry(op="line", ...)` を生成でき、`realize()` で `RealizedGeometry(coords, offsets)` が得られる。
- `tests/test_line.py` が追加され、代表ケースが通る。

## 非ゴール

- `p0/p1` で線分を指定する API は実装しない（必要なら別タスク）。
- 線幅・色・破線など描画スタイルは Layer 側の責務として扱い、primitive では扱わない。
- `vec2` 型の新設はこの作業に含めない（必要なら別タスク）。

## 事前確認したいこと（仕様の確定が必要）

- [x] 公開 API の引数設計
  - `center: vec3`, `length: float`, `angle: float`（旧 `length/angle` に `center` を追加）
- [x] 正規化（デフォルト形状）の方針
  - 長さ 1 で原点中心（`p0=(-0.5,0,0), p1=(0.5,0,0)`）
- [x] 入力次元の扱い
  - `center` は vec3（長さ 3）を必須とする。
  - 線分は XY 平面で生成し、z は `center[2]` を両端点へ適用する。
  - `spec.md` の `G.line(...)` 例はこの仕様に合わせて更新する。
- [x] 退化ケース（`p0==p1`）の扱い
  - `length==0` のとき `p0==p1==center` の 2 点を返す（空にはしない）。
- [x] ファイル配置
  - `src/primitives/line.py` を新設し、旧ファイルは参照用に残す。

## 実装チェックリスト（OK をもらったらここから着手）

- [x] 仕様確定：上の「事前確認」項目を確定する（確定内容をこの md に追記）
- [x] 既存コード調査：`circle/polygon/polyhedron` の実装パターン（meta/offsets/エラー方針）を再確認
- [x] `src/primitives/line.py` を新規作成して現行スタイルへ統一
  - [x] ファイル先頭のヘッダ（どこで/何を/なぜ）を追加
  - [x] `line_meta = {...}` を `ParamMeta` で定義
  - [x] `@primitive(meta=line_meta)` で `line(...) -> RealizedGeometry` を実装
  - [x] 返り値の不変条件を満たす（`coords: float32 (N,3)`, `offsets: int32 (M+1,)`, `offsets[0]==0`, `offsets[-1]==N`）
- [x] line 生成の実装方針（center/length/angle）
  - [x] `center` を `(cx, cy, cz)` に展開し、float 化する（長さ不正は `ValueError`）
  - [x] `theta = deg2rad(angle)` として `dx, dy` を算出し、`p0/p1` を生成する
  - [x] `coords = np.array([p0, p1], dtype=np.float32)` を生成
  - [x] `offsets = np.array([0, 2], dtype=np.int32)` を生成
- [x] `src/api/primitives.py` に `from src.primitives import line as _primitive_line  # noqa: F401` を追加（公開）
- [x] `spec.md` の `G.line(...)` 例を確定仕様に合わせて修正
- [x] テスト追加：`tests/test_line.py`
  - [x] `realize(Geometry.create("line"))` が例外なく動く（登録と評価の疎通）
  - [x] `coords.shape==(2,3)` と `offsets == [0,2]` を検証
  - [x] `angle` の回転が反映されることを検証
  - [x] `center` の平行移動と z 固定を検証
  - [x] `length==0` のとき 2 点（同一点）になることを検証
- [x] 実行確認（対象限定）
  - [x] `pytest -q tests/test_line.py`
  - [ ] `ruff check src/primitives/line.py tests/test_line.py`（この環境では `ruff` 未導入）

## 追加で気づいたこと（作業中に追記）

- 現状の Parameter GUI は `vec3` を前提にしているため、座標入力は vec3 に統一する。
