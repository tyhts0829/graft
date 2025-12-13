# どこで: `docs/rotate_effect_migration_checklist.md`。

# 何を: `src/effects/rotate.py` を現行コア（Geometry/RealizedGeometry/effect_registry）へ移植するチェックリスト。

# なぜ: 旧実装（旧 import/旧 registry/旧 Geometry.rotate）を排除し、このリポジトリでそのまま動く rotate effect を作るため。

## ゴール

- `E.rotate(...)` が登録済み effect として利用できる。
- `realize()` により回転済み `RealizedGeometry` が得られる。
- Parameter GUI 用に `ParamMeta` が設定され、デフォルト値も観測される。
- 最小限のユニットテストで挙動が固定される。

## 作業チェックリスト

- [x] 現状整理
  - [x] 旧 `src/effects/rotate.py` の依存（`engine.core.geometry`, `.registry`）が現行構成と不整合である点を確認
  - [x] 現行 effect 実装の規約（`src/effects/scale.py`）と `EffectRegistry` のシグネチャを確認
- [x] 仕様確定（下の「事前確認したいポイント」）
- [x] 実装
  - [x] `src/effects/rotate.py` を現行形式へ全面書き換え（`@effect(meta=...)` + `RealizedGeometry` 変換）
  - [x] 回転中心（auto center / pivot）の扱いを実装
  - [x] dtype/shape を `RealizedGeometry` 契約に合わせる（float32, (N,3) / offsets int32）
- [x] 登録
  - [x] `src/api/effects.py` に rotate 実装モジュール import を追加してレジストリ登録されるようにする
- [x] テスト
  - [x] `tests/test_rotate.py` を追加
    - [x] z 回転（2D）で期待座標になる（簡単な 1 ポリライン）
    - [x] pivot 指定時に中心が変わる
    - [x] 空 geometry が no-op で落ちない
- [x] 検証
  - [x] `pytest -q tests/test_rotate.py` を実行
  - [ ] 既存テスト（少なくとも `pytest -q`）で破綻が無いことを確認（必要なら）

## 事前確認したいポイント（ここが決まると実装が一意になる）

1. rotate の公開引数（破壊的変更 OK 前提）

- 案 A: `angle: float`（Z 軸回転のみ、2D 想定）+ `auto_center/pivot`
- 案 B: `rotation: tuple[float,float,float]`（XYZ 回転）+ `auto_center/pivot`（旧 rotate.py 踏襲） 案 B を採用

2. 角度の単位

- 度（deg）で受け取って内部で rad に変換（旧 rotate.py と同じ） こちらを採用
- ラジアン（rad）で受け取り、そのまま `sin/cos` に渡す（spec.md の `angle=t` と相性が良い可能性）

3. auto_center の定義

- 頂点の平均（旧 rotate.py と同じ）；こちらを採用
- bbox の中心（min/max の中点）

4. pivot の型

- `vec3`（(x,y,z)）で統一；こちらを採用
- `cx/cy` の 2 スカラー（2D 回転に寄せる）

## 追加で気づいた点（今回はやらないが、次の改善候補）

- `src/primitives/polygon.py` も旧実装（`engine.core.geometry` / `.registry` / `__param_meta__`）なので、同様に現行 `primitive_registry` へ移植が必要。
- `spec.md` の例は `E.rotate(angle=t)` になっているため、今回の決定（`rotation=(rx,ry,rz)` + degree）に合わせて記述更新が必要。
