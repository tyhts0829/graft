# `src/grafix/core/effects/mirror.py` 高速化計画（2025-12-19）

## 目的

- `mirror` effect の処理時間とメモリ確保を減らす。
- 対象は `src/grafix/core/effects/mirror.py`（必要なら同ファイル内のヘルパ整理まで）。
- 指標はベンチ（`tools/benchmarks/effect_benchmark.py`）とテスト（`tests/core/effects/test_mirror.py`）の維持。

## 実データ傾向（遅い入力）

- 大きめの閉曲線（正多角形リング）
  - `verts=5001 lines=1 closed_lines=1`（`tools/benchmarks/cases.py` の `ring_big` と同等）
- 1 本の長い折れ線（頂点数が多い）
  - `verts=50000 lines=1 closed_lines=0`（`tools/benchmarks/cases.py` の `polyline_long` と同等）

方針:

- 上記 2 ケース（「1 本が長い」「閉曲線 1 本」）を主指標として最適化する。
- `many_lines`（線分が多い）専用の最適化は後回しにする（必要になったら P1/P2 に追加）。

## ベースライン計測（先にやる）

- [ ] `mirror` 単体のベンチを取得する（HTML/JSON を保存）
  - `PYTHONPATH=src python -m tools.benchmarks.effect_benchmark --only mirror --cases polyline_long,ring_big --repeats 10 --warmup 2 --disable-gc`
  - 注: 現状のベンチは `mirror` の `n_mirror=3` 固定（`tools/benchmarks/effect_benchmark.py` の overrides）
- [ ] （任意）`many_lines` も確認する（「線が多い」で遅い問題が残っていないか）
  - `PYTHONPATH=src python -m tools.benchmarks.effect_benchmark --only mirror --cases many_lines --repeats 10 --warmup 2 --disable-gc`
- [ ] 追加で `n_mirror=1/2` の支配ケースも見たい場合、手段を決める（どれか）
  - A: ベンチツールに「effect パラメータ上書き」オプションを足す（最小: `--param mirror.n_mirror=1`）
  - B: `tools/benchmarks` に `mirror` 専用の小ベンチスクリプトを追加する

## 現状メモ（ボトルネック候補）

- `_clip_polyline_halfspace()` / `_clip_polyline_halfplane()` が「頂点ごとの Python ループ」＋「点ごとの `copy()`」になっている。
  - `polyline_long`（50k 頂点）で支配的になりやすい（この 1 本のループを速くするのが効く）。
  - `ring_big`（5k 頂点・閉曲線）でも、楔/半空間で分割が起きると同様に効く。
- `_clip_polyline_halfplane()` が毎回 `normal` の正規化（`np.linalg.norm`）と `cxy` の生成をしている（ループ内で繰り返し）。
- `n_mirror>=3` は 1 本のソースから `2*n` 回の回転生成が走る（`_rotate_xy()` の呼び出し回数が増える）。
  - 各回で `sin/cos` を計算し、`out[:,0/1]` のコピーも作っている。
- `_dedup_lines()` が「全出力ラインの全頂点」を量子化して `tobytes()` をキー化するため、重複が無いケースでも `O(total_vertices)` の追加コストが必ず乗る。
  - `polyline_long` のように 1 本が長いケースで特に効きやすい。
- `show_planes=True` で `np.vstack(uniq)` を作って bbox を取っており、大きい出力だと重い（ただし既定は False）。

## 改善案（優先順）

### P0（挙動維持・実装小）: まずムダなコピーと再計算を消す

- [x] `n_mirror>=3` のくさびクリップ（halfplane）を Numba 化し、頂点ごとの `copy()` を削減する
  - ねらい: 頂点数に比例する Python 側のメモリ確保を削減
- [x] 交点重複回避の `np.allclose(..., atol=EPS)` を「3 要素の手書き比較」に置換する（Numba 内）
  - ねらい: 小配列 `allclose` の呼び出しオーバーヘッド削減
- [x] `_clip_polyline_halfplane()` を「正規化済み normal 前提」にして、`normal` 正規化と `cxy` 生成の繰り返しを避ける（Numba 版で実現）
  - ねらい: 多数ポリライン時の固定費削減
