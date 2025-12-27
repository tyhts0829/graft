# mypy エラー原因と改善案（2025-12-27）

実行コマンド: `mypy src/grafix`  
結果: `Found 13 errors in 7 files (checked 116 source files)`

## 全体傾向（先に直すと効率が良いもの）

- **依存ライブラリの型情報不足/不整合**（`numba`, `shapely`, `fontTools`, `pyglet`）。→ 方針は「(A) stub を入れる」「(B) 局所 `type: ignore[...]`」「(C) `typings/` に最小 stub を置く」のどれかに寄せるのがきれい。

---

## ファイル別の詳細

### `src/grafix/core/parameters/style.py`

- L50 `[call-overload]` `No overload variant of "int" matches argument type "object"`
  - 原因: `_clamp(v: object)` の `v` が `object` すぎて `int(v)` 可能だと型的に言えない。
  - 改善案:
    - **推奨**: `value` を「長さ 3 の数値シーケンス」として受け、アンパック前に `Sequence` + `len==3` をチェックする（`int/float` へ正規化してから clamp）。
    - 代替: `_clamp(v: int | float)` にして、呼び出し側で `float(...)` / `int(...)` へ寄せる。

- L53 `[has-type]` `Cannot determine type of "r" / "g" / "b"`
  - 原因: `r, g, b = value  # type: ignore[...]` の結果、`r/g/b` の型が確定しない。
  - 改善案:
    - **推奨**: 上_confirm_（シーケンス判定＋長さチェック）の中で `r0/g0/b0` を取り出し、`_clamp(float(r0))` のように型を確定させる。

---

### `src/grafix/core/realized_geometry.py`

- L96 `[var-annotated]` `Need type annotation for "new_offsets"`
  - 原因: `new_offsets = []` だと要素型が推論できない。
  - 改善案:
    - **推奨**: `new_offsets: list[int] = []`（`offsets` は index の列なので `int` が自然）。

---

### `src/grafix/core/effects/partition.py`

- L344 `[no-redef]` `Name "loops_2d" already defined on line 336`
  - 原因: `if` ブランチで `loops_2d = ...` を作った後、`else` 側で `loops_2d: list[np.ndarray] = []` と **注釈付きで再定義**している。
  - 改善案:
    - **推奨**: `loops_2d: list[np.ndarray]` を分岐の前で宣言し、各分岐では代入だけにする。
    - 代替: 片方の注釈を外す（ただし読みやすさは落ちる）。

---

### `src/grafix/core/effects/fill.py`

- L867 `[no-redef]` `Name "out_lines" already defined on line 845`
  - 原因: `if not groups:` ブランチ内で `out_lines: list[np.ndarray] = []` を定義し、ブランチ外でも `out_lines: list[np.ndarray] = []` を定義している（注釈付き再定義扱い）。
  - 改善案:
    - **推奨**: 分岐の前で `out_lines: list[np.ndarray]` を宣言し、各分岐は代入だけにする。
    - 代替: 片方の注釈を外す。

---

### `src/grafix/core/effects/dash.py`

- L264/L331/L345 `[assignment]` `expression has type "int", variable has type "signedinteger[_32Bit | _64Bit]"`
  - 原因: `np.searchsorted` などの戻り値（numpy/numba の整数型）を使って作った変数へ `0`（Python int）を代入しており、mypy 的に代入互換が取れない。
  - 改善案:
    - **推奨**: インデックス計算は `int(...)` で Python int に寄せる（例: `s_idx = int(np.searchsorted(...))`、`s0 = s_idx - 1`、`if s0 < 0: s0 = 0`）。
    - 代替: `np.int64(0)` のように numpy 側の整数型へ寄せる（ただし型が散らばりやすい）。

---

### `src/grafix/api/export.py`

- L68 `[arg-type]` `Argument "canvas_size" ... has incompatible type "tuple[int, ...]"; expected "tuple[int, int] | None"`

  - 原因: `tuple(canvas_size)` によって、固定長 `tuple[int, int]` が **可変長 `tuple[int, ...]` に型が広がる**。
  - 改善案:
    - **推奨**: 変換せずそのまま渡す（`canvas_size=canvas_size`）。
    - 代替: 入力が sequence の可能性があるなら、`(int(canvas_size[0]), int(canvas_size[1]))` のように **2 要素のタプルを明示的に作る**（長さチェック付き）。

- L74 `[arg-type]` `export_image(... canvas_size=...)` も同様
  - 原因/改善案: L68 と同じ。

---

### `src/grafix/interactive/runtime/draw_window_system.py`

- L146 `[assignment]` `expression has type "dict[int, float] | None", variable has type "dict[int, float]"`
  - 原因: `cc_snapshot` が `midi.snapshot()` 由来で `dict[int, float]` と推論される一方、`else` 側の `_frozen_cc_snapshot` が `None` になり得る（Optional）ため型が合わない。
  - 改善案:
    - **推奨**: `_frozen_cc_snapshot` を常に辞書にする（例: デフォルト `{}`、または `self._frozen_cc_snapshot or {}` で代入）。
    - 代替: `cc_snapshot: dict[int, float] | None` にして後段で `None` をハンドリングする。

---

## 修正方針の候補（チェックリスト）

このレポートは「提案」まで。実際に直す場合の進め方案だけ置いておく。

- [ ] `api/export.py` の `tuple(canvas_size)` を撤去（低リスク）
- [ ] `partition.py` / `fill.py` の `no-redef` を解消（注釈位置の整理）
- [ ] `style.py` の `coerce_rgb255` を型が付く形に整理（引数型の絞り込み）
- [ ] `realized_geometry.py` の `new_offsets` に型注釈を付ける
- [ ] `dash.py` の index 計算を `int(...)` に寄せる（mypy の assignment 解消）
- [ ] `draw_window_system.py` の `cc_snapshot` 型を揃える（Optional の扱いを決める）
