# grid primitive（旧 `from_previous_project`）を現仕様へ移植する計画

## 目的

`src/primitives/from_previous_project/grid.py` を、現行の primitive 仕様（`@primitive` + `RealizedGeometry` + `ParamMeta`）に合わせて作り直し、`G.grid(...)` として利用できる状態にする。

## 現状

- 旧実装は `engine.core.geometry.Geometry` / `shape` デコレータ前提で、このリポジトリの現構成と整合しない（`src/core/*` 系に移行済み）。
- `src/api/primitives.py` は `src/primitives/*` を明示 import して登録しているため、`from_previous_project` 配下を書き換えるだけでは公開 API に出てこない。

## ゴール（完了条件）

- `src/primitives/grid.py` が現行流儀で実装されている（モジュールヘッダ + NumPy style docstring + 型）。
- `G.grid(**params)` で `Geometry(op="grid", ...)` を生成でき、`realize()` で `RealizedGeometry(coords, offsets)` が得られる。
- `tests/test_grid.py` が追加され、代表ケースが通る。

## 非ゴール

- `from_previous_project` 配下の他 primitive（line/sphere/torus/text）の移植はこの作業に含めない。
- グリッドの「太線」「破線」「クリップ」など描画スタイルは Layer 側の責務として扱い、primitive では扱わない。

## 事前確認したいこと（仕様の確定が必要）

- [x] `nx`/`ny` の意味をどちらにするか
  - 採用: 案 A（「線の本数」。`nx` 本の縦線 + `ny` 本の横線）
  - 案 B: 「セル分割数」（`nx+1` 本の縦線 + `ny+1` 本の横線、のような解釈）
- [x] 範囲の定義（正規化空間）
  - 採用: `x,y ∈ [-0.5, 0.5]` を基本とし、境界線を含める（旧実装踏襲）
- [x] 変換パラメータを入れるか
  - 採用: 案 A（`center: vec3` と `scale: vec3` を追加）
  - 案 B: 追加しない（最小の `nx, ny` のみにする）
- [x] 公開の仕方
  - 採用: 案 A（`src/primitives/grid.py` を新設し、`src/api/primitives.py` に import を追加して `G.grid` を有効化）
  - 案 B: まず実装だけ追加し、API 露出は後回し

## 実装チェックリスト（OK をもらったらここから着手）

- [x] 仕様確定：上の「事前確認」項目を確定する（確定内容をこの md に追記）
- [x] 既存コード調査：`circle/polygon/polyhedron` の実装パターン（meta/offsets/エラー方針）を再確認
- [x] `src/primitives/grid.py` を新規作成（または移動）して現行スタイルへ統一
  - [x] ファイル先頭の 3 行ヘッダ（どこで/何を/なぜ）を追加
  - [x] `grid_meta = {...}` を `ParamMeta` で定義（UI レンジもここで決める）
  - [x] `@primitive(meta=grid_meta)` で `grid(...) -> RealizedGeometry` を実装
  - [x] 返り値の不変条件を満たす（`coords: float32 (N,3)`, `offsets: int32 (M+1,)`, `offsets[0]==0`, `offsets[-1]==N`）
- [x] グリッド生成の実装方針
  - [x] `np.linspace(-0.5, 0.5, nx)` / `np.linspace(-0.5, 0.5, ny)` を基本にする（境界含めるなら endpoint=True）
  - [x] 縦線: 各線は 2 点（(x,-0.5,0)→(x,0.5,0)）
  - [x] 横線: 各線は 2 点（(-0.5,y,0)→(0.5,y,0)）
  - [x] `coords` は `np.concatenate` 等で一括生成し、`offsets` は 2 ずつ増える等差配列で作る
  - [x] （`center/scale` を採用する場合）最後に座標へ適用（`coords = coords * scale_vec + center_vec`）
- [x] `src/api/primitives.py` に `from src.primitives import grid as _primitive_grid  # noqa: F401` を追加（公開する場合）
- [x] テスト追加：`tests/test_grid.py`
  - [x] 最小ケース（例: `nx=1, ny=1`）で lines 数・offsets を検証
  - [x] 代表ケース（例: `nx=3, ny=2`）で `coords.shape==(2*(nx+ny),3)` と `offsets == [0,2,4,...]` を検証
  - [x] （変換パラメータ採用時）`center/scale` の反映を検証
- [ ] 実行確認（対象限定）
  - [x] `pytest -q tests/test_grid.py`
  - [ ] `ruff check src/primitives/grid.py tests/test_grid.py`（この環境では ruff 未導入）

## 追加で気づいたこと（作業中に追記）

- `nx=0` または `ny=0` は許容し、両方 0 の場合は空ジオメトリ（coords=(0,3), offsets=[0]）を返す。
