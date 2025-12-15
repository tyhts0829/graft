# sphere primitive（旧 `from_previous_project`）を現仕様へ移植する計画

## 目的

`src/primitives/from_previous_project/sphere.py` を、現行の primitive 仕様（`@primitive` + `RealizedGeometry` + `ParamMeta`）に合わせて作り直し、`G.sphere(...)` として利用できる状態にする。

## 現状

- 旧実装は `engine.core.geometry.Geometry` / `shape` デコレータ前提で、このリポジトリの現構成（`src/core/*`）と整合しない。
- `src/api/primitives.py` は `src/primitives/*` を明示 import して登録する方式のため、`from_previous_project` 配下を書き換えるだけでは公開 API（`G.*`）に出てこない。
- README では `G.sphere()` が例示されているが、現状は未実装（登録されていない）。

## ゴール（完了条件）

- `src/primitives/sphere.py` が現行流儀で実装されている（モジュールヘッダ + NumPy style docstring + 型）。
- `G.sphere(**params)` で `Geometry(op="sphere", ...)` を生成でき、`realize()` で `RealizedGeometry(coords, offsets)` が得られる。
- `tests/test_sphere.py` が追加され、代表ケースが通る。

## 非ゴール

- `from_previous_project` 配下の他 primitive（grid/line/torus/text）の移植はこの作業に含めない。
- 球の「陰影」「隠線処理」「Z ソート」等の見た目制御は行わない（本プロジェクトは線のポリライン列が主対象）。
- 旧 `sphere.__param_meta__` 互換のためのラッパーやシムは作らない（破壊的変更 OK 方針）。

## 事前確認したいこと（仕様の確定が必要）

- [x] 公開パラメータの方針（確定）
  - 案 A: `subdivisions`, `type_index`, `mode` の最小セット（旧実装の意図を保つ）
  - 案 B: 上に加えて `center: vec3`, `scale: vec3` を追加（`polygon/polyhedron` と揃える）【採用】
  - 案 C: `r`（半径）も追加（`circle` に合わせる）
- [x] `type_index`（または `style_index`）の中身（確定）
  - 案 A: 旧実装踏襲（`latlon/zigzag/icosphere/rings` の 4 種）【採用】
  - 案 B: もっと単純化（例: `latlon` と `rings` だけ、等）
- [x] `subdivisions` の扱い（範囲とポリシー）（確定）
  - 範囲外入力はクランプ【採用】
  - UI 推奨レンジ（`ParamMeta.ui_min/ui_max`）は `0..5`【採用】
- [x] 球の基準サイズ（確定）
  - 案 A: 半径 `0.5`（`polygon` と同じく正規化サイズ 1 を基本にする）【採用】
  - 案 B: 半径 `1.0`（`circle(r=1.0)` のデフォルトと揃える）

## 実装チェックリスト（OK をもらったらここから着手）

- [x] 仕様確定：上の「事前確認」項目を確定する（確定内容をこの md に追記）
- [x] 現行実装パターン確認：`circle/polygon/polyhedron` の meta/offsets/エラー方針を再確認
- [x] 実装方針の決定
  - [x] 旧 `*_sphere_*` 生成関数を「ポリライン列（list[np.ndarray]）」生成として整理する
  - [x] `RealizedGeometry` へ変換する共通処理（`coords` 連結 + `offsets` 構築）を用意する
  - [x] 不要なキャッシュは置かない（`realize_cache` があるため）
- [x] `src/primitives/sphere.py` を新規作成して現行スタイルへ統一
  - [x] ファイル先頭の 3 行ヘッダ（どこで/何を/なぜ）を追加
  - [x] `sphere_meta = {...}` を `ParamMeta` で定義（UI レンジ込み）
  - [x] `@primitive(meta=sphere_meta)` で `sphere(...) -> RealizedGeometry` を実装
  - [x] 返り値の不変条件を満たす（`coords: float32 (N,3)`, `offsets: int32 (M+1,)`, `offsets[0]==0`, `offsets[-1]==N`）
- [x] 形状生成（採用スタイル）
  - [x] `latlon`: 経度線（極 → 極の開ポリライン）＋緯度リング（閉ポリライン）を生成
  - [x] `rings`: 軸ごとのリング（閉ポリライン）を生成
  - [x] `zigzag`: 複数ストランドの螺旋ポリラインを生成
  - [x] `icosphere`: 辺の線分（2 点ポリライン）を生成（重複辺は除外）
- [x] `src/api/primitives.py` に `from src.primitives import sphere as _primitive_sphere  # noqa: F401` を追加して `G.sphere` を公開
- [x] テスト追加：`tests/test_sphere.py`
  - [x] 代表ケースで `realize()` が例外なく動き、`coords/offsets` の基本不変条件を満たすことを検証
  - [x] `type_index/subdivisions` のクランプ方針を検証（仕様確定に従う）
  - [x] `center/scale` が座標へ反映されることを検証
- [ ] 実行確認（対象限定）
  - [x] `pytest -q tests/test_sphere.py`
  - [ ] `ruff check src/primitives/sphere.py tests/test_sphere.py`（環境に ruff が無いため未実施）
- [ ] 後片付け（要方針決定）
  - [ ] `src/primitives/from_previous_project/sphere.py` を残す/削除/`docs/done` へ移動のどれにするか決める

## 追加で気づいたこと（作業中に追記）

- `ruff` が実行環境に入っていないため、lint は未実施（`pytest` は実施済み）。
