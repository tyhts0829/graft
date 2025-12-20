---
title: fill の引数を sequence 対応（グループ単位サイクル）
date: 2025-12-20
status: draft
---

# 背景

`fill` は現在 `angle_sets/angle/density/spacing_gradient/remove_boundary` をスカラーとして受け取り、全グループに同一値を適用している。
これを、例えば `angle=[0,45,90]` のように sequence も受け取り、even-odd グループごとに `0,45,90,0,45,90,...` とサイクル適用できるようにしたい。

# 目的

- `fill` の主要パラメータを **グループ（外環＋穴）単位**でサイクル適用できるようにする。
- 既存のスカラー指定はそのまま動かす（後方互換）。
- パフォーマンス劣化は “パラメータの正規化＋添字” 程度に抑える（内側ループに型判定を入れない）。

# 要件

- In:
  - `angle_sets: int | Sequence[int]`
  - `angle: float | Sequence[float]`（deg）
  - `density: float | Sequence[float]`
  - `spacing_gradient: float | Sequence[float]`
  - `remove_boundary: bool | Sequence[bool]`
  - いずれも `seq[group_index % len(seq)]` でサイクル。
  - planar（グローバル平面）経路では `group_index` は `groups` の列挙順。
  - non-planar フォールバックでは “グループ=各ポリライン” とみなし `poly_i` を `group_index` とする。
- Out:
  - GUI（ParamStore/ImGui）で sequence を編集する UI 提供（コード指定のみにする）。

# 実装方針

1) `fill()` 冒頭で引数を “サイクル可能な列” に正規化する（list/tuple へ）。
   - 例: `_as_float_cycle(x) -> tuple[float, ...]` / `_as_int_cycle(x) -> tuple[int, ...]`
   - `remove_boundary` 用に `_as_bool_cycle(x) -> tuple[bool, ...]` を追加。
   - 空の sequence は `ValueError`（サイクルできないため）。
   - ここで型変換・クランプを済ませ、ループ内は `seq[idx]` のみで動くようにする。

2) planar 経路
   - `groups = _build_evenodd_groups(...)` の列挙 index を `gi` とし、各パラメータを `..._seq[gi % len(..._seq)]` で取り出す。
   - `base_spacing = _spacing_from_height(ref_height_global, density_i)` をグループごとに計算。
   - `angle_sets_i` に応じて `k_i` 方向を回す（現在の `k` をグループ内変数へ置換）。
   - `remove_boundary_i` が False の場合のみ、このグループに属する輪郭（outer+holes）を出力へ追加する。

3) non-planar 経路
   - `poly_i` を `gi` として同様にサイクル。
   - 現状通り「各ポリラインの ref_height から spacing」を決める（グローバルの共通 spacing にはしない）。
   - `remove_boundary_i` が False の場合のみ、そのポリライン境界を出力へ残す（現行挙動を polywise に拡張）。

4) ドキュメント
   - `fill` docstring に「sequence 指定可・グループ単位でサイクル」の追記。
   - `remove_boundary` も sequence 対応であることを明記（planar=グループ単位、non-planar=ポリライン単位）。

# 変更箇所

- `src/grafix/core/effects/fill.py`
  - `fill()` の引数型・正規化ロジック追加
  - planar/non-planar それぞれで `gi` によるサイクル参照
- `tests/core/effects/test_fill.py`
  - 回帰/仕様テスト追加（複数グループで angle がサイクルすること）
- （必要なら）API スタブ:
  - `src/grafix/api/__init__.pyi` の `E.fill(...)` 型定義更新
  - 既存のスタブ同期テストがある場合はそれに従う

# テスト案

- `test_fill_groupwise_angle_cycles_across_disjoint_rings`
  - 3 つの離れた正方形（3 グループ）を作る。
  - `angle=[0, 90]` を指定して `remove_boundary=True` で塗る。
    - 1つ目（angle=0）は水平線中心、2つ目（angle=90）は垂直線中心、3つ目は水平（cycle）になることを、セグメントの向き（x/y どちらが一定か）で判定する。
- `test_fill_groupwise_remove_boundary_cycles`
  - 2 つの離れた正方形（2 グループ）を作る。
  - `remove_boundary=[True, False]` を指定して塗る。
  - 1つ目は境界が出ない / 2つ目は境界が出ることを、出力の「境界っぽいポリライン（頂点数>2）」の有無で判定する。

# 検証コマンド

- `PYTHONPATH=src pytest -q tests/core/effects/test_fill.py`
- （任意）`PYTHONPATH=src pytest -q`

# リスク/注意点

- サイクル単位は「グループ」なので、穴付き形状は外環＋穴を一体として同じパラメータになる。
- GUI からは sequence を投入できない想定（コード指定のみ）。
- `angle_sets`/`density` を groupwise に上げると、そのグループの計算量が増える（これは仕様）。
- `remove_boundary` を groupwise にすると、出力の境界線が「グループごとに出たり出なかったり」するため、見た目/順序の期待値が入力に依存する（ただし仕様として明確化する）。

# 作業チェックリスト

- [ ] `fill()` 引数の sequence 対応方針を確定（In/Out の最終確認、remove_boundary 含む）
- [ ] `fill.py` に “cycle 正規化” ヘルパーを追加
- [ ] planar 経路を groupwise cycle に置換（`gi`/`k_i`/`angle_i`/`density_i`/`grad_i`）
- [ ] non-planar 経路を polywise cycle に置換（`poly_i` を `gi` とする）
- [ ] `remove_boundary` を groupwise/polywise cycle に対応させる（境界追加の位置を調整）
- [ ] docstring を更新
- [ ] テスト追加（上のテスト案）
- [ ] `pytest` で確認
- [ ] （必要なら）`src/grafix/api/__init__.pyi` を更新
