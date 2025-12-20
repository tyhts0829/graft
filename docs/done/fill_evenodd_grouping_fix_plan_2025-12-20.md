---
title: fill/partition の「一部だけ塗られない」修正計画
date: 2025-12-20
status: draft
---

# 背景

`sketch/main.py` の `affine().partition().fill()` で、partition 後の区画の一部だけハッチ（fill）が生成されないことがある。
境界線は出るが、塗り線が出ないセルが混ざる。

現状の観測（再現）:

- `fill` の even-odd グルーピングが、隣接セル（共有辺/共有頂点）の「境界上の代表点」を内包扱いしてしまい、一部セルが hole 扱いになる。
- 旧実装 `src/grafix/core/primitives/from_previous_project/done/polygon_grouping.py` の `point_in_polygon_njit` は on-edge を inside としない挙動で、同様の誤グルーピングが起きにくい。

# 目的

partition 後のセルが隣接していても、`fill` がセル単位に安定して適用されるようにする。
（穴のある形状の穴抜き挙動は維持する）

# 方針（案）

`fill` 側の点包含判定を、**境界上(on-edge)は常に外側扱い（False）**になるように修正する。
これにより、partition セル間の共有辺/共有頂点に乗る代表点が「内包」と誤判定されず、セルが hole 扱いにならない。

あわせて、even-odd グルーピング実装を「旧 `polygon_grouping.build_evenodd_groups` と同等の戦略」へ寄せ、
“どのリングもグループから脱落しない”ようにする（穴に誤判定されても単独グループに落とす）。

# 対象ファイル

- `src/grafix/core/effects/fill.py`
  - `_point_in_polygon`
  - `_build_evenodd_groups`
- `tests/core/effects/test_fill.py`
  - 回帰テスト追加（隣接セルの境界接触ケース）

# 実装チェックリスト

- [x] 最小再現ケースをテストとして追加する（境界接触で hole 誤判定されない）
  - 例: 外側にあるが入力代表点が他ポリゴン境界上にある 2 ループを作り、`_build_evenodd_groups` の結果が「2 つの outer」となることを確認
- [x] `src/grafix/core/effects/fill.py` の `_point_in_polygon` を「境界上は常に False」に修正する
  - 辺上/頂点上を明示的に検出して除外し、その後に偶奇レイキャストで内部判定する
- [x] `src/grafix/core/effects/fill.py` の `_build_evenodd_groups` を “リング脱落しない” 実装へ差し替える
  - 旧 `polygon_grouping.build_evenodd_groups` と同等の戦略（hole が outer を見つけられない場合は単独グループ）
- [x] 既存の穴抜きテスト（`fill_test_square_with_hole`）が壊れないことを確認する
- [ ] 追加の回帰テストを 1 本足す（必要なら）
  - `on-edge` の点が inside にならないこと（辺上/頂点上）を直接テストするか、グルーピングの帰結で担保する
- [x] `partition().fill()` の簡易統合チェックを行う（任意）
  - 例: 単純な閉ループ（正方形など）→ `partition(site_count=...)` → `fill(remove_boundary=True)` で、塗り線が 0 本にならないことを確認
- [x] `pytest` を対象限定で流して確認する
  - `PYTHONPATH=src pytest -q tests/core/effects/test_fill.py`
- [ ] `ruff` を対象限定で流して確認する
  - この環境では `ruff` が見つからなかった（未インストール）。
- [ ] `mypy` を対象限定で流して確認する
  - この環境では既存の型エラーが複数あり、今回の変更と独立に失敗する。

# テスト/検証コマンド（予定）

- `PYTHONPATH=src pytest -q tests/core/effects/test_fill.py`
- `ruff check src/grafix/core/effects/fill.py tests/core/effects/test_fill.py`
- `mypy src/grafix/core/effects/fill.py`

# リスク/注意点

- point-in-polygon の on-edge 取り扱いが変わるため、境界上ギリギリの入力でグルーピング結果が変わり得る。
  - ただし今回の目的（隣接セルの誤 hole 化回避）には整合。
- 数値誤差で「ほぼ境界上」の点が内外どちらになるかは入力スケールに依存する。

# 事前確認したい点（あなたの OK が必要）

- [x] even-odd グルーピングにおいて **on-edge を外側扱い（False）**に揃える方針で良いか？；はい
- [x] `fill` は Shapely 非依存のまま（純 Python/Numpy）で修正して良いか？；はい
- [x] 回帰テストは「境界接触で hole にならない」ケースを 1 本追加で良いか？（統合テストは任意扱い）;はい

# 追加提案（必要になったら）

- もし修正後も `partition().fill()` で欠けが残る場合:
  - `_build_evenodd_groups` の代表点を「第 1 頂点」から「確実に内部にある点」へ変更する（ただしドーナツ等で破綻しない選び方が必要）。
  - `partition` 側でセル外周の開始点が共有頂点に偏らないように回転（最左下点から開始など）する。
