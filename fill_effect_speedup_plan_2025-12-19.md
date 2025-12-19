# fill effect 高速化計画（2025-12-19）

対象: `src/grafix/core/effects/fill.py`

## 目的

- `fill` の実行時間を短縮し、重い入力（テキスト/多輪郭/高密度）でも待ちを減らす。
- 出力の幾何（線分の位置・本数・順序）を既存テストの許容範囲で維持する。
- 依存追加なし（NumPy / 既存の numba は利用可）で、実装はシンプルに保つ。

## 前提（現状）

- 2D 化（`transform_to_xy_plane`）は numba で高速だが、塗り線生成の中核は Python ループが多い。
- 主要経路:
  - 平面判定 → 全輪郭を even-odd でグルーピング → 各グループをスキャンラインで塗る
  - 非平面の場合 → 各ポリライン単位に平面判定して塗る/スキップする
- 既存テスト: `tests/core/effects/test_fill.py` と `tests/core/test_text_fill_stability.py` が挙動の安定性を担保している。

## ボトルネック仮説（優先度順）

- `_generate_line_fill_evenodd_multi`
  - `y`（最大 ~1000）ごとに、輪郭数 × 辺数を Python で走査して交点リストを作る
  - 各スキャンラインで `np.sort` + Python でのペア化（線分生成）
- `_build_evenodd_groups`
  - 輪郭数 R に対して O(R^2) の包含判定（`_point_in_polygon` が Python ループ）
- `fill` の「全体平面性」判定
  - 候補ポリラインごとに全点へ回転を掛けて residual を計算しており、点数が多いと重い
- 生成後の `transform_back` の呼び出し回数（線分本数ぶん）と、小配列の多量生成

## 変更候補（要確認）

- 出力互換の厳しさ
  - 候補 A: 既存テストが通る範囲で OK（順序や微小差は許容）；OK
  - 候補 B: できる限り同一（順序まで固定したい）
- 高速化のために numba を `fill.py` 側にも導入してよいか（初回実行時のコンパイル増）；OK
- 「全体平面性」判定を 1 回の推定（PCA/最小二乗平面）へ置換してよいか
  - 置換する場合、面内回転自由度の固定（ハッチ方向の安定）をどう定義するか

## 実装 TODO（チェックリスト）

### 1) 計測（最初にやる）

- [ ] 代表入力セットを決める（最低限）
  - [ ] 正方形（`G.fill_test_square()`）
  - [ ] 穴つき正方形（`G.fill_test_square_with_hole()`）
  - [ ] テキスト（`G.text(text="HELLO", ...)` 相当）
  - [ ] 多輪郭・多頂点（ランダム生成、seed 固定）
- [ ] ベースライン計測スクリプトを追加（例: `tools/bench_fill.py`）
  - [ ] ケースごとの総時間（N 回平均）と polyline/vertex/segment 数を出力
  - [ ] どの関数が重いかを `cProfile` か簡易タイマで把握

### 2) Quick wins（挙動を変えない小改善）

- [ ] `fill` の全体平面性判定で `coords64 = base.coords.astype(np.float64, copy=False)` をループ外へ移動
- [ ] 角度セット `k` と `base_angle_rad` の正規化を早めに行い、不要分岐を減らす
- [ ] 交点計算用に、リングごとの `work[s:e]` スライス生成回数を減らす（事前に view を持つ等）

### 3) 交点計算の高速化（最重要）

- [ ] `_generate_line_fill_evenodd_multi` を「辺配列の前計算 + ベクトル化」に置換
  - [ ] `offsets` から全辺の `(x1,y1,x2,y2)` を 1 回で組み立てる（リング単位の wrap を含む）
  - [ ] スキャンライン `y` ごとに、全辺へ一括でマスク → 交点 `x` を算出（NumPy）
  - [ ] 既存の半開区間条件 `(y1 <= y < y2) or (y2 <= y < y1)` を同等に保つ
  - [ ] 交点 `x` の `np.sort` 後、ペア化して線分を生成（偶奇規則）
- [ ] 交点リストの Python `list` を廃止（`np.ndarray` ベースにする）
- [ ] 角度回転ありの場合の `rot_fwd` 適用を、線分 1 本ずつではなくまとめて行う（可能なら）

### 4) even-odd グルーピングの高速化（輪郭が多い入力向け）

- [ ] `_build_evenodd_groups` に bbox（AABB）事前計算を入れて、明らかに包含しない組を除外
- [ ] `_point_in_polygon` を numba 化（または同等の高速化）して包含判定コストを下げる
- [ ] 親候補探索を「面積（または bbox 面積）でソートして最小包含を探す」形へ整理（R^2 を減らす）

### 5) 全体平面性判定の見直し（点数が多い入力向け）

- [ ] 現状の「候補ポリラインごとに全点へ回転」をやめて、全点から 1 回で判定する案を比較
  - [ ] 案 A: いまのやり方を維持しつつ候補選びを改善（最大面積リングなど 1 回だけ試す）
  - [ ] 案 B: PCA/最小二乗平面を 1 回推定 → residual で判定（高速だが回転自由度の固定が課題）

### 6) 生成線分の post-process 最適化

- [ ] `seg3 = np.concatenate([...])` をやめ、(2,3) を直接確保して z=0 を埋める（小配列の削減）
- [ ] `transform_back` の呼び出し回数を減らす（線分をまとめて 1 回で戻す）方針を検討
  - [ ] まとめ戻し後に offsets を組み直す（線分は全部 2 点なので比較的簡単）

### 7) テスト/検証（対象限定で実行）

- [ ] 既存テストが通ること
  - [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_fill.py`
  - [ ] `PYTHONPATH=src pytest -q tests/core/test_text_fill_stability.py`
- [ ] 追加で「穴が多い」「輪郭が多い」ケースの回帰テストを足す（必要なら）
- [ ] 静的チェック
  - [ ] `mypy src/grafix/core/effects/fill.py`
  - [ ] `ruff check src/grafix/core/effects/fill.py`

## 事前確認したいこと

- 上の「変更候補（要確認）」で、どの方針（A/B）で進めたいか。
- numba を `fill.py` の hot path に増やすのは許容か（初回実行が重くなる可能性）。
