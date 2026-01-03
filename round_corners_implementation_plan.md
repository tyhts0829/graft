# 角丸（round_corners / fillet）effect 実装計画

目的: 少ない頂点数のポリライン/ポリゴンでも、頂点ごとの「角」を円弧で置き換えて角丸を作る effect を追加する。

## ゴール

- [ ] `E.round_corners(...)`（または `E.fillet(...)`）で角丸ポリラインを生成できる
- [ ] 閉曲線（`G.polygon()` 等）で全頂点に一貫して角丸がかかる
- [ ] 端点を持つ開曲線は、端点は保持し、内側頂点のみ角丸がかかる
- [ ] 半径 0 / 分割数 0 相当は no-op（入力をそのまま返す）
- [ ] 典型的な入力で self-intersection を増やしにくい（必要十分なクランプ）

## 非ゴール（今回やらない）

- [ ] `relax` の仕様変更で角丸を代替する
- [ ] Shapely の `buffer` を用いた「輪郭生成」で角丸を代替する（線そのものを角丸にするのが目的）
- [ ] 非平面（3D 空間でねじれた）ポリラインの厳密フィレット

## 公開 API（案）

候補 1: `round_corners`

- [ ] effect 名: `round_corners`
- [ ] 引数:
  - [ ] `radius: float = 2.0`（角丸半径 [mm]）
  - [ ] `segments: int = 8`（円弧分割数。大きいほど滑らか）
  - [ ] `closed: str = "auto"`（`"auto" | "open" | "closed"`）
  - [ ] `close_threshold: float = 1e-3`（`closed="auto"` の判定閾値）

候補 2: `fillet`（短いが CAD 用語寄り）

- [ ] effect 名: `fillet`
- [ ] 引数は候補 1 と同じ（名称だけ変更）

## 仕様（案）

- [ ] 入力は `RealizedGeometry`（複数ポリライン可）で、各ポリラインを独立に処理する
- [ ] `closed="auto"` の場合、始点終点が `close_threshold` 以内なら閉曲線として扱う
- [ ] 閉曲線は出力も閉曲線にする（先頭点を末尾に複製して閉じる）
- [ ] 開曲線は先頭/末尾点はそのまま残し、内側頂点のみ角丸対象にする
- [ ] 半径が大きすぎる場合は、隣接セグメント長に基づいて半径をクランプする

## アルゴリズム方針（2D フィレットで実装）

前提: 角丸は「局所処理」であり、各頂点の前後 2 セグメントから円弧を構成する。

- [ ] 各ポリラインを平面へ射影して 2D（XY）で処理し、戻す
  - [ ] 実装は `src/grafix/core/effects/util.py` の `transform_to_xy_plane` / `transform_back` を使用する方針
  - [ ] 射影後は `z=0` 前提で `x,y` のみでフィレット計算する
- [ ] 各頂点 `V`（開曲線は端点除く、閉曲線は全点）について:
  - [ ] `prev=P`, `next=N` を取り、方向 `d1 = normalize(V-P)`, `d2 = normalize(N-V)` を作る
  - [ ] 角度 `phi = angle(d1, d2)` を計算し、`phi≈0`（直線）や `phi≈π`（U ターン）ではスキップする
  - [ ] タンジェント距離 `t = r * tan(phi/2)` を用い、`t` がセグメント長を超える場合は `r` を縮める
  - [ ] タンジェント点 `T1 = V - d1*t`, `T2 = V + d2*t` を作る
  - [ ] 中心 `C` は `u=-d1`, `v=d2`, `bis=normalize(u+v)`, `h=r/sin(phi/2)` として `C = V + bis*h`
  - [ ] 回転方向は `cross2(d1, d2)` の符号で決め、`T1→T2` の円弧を `segments` 分割でサンプリングする
- [ ] 隣接コーナーが同一セグメント上で「食い込む」場合（`t_out + t_in > seg_len`）の解決:
  - [ ] まずは「両者を同率で縮める」簡単な再スケールで重なりを解消する（複雑な最適化はしない）

## 実装タスク（チェックリスト）

- [ ] 命名と引数仕様を確定（下の「事前確認」）
- [ ] `src/grafix/core/effects/round_corners.py`（or `fillet.py`）を新規追加
  - [ ] module docstring（どこで/何を/なぜ）
  - [ ] `*_meta` 定義（`ParamMeta` の ui_min/ui_max を決める）
  - [ ] 2D フィレットの小関数（ポリライン 1 本 → 2D 点列）
  - [ ] `@effect(meta=...)` の公開 effect 関数（`inputs: Sequence[RealizedGeometry]`）
- [ ] 既存 `RealizedGeometry` 形式（`coords`, `offsets`）に合わせて複数ポリラインを連結して返す
- [ ] 角丸後の点数増加が過大にならない上限を入れる（例: 1 コーナーあたり最大 `segments<=64`）
- [ ] `tests/core/effects/test_round_corners.py` を追加
  - [ ] 半径 0 で no-op
  - [ ] `G.polygon(n_sides=6)` 相当の閉曲線で、全頂点が角丸で増点される（点数が増える）
  - [ ] 開曲線（L 字）で、端点が維持される
  - [ ] 半径が大きすぎる場合に破綻せずクランプされる（NaN が出ない、点数が暴走しない）
- [ ] スタブ再生成（公開 API 追加のため）
  - [ ] `python -m tools.gen_g_stubs`
  - [ ] `pytest -q tests/stubs/test_api_stub_sync.py`
- [ ] 最小の静的チェック
  - [ ] `ruff check src/grafix/core/effects/round_corners.py`
  - [ ] `mypy src/grafix`（必要なら対象限定でも可）

## 事前確認（あなたに決めてほしいこと）

- [ ] effect 名は `round_corners` と `fillet` のどちらが良い？；fillet
- [ ] `segments` は「円弧の分割数（角ごと）」で良い？（別案: `segments_per_quadrant`）；はい
- [ ] `closed` 引数は必要？（不要なら「auto 判定のみ」で固定してシンプル化する）；シンプルで
- [ ] 半径が大きいときの挙動: 「自動で最大半径へクランプ」で良い？それとも「その角だけ角丸しない」が良い？；あなたの推奨で。

## 追加提案（後回しでよい）

- [ ] 端点のキャップ（`cap: "butt" | "round"`）を追加して、開曲線の端も丸められるようにする
- [ ] 角丸の品質を「最大偏差」基準で自動決定する（現状は固定 `segments`）
- [ ] 平面推定ロジックを共通化（`buffer.py` の射影コードと統合）して重複を減らす
