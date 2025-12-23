# fill エフェクト numba 化 実装計画（2025-12-23）

- どこで: `src/grafix/core/effects/fill.py`（現行 fill）/ `tests/`（検証追加）
- 何を: `fill` のボトルネック（外環＋穴グルーピング）を numba 化して高速化する
- なぜ: `sketch/test.py` のテキスト輪郭（約 1157 rings）で `fill` が 10〜30 秒級に重い（内包判定が純 Python で O(R^2)）

## 現状整理（観測事実）

- 現行の遅さは、`_build_evenodd_groups()` が `_point_in_polygon()` を R×(R-1) 回（例: 1157×1156=1,337,492 回）呼ぶため。
- 旧プロジェクトは同じ総当たりでも `point_in_polygon` が `@njit` で、同入力のグルーピングが **約 0.3 秒** 程度で完了する。

## 目標（Done の定義）

- `sketch/test.py` の `DESCRIPTION` を `G.text()`→`E.fill()` したケースで、
  - グルーピング（外環＋穴）の処理が **(warm) 1 秒未満** を目安に収まる
  - `fill` 出力のジオメトリ（少なくとも ring grouping と境界扱い）が現行と一致（または許容差を明示）
- 既存 API（`E.fill(...)` の引数/返り値）を変えない

## 方針

- `fill` 全体の njit 化は狙わず、**ボトルネック部分のみ numba 化**する。
- numba が苦手な `dict`/可変長 list を避け、**数値配列で返せる中間結果（親リング割当など）**に落とす。

## 実装チェックリスト（提案）

- [x] 1. ベースライン計測の固定（再現用）

  - [x] `sketch/test.py` を入力に、(A) `G.text` の realize、(B) `fill(text)` の realize、(C) グルーピング単体の時間を計測
  - [x] cProfile で支配セクションが `_build_evenodd_groups` であることを再確認

- [x] 2. 現行グルーピング仕様の確定（差分が出やすい所）

  - [x] 代表点は「第 1 頂点」を使う（現状通り）
  - [x] `_point_in_polygon` の「境界上は False」挙動を維持（現行踏襲）
  - [x] 浮動小数誤差（eps=1e-6）を踏襲

- [x] 3. numba 版 `point_in_polygon` の作成

  - [x] `@njit(cache=True)` の 2D 判定関数を実装（入力: `polygon2d`, `x`, `y`）
  - [x] 「境界上 False」を再現するための on-vertex / on-segment 判定を組み込み（現行ロジック踏襲）

- [x] 4. グルーピング二重ループの numba 化（or 低侵襲差し替え）

  - **案 A（低侵襲）**:
    - [x] Python の `_build_evenodd_groups` 構造は維持し、内包判定だけ `njit` 関数に差し替える
  - **案 B（推奨）**:
    - [ ] ring を `ring_start[]/ring_end[]/rep_xy[]/area[]` の配列に正規化
    - [ ] `@njit` で `contains_count[]` と `parent_outer[]`（hole の所属 outer）を返す
    - [ ] Python 側で `parent_outer` から `list[list[int]]` に組み立て（順序安定化は Python で行う）

- [x] 5. 検証（テスト）

  - [x] 小さい手作り形状で grouping/境界扱いを担保する pytest を追加
  - [x] `G.text(text="...")` の実データで動作と出力を手元確認（自動テスト化は未実施）
  - [x] 旧実装ではなく、**現行挙動（境界上 False）維持**を優先

- [x] 6. 性能確認

  - [x] `sketch/test.py` 入力で、グルーピング時間と `fill` 全体時間を再計測
  - [ ] `n_worker>1`（mp-draw）でも初回/2 回目の体感を確認（JIT キャッシュの効き方を観察）

- [ ] 7. ドキュメント最小追記（必要なら）
  - [ ] 「初回は numba JIT で遅いが 2 回目以降速い」など、挙動が気になる場合のみ短くメモを追加

## 事前確認したいこと（あなたの希望）

- [x] A. 「境界上は False」挙動は現行踏襲で良い？（速度優先で旧実装寄りに戻す選択肢もある）；はい
- [x] B. 出力の完全一致を必須にする？それとも“見た目が同等なら OK”？；見た目同等なら OK
- [x] C. 目標は「warm を速く」で良い？（cold=初回コンパイルは多少遅くても許容するか）；はい

## 実装結果（メモ）

- 変更:
  - `src/grafix/core/effects/fill.py` の `_point_in_polygon` を Numba 化（境界上 False のロジックは踏襲）
  - `tests/core/effects/test_fill.py` に境界判定の回帰テストを追加
- 計測（例: `sketch/test.py` の DESCRIPTION, rings=1157）:
  - 変更前: `fill(text)` が約 17 秒（支配は `_build_evenodd_groups`）
  - 変更後: `fill(text)` が約 0.7 秒、`_build_evenodd_groups` が約 0.5〜0.7 秒

## 追加提案（必要になったら）

- N^2 を根本的に減らす（bbox 事前フィルタ/空間インデックス）案もあるが、まずは numba 化で定数倍を削るのが最短。
