# fill: even-odd グルーピング全面 numba 化 計画（2025-12-23）

- どこで: `src/grafix/core/effects/fill.py`
- 何を: `_build_evenodd_groups()` の dict/list[np.ndarray] を廃止し、`ring_start/ring_end` 配列化 + 二重ループ自体を `@njit` 化する
- なぜ: 現状は `_point_in_polygon` だけ numba 化して高速化できたが、依然として
  - Python 側の dict/list 構築
  - 1,000,000 回級のループを Python で回す
  - numba 関数を Python から大量回数呼ぶ（dispatcher オーバーヘッド）
    が残り、さらに詰める余地がある

## 現状（ベースライン）

- 入力例: `sketch/test.py` の `DESCRIPTION` を `G.text()` した輪郭（rings=1157）
- 現状の実測（例）:
  - `_build_evenodd_groups`: 約 0.5〜0.7 秒
  - `fill(text)`: 約 0.7 秒

## 目標（Done の定義）

- rings=1000 前後で `_build_evenodd_groups` が **0.2〜0.3 秒級**（目安）になる
- 出力:
  - 「境界上は False」挙動を維持（現行踏襲）
  - グループ化結果（outer/hole の割当）は原則同等（厳密一致は必須ではないが、見た目が崩れないこと）
- API/挙動:
  - `fill()` の公開 API は変更しない
  - `_build_evenodd_groups()` の返り値形式（`list[list[int]]`）は維持する

## 実装方針

- Numba が苦手な `dict` と `list[np.ndarray]` をやめ、**配列だけを numba 関数へ渡す**。
- グルーピングは「内包判定 O(R^2)」が本質なのでアルゴリズムは変えず、**Python 側オーバーヘッドを削る**。
- メモリ爆発を避けるため、`R×R` の包含行列は基本的に作らない。

## 具体設計（案）

### 1) Python 側で作る入力配列

- `ring_idx` : `np.ndarray[int32]`（元の polyline index、長さ R）
- `ring_start` : `np.ndarray[int32]`（各リングの開始 index）
- `ring_end` : `np.ndarray[int32]`（各リングの終了 index）
- `rep_x/rep_y` : `np.ndarray[float32]`（代表点 = 第 1 頂点）
- （任意）`area_abs` : `np.ndarray[float64]`（外環候補の選別に使う）

※ `ring_idx` は戻り値の index へ戻すために保持する。

### 2) numba 側ユーティリティ

- `_point_in_polygon_coords_njit(coords2d, start, end, x, y) -> bool`
  - 既存 `_point_in_polygon_njit` と同等の「境界上は False」ロジックを維持
  - `coords2d[start:end]` の slice を作らず、index 参照で回す
- `_polygon_area_abs_coords_njit(coords2d, start, end) -> float64`
  - abs(signed area) を返す（閉じていなくても最後 → 先頭を接続）

### 3) numba 本体

- `_evenodd_parent_outer_njit(coords2d, ring_start, ring_end) -> (is_outer_u8, parent_outer)`
  - 1 回の二重ループで `contains_count` を積みつつ「最小面積の包含リング（immediate container）」を追跡する
  - `is_outer = (contains_count % 2 == 0)`
  - hole は `parent_min` を辿って outer に到達したものを親 outer とする（到達できない場合は -1）
  - 事前フィルタとして bbox を持ち、bbox 外は point-in-polygon を呼ばない

Python の `_build_evenodd_groups()` は、返ってきた `is_outer/parent_outer` を使って
現在と同じ安定順で `list[list[int]]` を構築する（出力は `ring_idx` へ戻して返す）。

## 実装チェックリスト

- [x] 1. 追加する関数のシグネチャ確定（dtype/shape）
- [x] 2. `_point_in_polygon_coords_njit` 実装（境界上 False の一致テスト付き）
- [x] 3. `_polygon_area_abs_coords_njit` 実装（簡単な正方形で面積検証）
- [x] 4. `_evenodd_parent_outer_njit` 実装（bbox 事前フィルタ + 親 outer 追跡）
- [x] 5. `_build_evenodd_groups` を「配列化 → njit → Python で組み立て」に置換
- [x] 6. 既存テスト実行（`tests/core/effects/test_fill.py` など）
- [x] 7. 追加テスト
  - [x] 正方形+穴が 1 グループになること
  - [x] テキスト "o" の穴が fill されないこと
  - [x] 「境界上 False」による誤 hole 化防止（隣接ポリゴン）
- [x] 8. 再計測（`sketch/test.py` / rings=1157）
  - [x] `_build_evenodd_groups` 時間
  - [x] `realize(fill(text))` 時間

## 事前確認したほうが良いこと（実装着手前）

- [x] A. `contains` 行列を作らない 2 パスで進めて良い？（メモリを抑える代わりに判定回数が増える）；はい
- [x] B. “見た目同等” の判定基準（テキスト穴の抜け・島の扱い・極小形状）をどこまで求める？；ドーナツ穴が正しく fill できること。o や a といったテキスト。

## 実装結果（メモ）

- 変更:
  - `src/grafix/core/effects/fill.py` の `_build_evenodd_groups` を配列化 + numba 化（二重ループを njit に移動）
  - bbox 事前フィルタを入れ、明らかに外側の候補では point-in-polygon を呼ばない
  - `tests/core/effects/test_fill.py` にテキスト穴の回帰テストを追加
- 計測（例: `sketch/test.py` の DESCRIPTION / rings=1157）
  - 変更前: `realize(fill(text))` が約 0.8 秒（= text 約 0.12 秒 + fill 約 0.7 秒）、`_build_evenodd_groups` 単体は約 0.5〜0.7 秒
  - 変更後: `_build_evenodd_groups` が約 0.002〜0.01 秒、`realize(fill(text))` が約 0.20 秒（= text 約 0.12 秒 + fill 約 0.08 秒）
