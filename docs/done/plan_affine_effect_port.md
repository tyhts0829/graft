# affine effect（`src/effects/from_previous_project/affine.py`）を現仕様へ移植する計画

## 目的

- 旧実装 `src/effects/from_previous_project/affine.py` を、現行アーキテクチャ（`RealizedGeometry` + `@effect(meta=...)`）に合わせて作り直し、`api.E.affine(...)` として利用可能にする。

## ゴール（完了条件）

- `E.affine(...)(g)` が動作し、`realize()` で期待どおりの座標変換（スケール → 回転 → 平行移動）が適用される。
- `ParamMeta` による GUI 生成に必要な meta と default が揃う（`effect_registry.get_meta/get_defaults` が成立）。
- 単体テスト（`tests/test_affine.py`）で主要ケース（空入力・auto_center・pivot・合成順序・delta）が検証される。

## 方針（設計）

- **現仕様の effect 実装は `src/effects/*.py` に置く**。そのため `affine` は `src/effects/affine.py` を新設し、現行の `scale/rotate` と同じインターフェイスに揃える。
- 旧ファイル `src/effects/from_previous_project/affine.py` は **そのまま保持** する（削除しない）。
- 数値計算は `numpy` ベースで実装し、回転規約は旧仕様（Rz・Ry・Rx の合成、row-vector のため `rot.T` を適用）を踏襲する。
- 変換順序は **中心へ移動 → スケール → 回転 → 中心へ戻す → 平行移動** とする。

## 仕様（この移植で確定させる内容）

- 関数名: `affine`（op 名も同一）
- 入力: `inputs: Sequence[RealizedGeometry]`（通常 1 要素）
- パラメータ:
  - `auto_center: bool = True`
  - `pivot: tuple[float, float, float] = (0.0, 0.0, 0.0)`
  - `scale: tuple[float, float, float] = (1.0, 1.0, 1.0)`
  - `rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)`（[deg]）
  - `delta: tuple[float, float, float] = (0.0, 0.0, 0.0)`（[mm]）

## チェックリスト（実装タスク）

- [x] `src/effects/affine.py` を新規作成（ヘッダ: どこで/何を/なぜ、NumPy スタイル docstring、型ヒント）。
- [x] `affine_meta` を定義（`ParamMeta(kind="bool"/"vec3")`、`ui_min/ui_max` は `scale.py` / `rotate.py` と整合する範囲で設定）。
- [x] `@effect(meta=affine_meta)` で `affine()` を登録（署名は他 effect と同形: `inputs` + keyword-only params）。
- [x] 空入力（`not inputs`）は空 `RealizedGeometry` を返す（既存 `scale/rotate` と同じ振る舞い）。
- [x] `inputs[0].coords.shape[0] == 0` は入力をそのまま返す（no-op）。
- [x] 中心座標の決定:
  - `auto_center=True` のとき `coords.mean(axis=0)`（float64）を中心に使う。
  - `auto_center=False` のとき `pivot` を中心に使う。
- [x] 合成変換を実装（float64 で計算し、最後に float32 へ戻す）:
  - `shifted = coords - center`
  - `scaled = shifted * scale + center`
  - `rotated = (scaled - center) @ rot.T + center`（`rotate.py` と同じ回転行列生成）
  - `translated = rotated + delta`
- [x] 変換が恒等（`scale=(1,1,1)` かつ `rotation=(0,0,0)` かつ `delta=(0,0,0)`）の場合は `inputs[0]` をそのまま返す（中心計算を避ける、仕様上安全）。
- [x] `src/api/effects.py` に `from src.effects import affine as _effect_affine  # noqa: F401` を追加し、`E.affine` を公開する。
- [x] `tests/test_affine.py` を追加（`tests/test_scale.py` / `tests/test_rotate.py` の形式に揃える）:
  - [x] 原点 pivot での合成結果（スケール → 回転 → 平行移動）を数値で検証。
  - [x] `auto_center=True` で `pivot` を無視することを検証。
  - [x] `auto_center=False` で `pivot` が効くことを検証。
  - [x] 空 geometry が no-op であることを検証。
- [x] 影響確認として `pytest -q tests/test_affine.py tests/test_rotate.py tests/test_scale.py` を実行。
- [ ] `ruff` / `mypy` が運用されているなら、変更ファイルに限定して実行（例: `ruff check src/effects/affine.py tests/test_affine.py`）。
- [x] 旧ファイル `src/effects/from_previous_project/affine.py` は保持する（削除しない）。
- [ ] README の例が `E.affine()` 前提なので、必要なら README を現状に合わせて更新（例の `displace()` が未移植なら例を差し替え）。

## 事前に確認した点（確定）

1. `affine` は **新規に `src/effects/affine.py`** として追加し、旧 `src/effects/from_previous_project/affine.py` はそのまま置く。
2. 回転合成順序は旧仕様（Rz・Ry・Rx 合成）を踏襲する。
3. `delta` の UI レンジ（`ui_min/ui_max`）は `-500..500` とする。

## 注意

- 旧ファイル `src/effects/from_previous_project/affine.py` を将来 import すると、同名 effect の登録が上書きされる可能性がある（現状は未 import なので影響なし）。