- [x] `n_mirror>=3` の回転で `sin/cos` を `m` ごとに事前計算して使い回す
  - ねらい: trig 計算の繰り返しを削減（`n<=12` でも効く可能性あり）
- [x] `src_lines` を作らず、クリップ後の `piece` から直接出力を構築する
  - ねらい: list の保持と 2 回目ループを削減

### P1（高効果候補）: `dedup` のコストを条件付きにする / 出力構築を軽くする

- [x] `_dedup_lines()` を常時実行しない設計にする（fast-path + 必要時のみ dedup）
  - 方針案:
    - 反射で「完全に同一になり得る」ケースだけ生成時にスキップする（例: `x==cx` の線は `_reflect_x` を追加しない）
    - `n>=3` でも「境界線上（楔の 2 辺）に乗っている」場合だけ dedup を掛ける
  - ねらい: 重複が無い通常ケースで `O(total_vertices)` 量子化コストを消す
- [x] （低優先）出力構築を「一括確保 + fill」に寄せる（`n_mirror>=3` 経路）
  - ねらい: `ring_big` で分割が多いケースや、将来 `many_lines` が遅い場合にも効く
  - 注意: まずは P0/P1（dedup 条件付き化）の効果を見てから判断する
- [x] `show_planes=True`（`n_mirror>=3`）の可視化線追加で巨大な `vstack` を避ける（最大半径の集計で r を決める）
  - ねらい: 可視化時の巨大な一時配列を避ける

### P2（必要なら）: Numba 化（ループ支配が残る場合）

- [x] クリップ（halfplane）を Numba カーネル化する
  - ねらい: `polyline_long` の頂点ループ支配をまとめて高速化
  - 実装方針（案）:
    - 1 パス目で「出力点数」と「分割数」を数える
    - 2 パス目で `out_coords/out_offsets` を一括確保して fill（`repeat` や `trim` と同型）
- [x] 回転・反射の生成も Numba 側で一括 fill へ寄せる（出力構築の固定費削減）
  - 注意: 初回 JIT コストがあるので、ベンチは `--warmup` 前提で比較する

## テスト追加（dedup 方針を変える場合は必須）

- [ ] `n_mirror=1` で「入力が x=cx 上の線」のとき、出力が重複しないこと
- [ ] `n_mirror=2` で「入力が x=cx または y=cy 上の線」のとき、出力が重複しないこと
- [ ] `n_mirror=3+` で「楔境界上の線」のとき、出力が重複しないこと（または仕様として許容するかを決める）

## 検証（変更ごとに回す）

- [x] テスト: `PYTHONPATH=src pytest -q tests/core/effects/test_mirror.py`
- [ ] lint/type（変更範囲に応じて）:
  - `ruff check src/grafix/core/effects/mirror.py tests/core/effects/test_mirror.py`
  - `mypy src/grafix`
- [ ] ベンチ（ベースラインと同条件で再計測して `mean_ms` 比較）
  - `PYTHONPATH=src python -m tools.benchmarks.effect_benchmark --only mirror --cases polyline_long,ring_big --repeats 10 --warmup 2 --disable-gc`

## Done の定義（受け入れ条件）

- [ ] 代表ケース（`polyline_long` / `ring_big`）で実測の改善が出ている（目標はあなたと決める）
- [x] `tests/core/effects/test_mirror.py` が通る
- [ ] 仕様（クリップ方針、境界の扱い、z 不変）が維持される（変更するならこの md に明記して合意する）

## 事前確認したいこと（あなたに質問）

- [x] 最優先で速くしたい分岐はどれか（`n_mirror=1` / `2` / `>=3`）；`>=3`
- [x] `dedup` を「条件付き」に変えるのは許容か（=通常ケースでは重複除去を省略する）；はい
- [x] Numba での高速化（初回 JIT コストあり）まで踏み込んで良いか；はい

## 実装メモ（反映済み）

- `n_mirror>=3` を「Numba くさびクリップ + Numba 回転/反射 fill + 条件付き dedup」に置換した（`src/grafix/core/effects/mirror.py`）。
