# どこで: `docs/scale_effect_migration_checklist.md`。
# 何を: `src/effects/scale.py` を現行コア（RealizedGeometry/effect_registry/ParamMeta）へ移植するチェックリスト。
# なぜ: 旧実装（旧 import/旧 registry/旧 Geometry.scale）を排除し、このリポジトリでそのまま動く scale effect を作るため。

## ゴール

- `E.scale(...)` が登録済み effect として利用できる。
- `realize()` によりスケール済み `RealizedGeometry` が得られる。
- Parameter GUI 用に `ParamMeta` が設定され、デフォルト値も観測される。
- 最小限のユニットテストで挙動が固定される。

## 仕様（今回の前提）

- 公開引数: `auto_center: bool`, `pivot: vec3`, `scale: vec3`（旧 `src/effects/scale.py` を踏襲）
- `auto_center=True` の中心: 頂点平均
- `auto_center=False` の中心: `pivot`

## 作業チェックリスト

- [x] 現状整理
  - [x] 旧 `src/effects/scale.py` の依存（`engine.core.geometry`, `.registry`）が現行構成と不整合である点を確認
  - [x] 現行 effect 実装の規約（`src/effects/rotate.py`）と `EffectRegistry` のシグネチャを確認
- [x] 実装
  - [x] `src/effects/scale.py` を現行形式へ全面書き換え（`@effect(meta=...)` + `RealizedGeometry` 変換）
  - [x] 中心（auto center / pivot）の扱いを実装
  - [x] dtype/shape を `RealizedGeometry` 契約に合わせる（float32, (N,3) / offsets int32）
- [x] 影響範囲の更新
  - [x] `E.scale(...)` の引数変更に伴い、参照しているテスト/スクリプトを更新
- [x] テスト
  - [x] `tests/test_scale.py` を追加
    - [x] 原点 pivot の 2D スケールが期待値になる
    - [x] auto_center が pivot を無視する
    - [x] 空 geometry が no-op で落ちない
- [x] 検証
  - [x] `pytest -q tests/test_scale.py` を実行
  - [x] scale を参照する既存テストを実行（`tests/parameters/` の該当分）

## 追加で気づいた点（今回はやらないが、次の改善候補）

- `spec.md` / `README.md` の `E.scale(s=...)` 例は今回の引数仕様とズレるため、記述更新が必要。
