# partition の階層分割（重ねがけ）対応プラン

## 目的

- `E.partition().partition()` のような重ねがけで、前段の各セル（領域）を後段でさらに細分化できるようにする。
- 既存の `partition` の「全リングを 1 つの領域に畳み込んでから分割する」挙動は維持し、必要時だけ新挙動を選べるようにする。

## 背景（現状の問題）

- 現行 `partition` は入力の閉ループ群を 1 つの `region`（Shapely geometry）へ畳み込み、その `region` を Voronoi 分割する。
- そのため `partition` を 2 回かけると、2 回目は「1 回目のセル境界」を領域として保持せず（再び `region` に畳み込まれて消える）、結果として前段の分割が“上書き”に見える。

## 方針（小さく実現する）

- `partition` に「領域の作り方」を選べるパラメータを 1 つ追加し、既存コードの大半（サイト生成〜Voronoi〜交差〜外周抽出）を再利用する。
- `RealizedGeometry` にメタ情報やグルーピング構造を追加しない（入力の `coords + offsets` からグループを再構成する）。
  - `fill` が同様に、平面が取れる場合は `coords + offsets` から even-odd で外周+穴のグルーピングを構築している。

## 提案 API（案）

- `E.partition(mode="merge"|"group"|"ring", ...)`
  - `mode="merge"`: 現行の挙動（全リングを 1 つの領域へ畳み込んでから分割）。**デフォルト**
  - `mode="group"`: even-odd で「外周 + 穴」をグループ化し、**グループごと**に Voronoi 分割
  - `mode="ring"`: 各リングを独立領域として扱い、**リングごと**に Voronoi 分割（穴構造は無視）

### mode != "merge" のときの site_count の解釈（要合意）

- もっとも単純: `site_count` を「グループ/リングあたり」とする（=総セル数が増える）。
- 代替（やや複雑）: 総サイト数を面積比で配分する（上限/下限/丸めが必要）。

## 実装概要

1. 既存と同様に、入力を平面推定して 2D へ射影し、リング（閉ループ候補）を抽出する。
2. リングごとに Shapely `Polygon` を作り、必要なら `buffer(0)` で簡易修復する（現行踏襲）。
3. `mode` に応じて「分割対象 region の列」を作る。
   - `merge`: 現行の `region` 合成ロジックをそのまま使用して `regions=[region]`
   - `group`: even-odd グルーピングで `regions=[group_region1, group_region2, ...]`
   - `ring`: `regions=[poly1, poly2, ...]`（穴は作らない）
4. `regions` を順に処理し、各 `region` を Voronoi 分割 → `region` と交差 → Polygon 外周（holes 無視）抽出 → 3D に戻す。
5. すべての出力ループをまとめて `RealizedGeometry` として返す。

## even-odd グルーピング方法（案）

- 既存 `fill` の `_build_evenodd_groups` と同等の処理が必要（外周+穴の対応付け）。
  - 実装場所は `partition.py` 内に最小コピーするか、共有ユーティリティへ移すかを決める。
  - “小さく”なら `partition.py` 内へ最小実装（Numba 依存は増やさない）。

## 乱数（seed）の扱い（要合意）

- もっとも単純: 1 つの RNG を `regions` の処理順に使い回す（現行と同じ seed でも、region 数で結果が変わる）。
- 代替: `seed` から region index を混ぜた派生 seed を作り、region ごとに RNG を独立化する（結果が直感的で比較しやすい）。

## 変更対象（予定）

- `src/grafix/core/effects/partition.py`（mode 追加、region 列の処理）
- `src/grafix/api/__init__.pyi`（スタブ再生成）
- `tests/core/effects/test_partition.py`（mode の単体テスト追加）

## テスト方針（案）

- `pytest.importorskip("shapely")` の既存流儀に合わせる。
- 追加テスト例:
  - `mode="merge"`: `E.partition(...).partition(... )` が（seed/入力固定なら）実質変化しない/変化が小さいことのスモーク
  - `mode="group"`: `E.partition(mode="group").partition(mode="group")` でループ数が増える等、階層化が起きることのスモーク

## 実装チェックリスト（実装開始前の合意ポイント込み）

- [ ] `mode` の名称と choices を確定（`mode`/`scope`/`grouping` など）
- [ ] `site_count` の意味を確定（per-region か、配分するか）
- [ ] `seed` の扱いを確定（共有 RNG か、派生 seed で独立化するか）
- [ ] `partition.py` に mode を追加し、`regions` ループへリファクタ（既存ロジックを最大限再利用）
- [ ] `group` 用の even-odd グルーピングを実装（最小・読みやすさ優先）
- [ ] スタブ再生成（`tools/gen_g_stubs.py` 相当の手順）
- [ ] `tests/core/effects/test_partition.py` に mode テストを追加
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_partition.py` を実行して確認

## 懸念点（デメリットの整理）

- `mode="group"/"ring"` は **region 数 × Voronoi** になり、入力（特にテキスト）次第で重くなる。
- `mode="ring"` は穴構造を壊すので、期待通りの領域にならないケースがある（文字の穴など）。
- `mode="group"` でも `site_count` を per-group にすると総セル数が増える（意図した挙動だがコスト増）。

