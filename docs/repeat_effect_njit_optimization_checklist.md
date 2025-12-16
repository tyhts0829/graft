# どこで: `docs/repeat_effect_njit_optimization_checklist.md`。

# 何を: `src/effects/repeat.py` を旧実装（`src/effects/from_previous_project/repeat.py`）同様に `numba.njit` で最適化するためのチェックリスト。

# なぜ: 複製数×頂点数が大きいケースで、Python/Numpy 経路のオーバーヘッドを減らし、repeat を高速に動かすため。

## 決定

- [x] `repeat` は Numba 依存（`from numba import njit`）を前提にし、純 NumPy フォールバックは用意しない
- [x] `@njit(cache=True, fastmath=True)` を使用する

## ゴール

- `E.repeat(...)(g)` が登録済み effect として利用できる（現状維持）。
- `realize()` により複製後の `RealizedGeometry(coords, offsets)` が得られる（現状維持）。
- 旧実装の変換順序（中心移動→スケール→回転→平行移動→中心復帰）と回転合成（Z→Y→X）を維持する。
- Numba カーネルを導入し、繰り返し変換部が njit 経路で動く。

## 作業チェックリスト

- [x] 上の確認事項へ回答を反映する
- [x] 実装（`src/effects/repeat.py`）
  - [x] 旧実装の `_apply_transform_to_coords` 相当を `@njit` で追加（in-place で出力へ書き込む）
  - [x] 既存の座標変換を njit カーネル呼び出しに置き換える
  - [x] 空入力 / 空ジオメトリ / no-op 条件の扱いを変えない
- [x] テスト（`pytest`）
  - [x] `tests/test_repeat.py` に回転（`rotation_step`）が効くケースを 1 つ追加する
  - [x] 最小対象テストのみ実行: `pytest -q tests/test_repeat.py`
- [x] 追加最適化
  - [x] 複製ループごと njit 内へ移す（copy 回数分を 1 カーネルで処理）
  - [x] 最小対象テストのみ実行: `pytest -q tests/test_repeat.py`

## 追加で気づいた点（必要なら追記）

- 追加最適化は「Python ループ → njit ループ」への置き換えであり、出力互換性を保ったままオーバーヘッドを削減する狙い。
