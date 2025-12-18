# Text 輪郭ポリラインの開始点ローテーション（fill 安定化 / 実装計画 / 2025-12-18）

目的:

- `G.text(...)` の輪郭ポリラインが **直線上の点（共線）から始まる**ケースで、`E.affine(...).fill()` が角度によって塗れたり塗れなかったりする問題を軽減する。
- 方針: **輪郭ポリラインの開始インデックスを回して、先頭 3 点が非共線になる（できれば角=曲率の大きい位置）**ようにする。
  - 形状は変えず、点列の “開始位置” だけ変える（視覚的に同一）。

背景（なぜ起きるか）:

- `fill` は 3D 入力でも「平面なら 2D に整列してハッチ生成」する。`transform_to_xy_plane()` が **先頭 3 点**で法線を推定する。
- text 輪郭は「長い直線エッジ上」から始まることがあり、先頭 3 点が共線になって法線が作れず、平面判定が不安定になる。
- `polygon` は先頭 3 点が非共線になりやすく、この問題が出にくい。

非目的:

- `fill` の平面推定アルゴリズム自体を修正する（今回は text 側の対策）。
- 点を微小に “ずらす”（幾何を変える）方式は採用しない。

---

## 実装方針（やること）

### A) 対象

- `src/grafix/core/primitives/text.py` が生成する **各輪郭ポリライン（閉曲線）**に対して適用する。
- 開曲線（閉じていない polyline）には適用しない（始端/終端の意味が変わりうるため）。

### B) いつ適用するか

- glyph コマンドから polyline を作った直後（`closePath` flush のタイミング）で適用する。
  - この段階なら「閉曲線かどうか」も判定しやすい。

### C) “角の近く” の決め方（開始点選択）

閉曲線の点列 `P[0..n-1]`（※ `P[0]==P[n-1]` になっている想定）について:

1. `P_unique = P[:-1]` として “重複終端” を一旦外す（長さ `m=n-1`）。
2. 候補インデックス `i = 0..m-1` を走査し、連続 3 点 `(a, b, c) = (P_unique[i], P_unique[(i+1)%m], P_unique[(i+2)%m])` で 2D の面積（外積）を計算する:
   - `area2 = abs(cross2(b-a, c-a))`（XY のみ）
3. `area2` が最大の `i_best` を開始点にする（= “最も曲がっている” 近傍を優先）。
4. ただし `max(area2)` が閾値以下（ほぼ直線）なら、ローテーションはしない（無理に回しても効果が薄い）。
   - 閾値は相対スケール依存を避けるため `eps = 1e-8` のような小さい定数 + 必要なら `|b-a|` などで正規化を検討。

この方式の利点:

- 「非共線な 3 点」になるだけでなく、**三角形面積が大きい点**が先頭に来るため、`fill` の平面推定が数値的に安定しやすい。
- 点列の順序（CW/CCW）は保持される。

### D) ローテーションのやり方（閉曲線を壊さない）

- `P_unique` を `i_best` で回転: `Q = P_unique[i_best:] + P_unique[:i_best]`
- 閉じを戻す: `Q_closed = Q + [Q[0]]`
- 元の polyline と同じ shape `(N,3)` で返す（z はそのまま）

---

## 実装チェックリスト

### 1) ヘルパ関数追加

- [x] `src/grafix/core/primitives/text.py` に `_rotate_closed_polyline_start_for_fill(polyline: np.ndarray) -> np.ndarray` を追加
  - [x] 入力は `(N,3)` float32 を想定（内部計算は float64）
  - [x] `N < 4`（= ユニーク点が 3 未満）なら何もしない
  - [x] `P[0]` と `P[-1]` が一致（または十分近い）しない場合は何もしない
  - [x] `max(area2)` が閾値以下なら何もしない
  - [x] それ以外は上記ローテーションを適用して返す

### 2) text 生成パスへ組み込み

- [x] glyph→polyline 生成（`closePath` flush）で、生成した閉曲線に対してヘルパを適用する
- [x] 出力の polyline 群を `RealizedGeometry` 化する既存フローは維持する

### 3) 退行テスト（現象の再現と改善の確認）

- [x] `tests/core/test_text_fill_stability.py`（仮）を追加
  - [x] `base = G.text(text="HELLO", font="SFNS.ttf", scale=(100,100,1))`
  - [x] `boundary_count = realize(base).offsets.size - 1`
  - [x] 問題が出やすい角度例（rx=105/120/135）で
    - [x] `out = realize(E.affine(rotation=(rx,0,0)).fill()(base))`
    - [x] `out_count = out.offsets.size - 1`
    - [x] `out_count > boundary_count`（= fill 線が生成されている）を確認
  - [x] 既存の `text` テストは維持（align/center/scale）

### 4) 確認コマンド

- [x] `PYTHONPATH=src pytest -q tests/core/test_text_primitive.py`
- [x] `PYTHONPATH=src pytest -q tests/core/test_text_fill_stability.py`（追加後）

---

## リスク / 注意点

- これは `fill` の実装都合に合わせた “入力整形” なので、他の primitive でも同様の問題は残る。
- ただし **点の集合・辺の集合は変えず**、閉曲線の “開始点” だけ変えるため、描画・SVG の形状は原理的に不変（視覚的に同一）になる想定。

---

## 事前確認（これで進めてよいか）

- 「閉曲線のみ開始点を回す」「角（最大 area2）を開始点にする」「閾値以下は no-op」の方針で OK か？
- 退行テストの角度セット（rx=105/120/135）はこのままで良いか？
