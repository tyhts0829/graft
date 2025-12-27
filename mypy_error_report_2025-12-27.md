# mypy エラー原因と改善案（2025-12-27）

実行コマンド: `mypy src/grafix`  
結果: `Found 28 errors in 11 files (checked 115 source files)`

## 全体傾向（先に直すと効率が良いもの）

- **変数名の再利用による型の衝突**が多い（例: `x` を「スカラー」と「np.ndarray」の両方で使う、`base` を `RealizedGeometry` と `int` で使う等）。→ **変数名の整理だけで複数エラーが同時に消える**。
- **依存ライブラリの型情報不足/不整合**（`numba`, `shapely`, `fontTools`, `pyglet`）。→ 方針は「(A) stub を入れる」「(B) 局所 `type: ignore[...]`」「(C) `typings/` に最小 stub を置く」のどれかに寄せるのがきれい。

---

## ファイル別の詳細

### `src/grafix/core/realized_geometry.py`

- L96 `[var-annotated]` `Need type annotation for "new_offsets"`
  - 原因: `new_offsets = []` だと要素型が推論できない。
  - 改善案:
    - **推奨**: `new_offsets: list[int] = []`（`offsets` は index の列なので `int` が自然）。

---

### `src/grafix/core/primitives/sphere.py`

このファイルは **変数名の再利用**が原因で型が衝突している。

- L315 `[assignment]` `expression has type "float", variable has type "ndarray[...]"`

  - 原因: 先行ブロックで `x` が `np.ndarray` になった後、別ブロックで `x = ...`（float）として再代入している。
  - 改善案:
    - **推奨**: `x`（スカラー）を `x_pos`、配列は `xs` のように分離する（同様に `y/z` も）。

- L316 `[arg-type]` `Argument 1 to "sqrt" has incompatible type ...`

  - 原因: 上記の型衝突に引きずられて `max(0.0, _RADIUS*_RADIUS - x*x)` が「float」だと確定できていない。
  - 改善案:
    - **推奨**: 変数名の分離で `x` が常に `float` になれば自然に解決する。

- L324 `[assignment]` `expression has type "ndarray[...]", variable has type "float"`

  - 原因: 別ブロックで `y` が float として使われた後に `y = (radius * np.cos(...)).astype(...)`（配列）へ再代入している。
  - 改善案:
    - **推奨**: `y_pos`（float）と `ys`（配列）へ分離。

- L330 `[assignment]` `expression has type "float", variable has type "ndarray[...]"`

  - 原因: `z` の float/配列の再利用が `x` と同様に衝突している。
  - 改善案:
    - **推奨**: `z_pos` と `zs` に分離。

- L331 `[arg-type]` `Argument 1 to "sqrt" has incompatible type ...`

  - 原因: `z` の型が確定せず、`sqrt` の引数が数値だと保証できない。
  - 改善案:
    - **推奨**: 変数名の分離で解消。

- L340 `[assignment]` `expression has type "ndarray[...]", variable has type "float"`
  - 原因: `y` を float と配列で再利用している影響の後続。
  - 改善案:
    - **推奨**: `ys` へリネーム。

---

### `src/grafix/core/realize.py`

- L74 `[assignment]` `Callable[[Sequence[RealizedGeometry], ...], ...]` を `Callable[[tuple[tuple[str, Any], ...]], ...]` へ代入している

  - 原因: `func` 変数に対して
    - primitive: `PrimitiveFunc(args) -> RealizedGeometry`
    - effect: `EffectFunc(inputs, args) -> RealizedGeometry`
      を同名で入れており、**同一変数の型が揃わない**。
  - 改善案:
    - **推奨**: 変数を分ける（`primitive_func` / `effect_func` など）。
    - 代替: `Callable[..., RealizedGeometry]` に落とす（型精度が落ちるので非推奨）。

- L75 `[call-arg]` `Too many arguments`

  - 原因: mypy が `func` を primitive 側の 1 引数 callable と解釈している状態で、`func(realized_inputs, geometry.args)` と 2 引数で呼んでいる。
  - 改善案:
    - **推奨**: 上と同じく `func` 変数の分離。

