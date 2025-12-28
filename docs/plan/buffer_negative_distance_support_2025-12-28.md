# buffer エフェクトの「内側距離（distance<0）」対応チェックリスト（2025-12-28）

目的: `E.buffer(distance=...)` に負値を許可し、閉曲線に対して「内側側の輪郭（inner outline）」を生成できるようにする。

背景:

- 旧仕様の `buffer` は `distance<=0` を no-op 扱いにしていた（`src/grafix/core/effects/buffer.py`）。
- 閉曲線の輪郭を「外側だけでなく内側にも」オフセットしたい用途がある。

方針（案）:

- `distance>0`: 現状どおり「外側輪郭」を返す（buffer 結果の exterior）。
- `distance<0`: `abs(distance)` で buffer を作り、「内側輪郭」を返す（buffer 結果の interiors）。
  - 内側輪郭が存在しない（開曲線・太すぎて穴が潰れた等）場合は空ジオメトリを返す。
- `distance==0`: no-op（入力をそのまま返す）。

非目的:

- `cap_style` 対応
- 内側/外側の両方を同時に返すモード追加
- 「閉曲線かどうか」の厳密判定や自己交差修正などの過度な頑健化

## 0) 事前に決める（あなたの確認が必要）

- [x] `distance<0` の意味は「inner outline = holes（interiors）」で確定する；はい
- [x] `distance<0` で inner outline が存在しない場合は「空ジオメトリ」を返す（keep_original=True のときは元も追加）；はい
- [x] GUI の範囲: `distance` の `ui_min` を負値まで広げる（例: `-25.0`）；はい

## 1) 受け入れ条件（完了の定義）

- [x] `E.buffer(distance=-d)` が未登録エラーにならず、realize まで到達する
- [x] 閉曲線（例: 正方形）で `distance<0` のとき、出力 bbox が「元の内側」へ縮む（定性的で良い）
- [x] 開曲線（例: 2 点線分）で `distance<0` のとき、出力が空（または keep_original=True なら元のみ）
- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer_negative_distance.py`
- [x] `python -m tools.gen_g_stubs` 後に `tests/stubs/test_api_stub_sync.py` が通る
- [x] `mypy src/grafix`
- [ ] `ruff check .`（環境に ruff がある場合）

## 2) 仕様案（API/パラメータ）

- 既存 API を維持: `buffer(inputs, *, join="round", quad_segs=12, distance=5.0, keep_original=False)`
- `distance`:
  - `>0`: exterior
  - `<0`: interiors（inner outline）
  - `==0`: no-op

## 3) 実装設計（アルゴリズム）

- [x] `d = float(distance)` を取得
- [x] `d == 0` は no-op
- [x] `abs_d = abs(d)` を使って Shapely buffer を作る（現状と同じ `LineString(...).buffer(abs_d, ...)`）
- [x] `d > 0` の場合:
  - [x] 従来どおり exterior だけ抽出して out_lines に積む
- [x] `d < 0` の場合:
  - [x] `Polygon.interiors`（および MultiPolygon の各 poly の interiors）を抽出して out_lines に積む
  - [x] out_lines が空なら空ジオメトリ（keep_original=True なら元のみ）
- [x] 抽出関数の整理:
  - [x] `_extract_vertices_2d()` を「exterior 専用」から「exterior/interior を選べる」形にする（引数 `which="exterior"|"interior"` 等）
  - [x] 既存挙動（distance>0）を壊さないテストを維持

## 4) 変更箇所（ファイル単位）

- [x] `src/grafix/core/effects/buffer.py`
  - [x] `buffer_meta["distance"].ui_min` を負値へ（例: `-25.0`）
  - [x] `distance<=0` の no-op 判定を `distance==0` に変更
  - [x] interior 抽出ロジックを追加
  - [x] docstring に `distance<0` の意味を追記
- [x] テスト追加: `tests/core/effects/test_buffer_negative_distance.py`
- [x] スタブ再生成: `python -m tools.gen_g_stubs`（手編集しない）

## 5) テスト観点（最小）

- [x] 閉曲線（正方形）+ `distance=-0.1` で bbox が内側へ（min が増え、max が減る）
- [x] `distance=-0.1` と `distance=+0.1` で「方向が反対」になっていること（定性的で良い）
- [x] 開曲線（既存の 2 点線分）+ `distance<0` で空（または keep_original=True で元だけ）
- [x] `keep_original=True` のとき、buffer 結果に加えて入力が append される

## 6) 実行コマンド（ローカル確認）

- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_buffer_negative_distance.py`
- [x] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py`
- [x] `mypy src/grafix`
- [ ] `ruff check .`（任意）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] 「閉曲線判定」を `AUTO_CLOSE_THRESHOLD` に依存させるか、明示的に start==end を要求するか
- [ ] `distance<0` のときだけ `join` の解釈を変える必要があるか（基本は同一で良さそう）
