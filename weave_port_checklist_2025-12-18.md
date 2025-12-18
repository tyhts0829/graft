# weave effect 移植チェックリスト（旧 → 新プロジェクト）

## 目的

- 旧実装 `src/grafix/core/effects/from_previous_project/weave.py` を参考にしつつ、新プロジェクト用の `src/grafix/core/effects/weave.py` を新規実装する。
- 基本方針は **旧仕様踏襲**（入出力、パラメータ、クランプ、アルゴリズム）とする。

## 旧仕様（踏襲する要点）

- 入力ポリラインごとに:
  - XY 平面へ射影 → ウェブ生成（候補線+交点分割+弾性緩和）→ 元の 3D 姿勢へ復元 → 出力に合成。
- パラメータ:
  - `num_candidate_lines`: 0–500 にクランプ（整数）
  - `relaxation_iterations`: 0–50 にクランプ（整数）
  - `step`: 0.0–0.5 にクランプ（float）
- 乱数は擬似乱数（sin/fract）だが、シード固定で **決定論的**。

## 実装チェックリスト

### 1) 調査（移植先の前提確認）

- [ ] `RealizedGeometry`（coords/offsets）で旧 `Geometry.from_lines` 相当の出力構築方法を決める（例: `_lines_to_realized` を内蔵）。
- [ ] 新プロジェクトの effect I/F（`@effect(meta=...)` / `ParamMeta` / `inputs: Sequence[RealizedGeometry]`）に合わせる。
- [ ] 平面変換は `src/grafix/core/effects/util.py` の `transform_to_xy_plane` / `transform_back` で置換できることを確認する。
- [ ] `src/grafix/core/effects/AGENTS.md` の制約（他 effect モジュールへ依存しない、冒頭 docstring 形式）を守る方針を確定する。

### 2) 実装（新規ファイル追加）

- [ ] `src/grafix/core/effects/weave.py` を新規追加（モジュール先頭は「この effect が何をするか」の説明のみ）。
- [ ] `weave_meta` を `ParamMeta` で定義し、`@effect(meta=weave_meta)` を付与する。
- [ ] `weave(inputs, *, num_candidate_lines=..., relaxation_iterations=..., step=...) -> RealizedGeometry` を実装する。
- [ ] 旧コードの内部クランプ（上限/下限）を同等に移植する。
- [ ] 各ポリライン（offsets 区間）ごとに旧 `_webify_single_polygon` 相当を適用する。
- [ ] 出力ポリライン列を `RealizedGeometry` にまとめる（dtype=float32, offsets=int32）。
- [ ] 入力が空 / ポリラインが短い（<3 点）ケースの挙動を旧実装と同等にする。

### 3) 統合（登録）

- [ ] `src/grafix/api/effects.py` に `weave` の import を追加し、`E.weave(...)` で呼べるようにする。

### 4) テスト（最小セット）

- [ ] `tests/core/effects/test_weave.py` を追加する。
- [ ] 空入力が noop である（coords=(0,3), offsets=[0]）。
- [ ] `num_candidate_lines=0` かつ `relaxation_iterations=0` で「ほぼ noop」（入力と同一、または許容誤差内）になる。
- [ ] クランプ確認: 負値/上限超の入力でも例外なく動き、結果が有限値になる。
- [ ] `num_candidate_lines>=1` で出力が入力より増える（例: coords 数または polyline 数が増加）ことを確認する。

### 5) ローカル検証コマンド（対象限定）

- [ ] `ruff check src/grafix/core/effects/weave.py tests/core/effects/test_weave.py`
- [ ] `mypy src/grafix/core/effects/weave.py`
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_weave.py`

## 事前に確認したい点（あなたの返答待ち）

- [ ] `weave` を API として公開してよい？（= `src/grafix/api/effects.py` へ import 追加）
- [ ] 入力ポリラインは「閉じ（先頭点の重複あり）」を標準として扱ってよい？（例: `G.polygon()` 形式）
- [ ] 旧仕様どおり、弾性緩和で **交点として追加された境界上ノードが動く** 挙動は維持でよい？