- L75 `[arg-type]` `Argument 1 has incompatible type "list[RealizedGeometry]" ...`
  - 原因: 上記と同根（`func` の解釈が primitive になっている）。
  - 改善案:
    - **推奨**: `effect_func(realized_inputs, geometry.args)` のように effect 用 callable で呼ぶ。

---

### `src/grafix/core/effects/partition.py`

- L344 `[no-redef]` `Name "loops_2d" already defined on line 336`
  - 原因: `if` ブランチで `loops_2d = ...` を作った後、`else` 側で `loops_2d: list[np.ndarray] = []` と **注釈付きで再定義**している。
  - 改善案:
    - **推奨**: `loops_2d: list[np.ndarray]` を分岐の前で宣言し、各分岐では代入だけにする。
    - 代替: 片方の注釈を外す（ただし読みやすさは落ちる）。

---

### `src/grafix/core/effects/mirror.py`

- L197 `[assignment]` `expression has type "int", variable has type "RealizedGeometry"`

  - 原因: 同一関数内で `base = inputs[0]`（`RealizedGeometry`）とした後、別の場所で `base = int(...)` と **別用途で再代入**している。
  - 改善案:
    - **推奨**: `base`（入力ジオメトリ）と `offset_base`（int）など、役割で変数名を分ける。

- L199 `[operator]` `ndarray.__radd__` が `RealizedGeometry` を受け取れない

  - 原因: L197 の影響で `base` が `RealizedGeometry` 扱いになり、`base + ln * np.arange(...)` が型破綻している。
  - 改善案:
    - **推奨**: L197 の変数名衝突を解消（副作用で解決するはず）。

- L309 `[assignment]` `expression has type "ndarray[...]", variable has type "int"`

  - 原因: 先行箇所で `ln = int(...)` を使っているのに、後半で `for i, ln in enumerate(uniq, ...)` と **同名 `ln` に ndarray を入れている**。
  - 改善案:
    - **推奨**: 後半ループの `ln` を `line` 等に変更。

- L310 `[attr-defined]` `"int" has no attribute "shape"`
  - 原因: L309 と同根で、mypy が `ln` を `int` と解釈している。
  - 改善案:
    - **推奨**: L309 の変数名衝突解消。

---

### `src/grafix/core/effects/fill.py`

- L867 `[no-redef]` `Name "out_lines" already defined on line 845`
  - 原因: `if not groups:` ブランチ内で `out_lines: list[np.ndarray] = []` を定義し、ブランチ外でも `out_lines: list[np.ndarray] = []` を定義している（注釈付き再定義扱い）。
  - 改善案:
    - **推奨**: 分岐の前で `out_lines: list[np.ndarray]` を宣言し、各分岐は代入だけにする。
    - 代替: 片方の注釈を外す。

---

### `src/grafix/interactive/parameter_gui/store_bridge.py`

- L161 `[assignment]` `expression has type "tuple[str, str]", variable has type "tuple[str, int]"`

  - 原因: 直前のループで `group_key` が `tuple[str, int]`（例: `(op, ordinal)`）として推論され、その後の `for group_key, ... in other_blocks.items():` で `tuple[str, str]`（例: `(op, site_id)`）を代入して衝突している。
  - 改善案:
    - **推奨**: ループ変数名を分ける（`primitive_key` / `other_key` など）。

- L162 `[assignment]` `expression has type "int", variable has type "str"`
  - 原因: 上記の推論汚染の結果、`op, site_id = group_key` の `site_id` が `int` 扱いになっている。
  - 改善案:
    - **推奨**: L161 の変数名衝突を解消。

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

## 修正方針の候補（チェックリスト）

このレポートは「提案」まで。実際に直す場合の進め方案だけ置いておく。

- [ ] `sphere.py` / `mirror.py` / `store_bridge.py` / `realize.py` の **変数名衝突**を解消（低リスクで大量に消える）
- [ ] `api/export.py` の `tuple(canvas_size)` を撤去（低リスク）
- [ ] `partition.py` / `fill.py` の `no-redef` を解消（注釈位置の整理）
