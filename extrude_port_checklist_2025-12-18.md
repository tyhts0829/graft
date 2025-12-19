# extrude 移植チェックリスト（2025-12-18）

目的: 旧プロジェクト実装 `src/grafix/core/effects/from_previous_project/extrude.py` を参照し、新プロジェクトの effect として `src/grafix/core/effects/extrude.py` を新規実装する（基本は旧仕様踏襲）。

注意: 互換ラッパー/シムは作らない。旧ファイルは参照として残し、必要なら最後に整理する（要確認）。

## 0) 事前に決める（あなたの確認が必要）

- [x] 出力構成: 旧仕様どおり「元ライン群 + 押し出し後ライン群 + 各頂点の接続エッジ（2 点ポリライン）」を出力する。ただし引数的に図形に変化が無い場合は no-op で入力を返す。
- [x] `center_mode` の不正値: `center_mode == "auto"` のときだけ重心中心スケールし、それ以外は `"origin"` 扱いにする。
- [x] 細分化: 旧仕様どおり「各セグメントへ中点挿入」を `subdivisions` 回繰り返す（`subdivide` effect の停止条件/上限は流用しない）。
- [x] 旧参照ファイルの扱い: そのまま放置する。

## 1) 仕様の棚卸し（旧実装 → 新実装の対応表）

- [x] 入出力: `Geometry`（旧）→ `RealizedGeometry`（新）
- [x] effect 登録: 旧 `@effect()` + `__param_meta__` → 新 `@effect(meta=...)` + `ParamMeta`
- [x] パラメータ（旧仕様踏襲）:
  - `delta: vec3`（押し出し量 [mm]、長さは 0–200mm にクランプ）
  - `scale: float`（0–3 にクランプ）
  - `subdivisions: int`（0–8 にクランプ）
  - `center_mode: {"origin","auto"}`（スケール中心）
- [x] 退化エッジ: 接続エッジは `np.allclose(..., atol=1e-8)` でゼロ長をスキップ

## 2) 新規ファイル実装（`src/grafix/core/effects/extrude.py`）

- [x] ファイル先頭: 簡潔な日本語 docstring（旧仕様踏襲の要点、パラメータ、注意点）
- [x] `extrude_meta` を定義（`ParamMeta(kind=...)`）
  - `delta`: `vec3`（`ui_min=-200.0, ui_max=200.0`）
  - `scale`: `float`（`ui_min=0.0, ui_max=3.0`）
  - `subdivisions`: `int`（`ui_min=0, ui_max=8`）
  - `center_mode`: `choice`（`choices=("origin","auto")`）
- [x] `@effect(meta=extrude_meta)` で `extrude(...) -> RealizedGeometry` を実装
  - [x] `inputs` 空 → 空ジオメトリを返す（他 effect と同様）
  - [x] `base.coords` 空 → `base` を返す
  - [x] パラメータを旧仕様どおりクランプ（distance/scale/subdivisions）
  - [x] `extrude_vec = delta` を計算し、長さが 200mm を超える場合は正規化して 200mm にクランプする
  - [x] offsets からポリラインを抽出（頂点数 < 2 はスキップ）
  - [x] `subdivisions > 0` のとき中点挿入を反復（`new[::2]=...` の numpy 実装）
  - [x] 出力ポリライン列 `out_lines` を構築
    - [x] 元ライン（細分化後）を追加
    - [x] 押し出し + スケールしたラインを追加（`center_mode` 分岐）
    - [x] 対応頂点を接続する 2 点ポリラインを追加（退化はスキップ）
  - [x] 図形に変化が無い引数（`delta=(0,0,0), scale=1, subdivisions=0`）は no-op で `base` を返す
  - [x] `coords(float32)` / `offsets(int32)` を構築して `RealizedGeometry` を返す

## 3) effect 登録（API 側）

- [x] `src/grafix/api/effects.py` に `from grafix.core.effects import extrude as _effect_extrude  # noqa: F401` を追加（import によりレジストリ登録される）

## 4) テスト追加（最小）

- [x] `tests/` に extrude のスモークテストを追加
  - [x] `G.line()` に `E.extrude(...)` を適用 → `realize(...)` して、ポリライン数/頂点数/主要座標を検証
  - [x] `subdivisions=1` で頂点数が増えることを検証
  - [x] `delta=(0,0,0), scale=1, subdivisions=0` は no-op で入力と一致することを検証（0) の合意に従う）

## 5) 品質ゲート（ローカル）

- [x] `python -m tools.gen_g_stubs`（`src/grafix/api/__init__.pyi` を再生成）
- [x] `PYTHONPATH=src pytest -q tests/core/test_effect_extrude.py`
- [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`
- [ ] `PYTHONPATH=src pytest -q`
- [ ] `ruff check .`（この環境では `ruff` 未インストール）
- [x] `mypy src/grafix/core/effects/extrude.py`
- [ ] `mypy src/grafix`
