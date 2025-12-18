# 目的

`src/grafix/interactive/gl/index_buffer.py` の `build_line_indices()`（`offsets -> GL_LINE_STRIP + primitive restart 用 indices`）について、cache miss 時のインデックス生成を `numba.njit` で高速化する。

## 背景/前提

- 現状は `_build_line_strip_indices_cached()` 内で Python ループがあり、polyline 数が多い/offsets が巨大な場合に CPU が支配しうる。
- ただし `build_line_indices()` は LRU キャッシュのキーとして `offsets.tobytes()` を作るため、このコピーが支配になるケースでは numba 化だけでは改善しない。
- まずは「cache miss 時の生成」だけを最小差分で numba 化する（キー方式は変えない）。

# スコープ

## やる

- `_build_line_strip_indices_cached()` の中核（count/fill）を numba 2-pass に置き換える。
- 既存の仕様（short polyline をスキップ、polyline 間に `LineMesh.PRIMITIVE_RESTART_INDEX` を挿入、戻り値は read-only）を維持する。
- 既存テスト `tests/interactive/test_index_buffer.py` を通す。

## やらない（今回）

- `offsets.tobytes()`（キャッシュキー生成）の削減/別キー化。
- indices キャッシュ戦略（maxsize、キーの持ち方等）の設計変更。
- perf_sketch の新ケース追加（必要になったら別 issue/plan）。

# 実装チェックリスト

- [x] `src/grafix/interactive/gl/index_buffer.py` に numba カーネルを追加する（2-pass: count -> allocate -> fill）。
  - [x] 入力: `offsets: int32[:]`（`np.frombuffer` 由来で OK）
  - [x] 出力: `uint32[:]`
  - [x] polyline 長 `length < 2` はスキップ
  - [x] 2 本目以降の polyline の前に `PRIMITIVE_RESTART_INDEX` を 1 つ挿入
- [x] `_build_line_strip_indices_cached()` から numba 経路を呼び、戻り値を read-only にする。
- [x] `PYTHONPATH=src pytest -q tests/interactive/test_index_buffer.py` を実行して一致を確認する。
- [ ] （任意）`GRAFIX_PERF=1` の `perf_sketch` で `indices` が支配するケースが出る場合、再計測する（初回 numba コンパイル時間は除外）。

# 再計測タイミング（目安）

- `indices` がフレーム時間の主要因（例: 2ms/frm 以上）になった/なりそうなタイミングで再計測する。
- numba 初回コンパイルはヒッチとして出るため、**2 回目以降のログ**で比較する。
