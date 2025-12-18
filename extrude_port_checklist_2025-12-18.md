# extrude 移植チェックリスト（2025-12-18）

目的: 旧プロジェクト実装 `src/grafix/core/effects/from_previous_project/extrude.py` を参照し、新プロジェクトの effect として `src/grafix/core/effects/extrude.py` を新規実装する（基本は旧仕様踏襲）。

注意: 互換ラッパー/シムは作らない。旧ファイルは参照として残し、必要なら最後に整理する（要確認）。

## 0) 事前に決める（あなたの確認が必要）

- [ ] 出力構成: 旧仕様どおり「元ライン群 + 押し出し後ライン群 + 各頂点の接続エッジ（2 点ポリライン）」をすべて出力する（no-op でも重複ラインが出る可能性を許容）。
- [ ] `center_mode` の不正値: 旧仕様どおり `center_mode == "auto"` のときだけ重心中心スケール、それ以外は `"origin"` 扱い（no-op にしない）。
- [ ] 細分化: 旧仕様どおり「各セグメントへ中点挿入」を `subdivisions` 回繰り返す（`subdivide` effect の停止条件/上限は流用しない）。
- [ ] 旧参照ファイルの扱い: 実装後に `from_previous_project/done/` へ移動する / しない（移動は Ask-first 扱い）。

## 1) 仕様の棚卸し（旧実装 → 新実装の対応表）

- [ ] 入出力: `Geometry`（旧）→ `RealizedGeometry`（新）
- [ ] effect 登録: 旧 `@effect()` + `__param_meta__` → 新 `@effect(meta=...)` + `ParamMeta`
- [ ] パラメータ（旧仕様踏襲）:
  - `direction: vec3`（押し出し方向）
  - `distance: float`（0–200mm にクランプ）
  - `scale: float`（0–3 にクランプ）
  - `subdivisions: int`（0–8 にクランプ）
  - `center_mode: {"origin","auto"}`（スケール中心）
- [ ] 退化エッジ: 接続エッジは `np.allclose(..., atol=1e-8)` でゼロ長をスキップ

## 2) 新規ファイル実装（`src/grafix/core/effects/extrude.py`）

- [ ] ファイル先頭: 簡潔な日本語 docstring（旧仕様踏襲の要点、パラメータ、注意点）
- [ ] `extrude_meta` を定義（`ParamMeta(kind=...)`）
  - `direction`: `vec3`
  - `distance`: `float`（`ui_min=0.0, ui_max=200.0`）
  - `scale`: `float`（`ui_min=0.0, ui_max=3.0`）
  - `subdivisions`: `int`（`ui_min=0, ui_max=8`）
  - `center_mode`: `choice`（`choices=("origin","auto")`）
- [ ] `@effect(meta=extrude_meta)` で `extrude(...) -> RealizedGeometry` を実装
  - [ ] `inputs` 空 → 空ジオメトリを返す（他 effect と同様）
  - [ ] `base.coords` 空 → `base` を返す
  - [ ] パラメータを旧仕様どおりクランプ（distance/scale/subdivisions）
  - [ ] `direction` を正規化して `extrude_vec = direction * (distance / ||direction||)` を計算（`||direction|| < 1e-9` または `distance==0` はゼロベクトル）
  - [ ] offsets からポリラインを抽出（頂点数 < 2 はスキップ）
  - [ ] `subdivisions > 0` のとき中点挿入を反復（可能なら `new[::2]=...` の簡潔な numpy 実装で）
  - [ ] 出力ポリライン列 `out_lines` を構築
    - [ ] 元ライン（細分化後）を追加
    - [ ] 押し出し + スケールしたラインを追加（`center_mode` 分岐）
    - [ ] 対応頂点を接続する 2 点ポリラインを追加（退化はスキップ）
  - [ ] `coords(float32)` / `offsets(int32)` を構築して `RealizedGeometry` を返す

## 3) effect 登録（API 側）

- [ ] `src/grafix/api/effects.py` に `from grafix.core.effects import extrude as _effect_extrude  # noqa: F401` を追加（import によりレジストリ登録される）

## 4) テスト追加（最小）

- [ ] `tests/` に extrude のスモークテストを追加
  - [ ] `G.line()` に `E.extrude(...)` を適用→ `realize(...)` して、ポリライン数/頂点数/主要座標を検証
  - [ ] `subdivisions=1` で頂点数が増えることを検証
  - [ ] `distance=0, scale=1` で「接続エッジ無し・ライン重複」が旧仕様どおりか確認（0) の合意に従う）

## 5) 品質ゲート（ローカル）

- [ ] `PYTHONPATH=src pytest -q`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`

