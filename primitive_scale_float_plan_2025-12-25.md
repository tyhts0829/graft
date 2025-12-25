# primitive の `scale: vec3` を `scale: float` に統一する計画（2025-12-25）

## 背景 / 方針

- 目的: primitive の `scale` を **uniform（等方）スケール**として `float` に単純化する。
- 縦横比（非等方スケール, sx≠sy≠sz）を変えたい場合は、primitive ではなく **effect（例: `E.scale` / `E.affine`）** で表現する。
- このリポジトリは未配布前提のため、互換ラッパーや shim は作らず **破壊的変更でよい**。

## 影響範囲（`scale` が vec3 の primitive）

### コア primitive 実装

- `src/grafix/core/primitives/grid.py`（`scale: vec3`）
- `src/grafix/core/primitives/polygon.py`（`scale: vec3`）
- `src/grafix/core/primitives/polyhedron.py`（`scale: vec3`）
- `src/grafix/core/primitives/sphere.py`（`scale: vec3`）
- `src/grafix/core/primitives/text.py`（`scale: vec3`）
- `src/grafix/core/primitives/torus.py`（`scale: vec3`）

### API / スタブ（型）

- `src/grafix/api/__init__.pyi`（`G.<primitive>(..., scale: Vec3 = ...)` を `float` に）
- `tools/gen_g_stubs.py`（`G` スタブ生成の型割り当てが `scale: float` になることの確認・必要なら修正）

### テスト（呼び出し側）

- `tests/core/test_text_primitive.py`（`G.text(..., scale=(...))` を更新）
- `tests/core/test_text_fill_stability.py`（`G.text(..., scale=(...))` を更新）
- `tests/core/effects/test_fill.py`（`G.polygon(scale=(50.0, 0.0, 1.0))` を更新）

## 変更後の API イメージ（例）

- 等方スケール: `G.polygon(scale=50.0)`
- 縦横比変更（非等方）: `E.scale(scale=(2.0, 1.0, 1.0))(G.polygon(scale=50.0))`
  - もしくは `E.affine(scale=(2.0, 1.0, 1.0))(...)`

## 実施チェックリスト（承認後に実装）

- [ ] (1) 仕様確定: primitive の `scale` は `float`（等方）で、非等方は effect に寄せる。
- [ ] (2) `grid/polygon/polyhedron/sphere/text/torus` の `ParamMeta(kind="vec3")` を `kind="float"` に変更する。
- [ ] (3) 各 primitive の関数シグネチャを `scale: float = 1.0` に変更する（docstring の Parameters も更新）。
- [ ] (4) 各 primitive の実装で `scale` を `float(scale)` へ正規化し、`(s, s, s)` 相当の等方スケールとして適用する。
  - 例: `sx,sy,sz = scale` の分解・`長さ3` 検証・エラーメッセージを削除する。
  - `text/sphere/polyhedron` の `_polylines_to_realized(..., scale=...)` が vec3 前提なら、引数を `float` に合わせて整理する。
- [ ] (5) 呼び出し側（テスト）の修正:
  - [ ] `G.text(..., scale=(10,10,1))` のように等方だった箇所は `scale=10.0` に置換する。
  - [ ] `G.text(..., scale=(20,30,1))` のように非等方だった箇所は `E.scale(scale=(20,30,1))(G.text(scale=1.0))` 等へ移す。
  - [ ] `G.polygon(scale=(50,0,1))` は `E.scale(scale=(50,0,1))(G.polygon(scale=1.0))` 等へ移す（意図を確認しつつ最小変更）。
- [ ] (6) `G` の `.pyi` スタブを更新/再生成し、primitive の `scale` が `float` になっていることを確認する。
- [ ] (7) 最小検証:
  - [ ] `PYTHONPATH=src pytest -q tests/core/test_text_primitive.py`
  - [ ] `PYTHONPATH=src pytest -q tests/core/test_text_fill_stability.py`
  - [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_fill.py`
  - [ ] `ruff check src/grafix/core/primitives src/grafix/api tools tests`
  - [ ] `mypy src/grafix`

## 事前確認したいこと（あなたに質問）

1. 非等方スケールの表現は、原則 `E.scale(...)` に統一で良い？（`E.affine(scale=...)` でも同等だが、作法としてどちらを推奨するか）
2. primitive の `scale` は型ヒントも厳密に `float` のみで良い？（実装は `float(scale)` で `int` も通るが、公開 API の意図として）

