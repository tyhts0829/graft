# どこで: `docs/subdivide_effect_migration_checklist.md`。

# 何を: 旧 `src/effects/from_previous_project/subdivide.py` を参照しつつ、現行仕様の新規 effect `subdivide` を追加するためのチェックリスト。

# なぜ: 旧プロジェクト依存（`engine.*` / 旧 registry / 旧 Geometry）を排除し、`RealizedGeometry` + `src.core.effect_registry` 前提で `E.subdivide(...)` を利用可能にするため。

## 確認事項（回答済み）

- 追加先ディレクトリ: **`src/effects/`** を採用する。
- `subdivide` のデフォルト値:
  - 旧実装は docstring と実デフォルトが食い違う（説明: 5 / 実装: 0）。
  - 現行では **デフォルト 0**（no-op）を採用する。

## ゴール

- `E.subdivide(...)(g)` が登録済み effect として利用できる。
- `realize()` により細分化後の `RealizedGeometry(coords, offsets)` が得られる。
- Parameter GUI 用に `ParamMeta` が設定され、デフォルト値も観測される。
- 最小限のユニットテストで挙動が固定される。

## 仕様整理（旧 subdivide の挙動を踏襲する案）

- 引数: `subdivisions: int`
  - `subdivisions <= 0` は no-op（入力をそのまま返す）
  - `subdivisions` は `0..10` にクランプ（`MAX_SUBDIVISIONS=10`）
- 各ポリラインごとに「全セグメントへ中点挿入」を反復し、頂点数を増やす。
- 停止条件（旧実装踏襲）:
  - 初期状態で最短セグメント長が `MIN_SEG_LEN(=0.01)` 未満なら、そのポリラインは細分化しない
  - 反復後に最短セグメント長が `MIN_SEG_LEN` 未満になった時点で、以降の反復を停止する（その反復結果は採用）
  - 出力の合計頂点数が `MAX_TOTAL_VERTICES(=10_000_000)` を超えないようにガードし、超える場合は以降のライン処理を打ち切る

## 作業チェックリスト

- [x] 追加先ディレクトリとデフォルト値を確定する（`src/effects/`, default=0）
- [x] 実装
  - [x] `src/effects/subdivide.py` を追加（`@effect(meta=...)` + `RealizedGeometry` 変換）
  - [x] `ParamMeta(kind="int", ui_min=0, ui_max=10)` を設定
  - [x] 空入力 / 空ジオメトリ / no-op 条件を既存 effect と同様に扱う
  - [x] 旧挙動の停止条件（最短セグメント長 / 最大頂点数）を反映する
- [x] 登録
  - [x] `src/api/effects.py` に import を追加して `E.subdivide` が解決できるようにする
- [x] テスト（`pytest`）
  - [x] `tests/test_subdivide.py` を追加
    - [x] 2 点直線 + `subdivisions=1` で 3 点（中点が入る）
    - [x] `subdivisions=2` で頂点数が想定どおり増える
    - [x] デフォルト `subdivisions=0` が no-op になる
    - [x] 最短セグメント長ガードで過剰細分化が止まる
    - [x] 複数ポリライン入力で offsets が整合する
    - [x] 空ジオメトリが no-op で落ちない
  - [x] 最小対象テストのみ実行: `pytest -q tests/test_subdivide.py`

## 追加で気づいた点（必要なら追記）

- `MAX_TOTAL_VERTICES` ガードをテストする場合は、テスト内でモジュール定数を小さく差し替える（Python 側の制御で完結する設計にする）。
