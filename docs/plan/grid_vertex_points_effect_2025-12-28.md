# グリッド量子化（座標スナップ）エフェクト実装チェックリスト（2025-12-28）

目的: 入力ジオメトリの頂点座標を格子点へ量子化（snap）し、形状をグリッドに揃える新規 effect を追加する。

背景:

- `G.grid()` は「グリッド線」を生成できるが、任意の入力ジオメトリをグリッドに揃える（座標の量子化/スナップ）手段がない。
- “座標をカクつかせる / ピクセル化する / ステップ状にする” 目的で、頂点の丸めが欲しい。

方針（今回の決定案）:

- effect 名は `grid_quantize`（仮）とし、`E.grid_quantize(...)` で使えるようにする。
- 最小仕様として「頂点数と offsets は変えない」。`coords` のみを量子化して返す（同一点の連続や 0 長セグメントは許容）。
- 実装は `src/grafix/core/effects/` の新規モジュール 1 枚で完結させ、他 effect への依存はしない（`util.py` の利用は可）。

非目的:

- ジオメトリ簡略化（連続重複点の除去、自己交差修正など）の自動実行
- export/描画の仕様変更（点要素追加や 0 長セグメントの特別扱い）
- 互換ラッパー/シムで旧仕様を延命すること

## 0) 事前に決める（あなたの確認が必要）

- [ ] effect 名（案: `grid_quantize` / `snap_grid` / `quantize`）
- [ ] 対象軸
  - [ ] A: `xy` のみ量子化（推奨、2D 作図用途）
  - [ ] B: `xyz` を量子化（3D もカクつかせたい用途）
- [ ] グリッド定義
  - [ ] `step`（格子間隔）を 1 つの float で持つ（推奨、最小）
  - [ ] `step=(sx,sy,sz)` の vec3 で軸別に持つ（将来拡張）
- [ ] グリッド原点（アンカー）
  - [ ] `origin=(0,0,0)` 固定で良い / `origin` パラメータを持つ
- [ ] 丸め規則（境界 0.5 の扱いが変わる）
  - [ ] `nearest_even`（`np.rint` 相当 / 銀行丸め）
  - [ ] `half_away_from_zero`（±0.5 を絶対値方向へ）
  - [ ] `floor` / `ceil`（常に下/上へ）
- [ ] `step<=0` の扱い
  - [ ] A: no-op（入力をそのまま返す）
  - [ ] B: `ValueError`（パラメータ誤りとして落とす）

## 1) 受け入れ条件（完了の定義）

- [ ] `E.grid_quantize(...)` が未登録エラーにならず、realize まで到達する
- [ ] offsets が入力と一致する（「点数・ポリライン境界を保つ」）
- [ ] 量子化が期待どおり（小さな固定入力で座標が一致する）
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_grid_quantize.py`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`
- [ ] スタブ再生成（`python -m tools.gen_g_stubs`）が通る

## 2) 仕様案（API/パラメータ）

### effect シグネチャ（案: 最小）

- `grid_quantize(inputs, *, step=1.0, origin=(0,0,0), axes="xy", mode="nearest_even") -> RealizedGeometry`

### ParamMeta（案）

- `step`: `kind="float"`, `ui_min=0.0`, `ui_max=100.0`
- `origin`: `kind="vec3"`, `ui_min=-100.0`, `ui_max=100.0`
- `axes`: `kind="choice"`, `choices=("xy","xyz")`
- `mode`: `kind="choice"`, `choices=("nearest_even","half_away_from_zero","floor","ceil")`

## 3) 実装設計（アルゴリズム）

- [ ] 入力 0 件は空ジオメトリを返す（他 effect と同様）
- [ ] `base=inputs[0]`、空 coords は no-op
- [ ] `step_f=float(step)` を確定し、`step<=0` の扱いを仕様どおりにする
- [ ] `origin=(ox,oy,oz)` を float 化
- [ ] `axes` に応じて対象成分（x,y,(z)）のみを量子化
- [ ] 量子化式（例: nearest 系）:
  - [ ] `q = (coord - origin) / step`
  - [ ] `q_rounded = ...`（mode に応じた丸め）
  - [ ] `coord_out = q_rounded * step + origin`
- [ ] 出力は `RealizedGeometry(coords=coords_out, offsets=base.offsets)`（offsets は同一参照で良い）

## 4) 変更箇所（ファイル単位）

- [ ] `src/grafix/core/effects/grid_quantize.py` を追加
  - [ ] `@effect(meta=...)` で登録（`bypass` は meta に書かない）
  - [ ] モジュール docstring は effects 仕様に従い「効果の説明」だけを書く（どこで/なにを/なぜ 形式は使わない）
- [ ] `src/grafix/api/effects.py` に import を追加（レジストリ登録目的）
- [ ] テスト追加: `tests/core/effects/test_grid_quantize.py`
- [ ] スタブ再生成: `python -m tools.gen_g_stubs`（差分レビューしてコミットは別）

## 5) テスト観点（最小）

- [ ] `step=1.0, origin=(0,0,0), axes="xy"` で期待どおりにスナップする
- [ ] 負値を含む入力で丸め規則が期待どおり（特に `-0.5` 近傍）
- [ ] `axes="xy"` のとき z が不変、`axes="xyz"` のとき z も量子化される
- [ ] `step<=0` が仕様どおり（no-op または例外）
- [ ] 空ジオメトリは no-op（coords=0, offsets=[0]）

## 6) 実行コマンド（ローカル確認）

- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_grid_quantize.py`
- [ ] `ruff check .`
- [ ] `mypy src/grafix`
- [ ] `python -m tools.gen_g_stubs`（スタブ差分確認）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] 将来拡張: `step=(sx,sy,sz)`（軸別）や `auto_origin`（bbox/平均に追従）
- [ ] 将来拡張: 量子化後に「連続重複点を除去する」オプション（別 effect として分離でも良い）
