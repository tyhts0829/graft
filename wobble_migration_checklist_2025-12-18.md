# wobble 移植チェックリスト（2025-12-18）

目的: 旧 `src/grafix/core/effects/from_previous_project/wobble.py` を参照し、Grafix 新コア（`RealizedGeometry` + `@effect(meta=...)`）向けの `src/grafix/core/effects/wobble.py` を実装する。

注意: 互換ラッパー/シムは作らない（新プロジェクト流に揃える）。

## 0) 事前に決める（あなたの確認が必要）

- [ ] `frequency` の型:
  - A: `vec3` 固定（`(fx, fy, fz)`）。等方は `(f, f, f)` で表現（推奨: GUI と整合する）；はい
  - B: `float | vec3` を受けたい（この場合、GUI/ParamStore 側も壊さない手当が必要）
- [ ] z 方向の扱い（旧挙動の再現方法）:
  - A: 「入力が実質 2D（例: z が全て 0）」なら z は保持し、xy のみ wobble（旧の “2D は z を触らない” 意図を優先）
  - B: 常に xyz を wobble（新コア的に一貫。2D 図形でも z が動き得る）；こちらで
- [ ] パラメータ名（単位表記）:
  - A: `amplitude / frequency / phase`（旧名踏襲）；こちらで
  - B: `amplitude_mm / frequency / phase_deg`（単位を明示）
- [ ] UI レンジ（`ParamMeta.ui_min/ui_max`）:
  - A: 旧 `amplitude<=20, frequency<=0.2, phase<=360` を踏襲;こちらで
  - B: 新プロジェクトの他 effect に合わせて再調整（例: displace の `spatial_freq<=0.1` 等）

## 1) 旧仕様の要点（参照実装の挙動）

- サイン波で各頂点を変位: `axis += amplitude * sin(2π * f_axis * axis + phase)`
- `frequency` は等方（float）または軸別（Vec3）
- `phase` は degree 入力（内部は rad）
- 2D の場合は z を変位しない（旧実装は `shape[1] > 2` のときのみ z を触る）

## 2) 新実装の落とし込み（Grafix での形）

- 新規ファイル: `src/grafix/core/effects/wobble.py`
  - `grafix.core.effect_registry.effect` に `@effect(meta=wobble_meta)` で登録
  - 入出力は `Sequence[RealizedGeometry] -> RealizedGeometry`
  - `offsets` は維持（線分割を変えない）し、`coords` のみ変形する
- API 反映: `src/grafix/api/effects.py` に import を追加し、起動時にレジストリ登録されるようにする

## 3) 実装チェックリスト

- [ ] `src/grafix/core/effects/wobble.py` を追加

  - [ ] モジュール docstring: 「どのような効果か」を簡潔に（`src/grafix/core/effects/AGENTS.md` に従う）
  - [ ] `wobble_meta` を `ParamMeta` で定義（built-in effect は meta 必須）
  - [ ] `@effect(meta=wobble_meta)` で `wobble(inputs, *, ...)` を実装
  - [ ] no-op:
    - [ ] `inputs` が空 → 空ジオメトリ
    - [ ] `coords` が空 → `base` を返す
    - [ ] `amplitude == 0` → `base` を返す
  - [ ] 数値実装:
    - [ ] `phase` を deg→rad 変換
    - [ ] `coords` をコピーして xyz へベクトル化演算で加算（最後は `float32` に戻す）
    - [ ] z の扱いは 0) の決定に従う

- [ ] `src/grafix/api/effects.py` に登録 import を追加
  - [ ] `from grafix.core.effects import wobble as _effect_wobble  # noqa: F401`

## 4) テスト（最小・再現性重視）

- [ ] `tests/core/test_effect_wobble.py` を追加
  - [ ] 既知入力に対して `np.allclose` で期待値一致（位相・周波数の基本ケース）
  - [ ] `amplitude=0` が no-op（座標が変わらない）
  - [ ] `inputs=[]` が空ジオメトリを返す
  - [ ] z の扱い（0) の決定に合わせた期待値）

## 5) 検証コマンド（局所）

- [ ] `ruff check src/grafix/core/effects/wobble.py tests/core/test_effect_wobble.py`
- [ ] `mypy src/grafix`（必要なら対象ファイルに限定）
- [ ] `PYTHONPATH=src pytest -q tests/core/test_effect_wobble.py`

## 6) 完了条件（Definition of Done）

- [ ] `E.wobble(...)` が利用できる（`src/grafix/api/effects.py` 経由で登録済み）
- [ ] 旧仕様（0) で決めた範囲）を満たす
- [ ] 追加テストが通る
- [ ] ruff/mypy の対象チェックが通る

## メモ（追加提案）

- `vec3` 引数に対するスカラー入力の自動ブロードキャストを共通化すると便利だが、`ParamStore/GUI` 側の扱いまで含めて設計が必要。今回は wobble の移植スコープ外として切り分ける案もあり。
