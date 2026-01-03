# どこで: Grafix リポジトリ（設計メモ / 実装チェックリスト）。

# 何を: `E.buffer()` に `union: bool` を追加し、1 つの RealizedGeometry 内の複数ポリラインを統合して buffer できるようにする。

# なぜ: 重なり/接触した線群を 1 つのアウトラインにまとめたいケースがあるため。

# buffer union: 実装計画

## ゴール

- `E.buffer(union=True)` で、同一 `RealizedGeometry` の複数ポリラインを 1 つに統合した buffer 結果を返す。
- `union=False`（デフォルト）は現状挙動を維持する。
- `distance>0` は外周、`distance<0` は内周（holes）の方針を維持する。
- `keep_original=True` は従来通り「結果 + 元ポリライン」を出力する。

## 非ゴール（今回やらない）

- 複数 `inputs`（複数 RealizedGeometry）間の統合（今回は `inputs[0]` のみ）。
- 非共平面な線群の 3D ブーリアン（3D union）的な統合。
- 自動クラスタリングや高度な例外処理（最小の分岐に留める）。

## 仕様（案）

### `union=False`（現状）

- offsets で区切られた各ポリラインを独立に処理する:
  - 端点近接なら自動で閉じる（`_close_curve()`）
  - 各ポリラインごとに推定平面へ射影 →`LineString(...).buffer(...)`→ 輪郭抽出 →3D へ復元
- 結果はポリライン本数ぶん増え得る（重なっても統合しない）。

### `union=True`（追加）

- 目的: 複数ポリラインの buffer を 1 つの Shapely geometry としてまとめ、重なりを統合した輪郭を返す。
- 処理フロー（最小案）:
  1. 各ポリラインに `_close_curve()` を適用（現状互換）
  2. 全点（`inputs[0].coords`）から 1 つの平面 basis を推定
  3. 同一 basis で全ポリラインを 2D へ射影
  4. `MultiLineString([...])` を作り、1 回だけ `.buffer(abs(distance), ...)`
  5. `distance>0` は exterior、`distance<0` は interior を抽出
  6. 同一 basis で 3D に戻し、`RealizedGeometry` を構築
- 注意: `union=True` は「全体を 1 平面に潰す」ので、非共平面入力では結果が歪む。

## 仕様で先に決めたい点（要確認）

- 非共平面入力の扱い:
  - A: 判定せず best-fit 平面で実行（最小・単純）；これで
  - B: “ほぼ共平面” 判定を入れ、NG なら `union=False` 相当へフォールバック（挙動は安定、実装は増える）
- `distance<0` の意味:
  - 現行通り「buffer(abs) の holes を返す」で良い？（union 後も同じ）；はい
- `keep_original=True` かつ `union=True` の出力順:
  - (union 結果 → original) のままで OK？；はい
- 既存 `join`/`quad_segs` は、そのまま union buffer に適用で OK？；はい

## 実装チェックリスト

- [ ] `src/grafix/core/effects/buffer.py` に `union: bool = False` を追加
  - [ ] `buffer_meta` に `union` を追加（`kind="bool"`）
  - [ ] docstring の Parameters に `union` を追記
  - [ ] `union` 分岐を追加（`MultiLineString(...).buffer(...)` を 1 回）
- [ ] テスト追加
  - [ ] `tests/core/effects/test_buffer_union.py`（新規）
    - [ ] 2 本の近接/重なりセグメントで `union=False` は出力 2 本、`union=True` は出力 1 本になること
    - [ ] `keep_original=True` でも original が末尾に残ること
- [ ] 型スタブ同期
  - [ ] `tools/gen_g_stubs.py` の生成結果で `src/grafix/api/__init__.pyi` を更新（`E.buffer` の `union: bool` が反映されること）
  - [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`
- [ ] Lint/型
  - [ ] `ruff check src/grafix/core/effects/buffer.py tests/core/effects/test_buffer_union.py`
  - [ ] `mypy src/grafix`（必要なら対象限定）

## 追加で気づいた点（提案）

- `union=True` は基底推定が “全点で 1 回” になるため、現状の「線ごと basis」より結果が揃う一方、非共平面入力の許容度は下がる。
- union 実装は `MultiLineString(...).buffer(...)` を使うと “統合込みの buffer” になり、`unary_union` の追加呼び出しを避けられる可能性が高い（コードが短くなる）。
