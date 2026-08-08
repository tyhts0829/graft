# `G.delaunay` 閉領域Primitive実装計画（2026-08-09）

- 状態: **実装完了（対象検証成功、full suite未実行）**
- 計画作成時branch: `main`
- 計画作成時HEAD: `05cb4bb`
- 採用元:
  `sketch/agent_loop/runs/run_20260808_225435_n3/final/sketch.py` の
  Delaunay試作

本計画は、仮想点の散布からDelaunay三角形分割を生成する機能だけを、
組み込みPrimitive `G.delaunay` として正式採用するためのものとする。
各三角形は `E.fill` が独立領域として扱える閉ポリラインで返す。

production code、test、生成stub、showcase、benchmarkは、2026-08-09の明示承認に基づいて変更する。

## 1. 現状と採用判断

採用元の作品では、仮想点から空円条件を総当たりで調べ、Delaunayの共有辺を
2点polylineとして描いている。この実装は小さな作品用の試作としては機能するが、
公式Primitiveには次の課題がある。

- 三角形の辺だけを返すため、各領域が閉じておらず、そのままでは領域単位の
  `E.fill` 合成を保証できない。
- 三点組の総当たりと全点への空円検査により、点数増加時の計算量が大きい。
- 共円点、共線点、退化三角形、face順序の決定性を公式API契約として扱っていない。
- builtin catalog、Parameter GUI、stub、showcase、benchmark、resource budgetへ
  接続されていない。

今回正式採用するのは `delaunay` だけとし、他の試作案は追加しない。

## 2. ゴール

- zero-argumentで利用できる組み込みPrimitive `G.delaunay` を追加する。
- 指定矩形内へ再現可能な仮想siteを散布し、そのDelaunay三角形分割を生成する。
- 各三角形を独立した4頂点の閉ポリライン `[a, b, c, a]` として返す。
- 全faceの向き、開始頂点、face順を正規化し、同じ引数からbyte単位で同じ
  packed Geometryを返す。
- `E.fill(remove_boundary=True)(G.delaunay(...))` で各三角形領域へ
  ハッチングできることを統合testで保証する。
- Parameter GUI、catalog、生成stub、primitive showcase、benchmarkを同期する。
- 過大なsite数・sampling work・出力を、大規模配列確保やGEOS処理の前に拒否する。

## 3. 非ゴール

- `G.voronoi`、`point_weave`、`void_rivers`、`magnetic_calligraphy`、
  `onion_spiral`、`collision_seams`、`gravity_tree`のbuiltin化
- 入力Geometryや任意のpoint列を受け取るDelaunay Effect
- ユーザー指定site座標を受け取る公開parameter
- 共有辺を一度だけ返すwireframe / unique-edge mode
- 矩形四隅や境界siteを自動追加し、出力を矩形全体へ強制的に広げる機能
- constrained Delaunay、穴、境界polygon、weighted Delaunay、3D tetrahedralization
- `E.fill`自体の挙動変更
- 採用元art-loop run artifactの書き換え
- Shapely以外の新しい依存追加

## 4. 公開API案

初版のsignatureを次とする。

```python
G.delaunay(
    width=80.0,
    height=100.0,
    site_count=48,
    seed=0,
    candidates=8,
    center=(0.0, 0.0, 0.0),
)
```

### 4.1 Parameterの意味

- `width`, `height`
  - 仮想siteを散布する矩形の物理サイズ。
  - 正の有限値を要求する。
- `site_count`
  - 散布する仮想site数。3以上を要求する。
  - 個数を増やしたとき、同じ`seed`と`candidates`で既存site列のprefixを維持する。
- `seed`
  - site配置を再現する非負整数。
  - localなNumPy乱数generatorだけへ渡し、global RNG状態を変更しない。
- `candidates`
  - 二つ目以降のsiteごとに比較する候補点数。1以上を要求する。
  - `1`では一様乱数散布、値を増やすほどsite間隔が均一な
    Mitchell風best-candidate配置になる。
- `center`
  - 散布矩形の中心 `(x, y, z)`。
  - 全faceは `z=center[2]` の同一平面上へ生成する。

### 4.2 Parameter GUI

全公開parameterへ日本語description付き`ParamMeta`を定義する。

- Layout: `width`, `height`, `center`
- Sites: `site_count`, `seed`, `candidates`

GUI初期範囲案は次とする。GUI範囲はコード入力を制限しないため、raw evaluatorでも
別途validationを行う。

- `width`, `height`: 1.0〜300.0
- `site_count`: 3〜500
- `seed`: 0〜1,000,000
- `candidates`: 1〜32

`activate`は`@primitive`が自動追加する通常契約へ任せる。

## 5. Geometry出力契約

Delaunay三角形数を `T` としたとき、次のpacked Geometryを返す。

- `coords`: C-contiguousかつwritableな `float32 (4T, 3)`
- `offsets`: C-contiguousかつwritableな
  `int32 [0, 4, 8, ..., 4T]`
- 各offset区間は一つの三角形だけを表す。
- 各faceは三つの相異なる頂点を持ち、非ゼロ面積である。
- 各faceはlocal XYでCCWに統一する。
- 各faceは辞書順で最小の頂点から開始する。
- 4点目は1点目を計算し直さず、配列上で厳密にコピーする。
- `coords[start] == coords[stop - 1]`をbit単位で保証する。
- 重複face、退化face、非有限faceを含めない。
- 全faceをcanonical vertex keyでsortし、GEOSのcollection順へ依存しない。

有効faceがない場合は標準空Geometry
`coords.shape == (0, 3)`, `offsets == [0]`を返す。

## 6. `E.fill`との合成契約

各三角形を独立した閉ringにするため、平面上で隣接する三角形は共有辺または共有頂点で
接するだけの別outer regionとして`E.fill`へ渡る。穴判定はeven-oddで行われるため、
隣接faceをholeとして扱わない。

### 6.1 共有辺の扱い

内部共有辺は、隣接する二つの閉ringへ一度ずつ含まれる。このため
`G.delaunay()`単体、または`E.fill(remove_boundary=False)`では、plotterが内部共有辺を
二重に描く可能性がある。

これは「各三角形を独立したfill可能領域にする」という今回の要件に伴う意図的な契約とする。
Primitive側で共有辺をdeduplicateするとface closureが壊れるため行わない。
境界線を描かずハッチだけが必要な場合は、利用側で次のように指定する。

```python
filled = E.fill(
    angle=35.0,
    density=24.0,
    remove_boundary=True,
)(G.delaunay())
```

## 7. Site散布

### 7.1 座標系

- siteはまず`center`を含まないlocal XY矩形
  `[-width/2, width/2] x [-height/2, height/2]`へ`float64`で生成する。
- best-candidateの距離とDelaunay判定は、異方scale後の実際の物理XY距離で行う。
- triangulation完了後に`center.xy`を加え、`center.z`を設定して`float32`へ変換する。
- 大きな`center`値をGEOS計算へ混ぜず、平行移動でtriangulation topologyが
  変わらないようにする。

### 7.2 Best-candidate法

- `np.random.default_rng(seed)`から決定的な候補列を生成する。
- 最初のsiteは矩形内の一様乱数点とする。
- 以後は毎回`candidates`個の候補を生成し、既存siteへの最短二乗距離が
  最大の候補を採用する。
- 最大値が同じ場合は候補生成順で先の点を採用する。
- `candidates == 1`は距離行列を作らず、一様乱数点をまとめて生成するfast pathとする。
- `site_count`増加時に既存siteの生成順を変えない。
- 近接点をepsilonでmergeしない。Shapely投入前には完全一致するduplicateだけを除く。

矩形四隅は自動追加しない。したがって、三角形群が覆うのは散布矩形全体ではなく
仮想siteの凸包である。この不規則な外周を初版の視覚契約とする。

## 8. Delaunay triangulation

既存必須依存のShapely 2を使用する。

```python
shapely.delaunay_triangles(
    MultiPoint(sites),
    tolerance=0.0,
    only_edges=False,
)
```

### 8.1 Shapelyを採用する理由

- `pyproject.toml`ですでに`shapely>=2,<3`を必須依存としているため、依存追加がない。
- 既存`E.partition`もGEOSによる平面幾何処理を利用している。
- 自前Bowyer-Watsonではrobustなorientation / incircle predicate、duplicate、共線、
  共円点のtie処理が新たな保守対象になる。
- 単純さと堅牢さのバランスが、リポジトリ方針に合う。

### 8.2 Canonicalization

1. local siteを辞書順でsortし、完全一致duplicateを除く。
2. `MultiPoint`からPolygon形式のDelaunay triangleを得る。
3. Polygon外周から三頂点を取り出し、相異なる有限頂点であることを確認する。
4. scale-awareな面積閾値以下の退化faceを除く。
5. windingをCCWへ揃える。
6. 辞書順最小頂点が先頭になるよう、三頂点をcyclic rotationする。
7. canonical triangle keyで重複faceを除き、全faceをsortする。
8. `center`とZを反映して`float32`化し、先頭頂点を末尾へ厳密コピーする。
9. 実face数でresource budgetを再確認してからpackする。

GEOSの返却順は上記正規化で吸収する。厳密な共円点ではDelaunay対角線自体が
一意でないが、連続乱数siteを用い、共円になりやすい人工cornerを追加しないことで
通常生成では回避する。GEOSが有効入力を処理できない場合は、説明付き`ValueError`へ変換し、
別の図形へ黙って置換しない。

## 9. Validationとresource budget

点生成・大規模配列確保・GEOS呼び出しの前に次を検査する。

- `width`, `height`: 正の有限値
- `site_count`: boolではないexact int、3以上、hard cap以下
- `seed`: boolではないexact int、0以上
- `candidates`: boolではないexact int、1以上
- `center`: 有限な3成分tuple
- local bounds、center反映後bounds、Zが有限なcanonical `float32`へ変換できる
- width / heightがfloat32化で潰れず、正面積faceを表現できる

初版では次のoperation固有限界を置く。

- `_MAX_SITE_COUNT = 10_000`
- `_MAX_CANDIDATE_DISTANCE_EVALUATIONS = 64_000_000`

`candidates > 1`では仕事量を
`candidates * site_count * (site_count - 1) // 2`で事前計算し、上限超過時は
`ResourceLimitError`を送出する。

平面Delaunayの最大三角形数は、一般位置の `n >= 3` に対して `2n - 5` 以下である。
この上限から、triangulation前に次を行う。

```python
max_faces = 2 * site_count - 5
ensure_geometry_output(
    "delaunay",
    vertices=4 * max_faces,
    lines=max_faces,
    scratch_bytes=estimated_sampling_scratch_bytes,
)
```

実face数確定後も、pack前に`ensure_geometry_output`を再実行する。
上限超過時にsite数や`candidates`を黙って減らさない。

## 10. 追加・変更するファイル

### 10.1 Production

- [x] `src/grafix/core/primitives/delaunay.py`（新規）
  - `delaunay_meta`を定義する。
  - site散布、Shapely triangulation、face canonicalization、validation、
    resource preflightを実装する。
  - `@primitive(meta=delaunay_meta)`とNumPyスタイル日本語docstring、型hintを付ける。
  - `__all__ = ["delaunay", "delaunay_meta"]`を定義する。
- [x] `src/grafix/core/builtins.py`
  - `_PRIMITIVE_NAMES`末尾へ`"delaunay"`を追加する。
  - 既存21 Primitiveのlocator順を変えない。
  - manifest総数を58から59へ増やす。
- [x] `src/grafix/api/__init__.pyi`
  - 手編集せず、fresh processの正規stub generatorから再生成する。

次のファイルは変更不要である。

- `src/grafix/core/primitives/__init__.py`
- `src/grafix/api/primitives.py`
- `src/grafix/__init__.py`

builtin manifestへの登録後、`G.delaunay`、`G.catalog()`、
`G.describe("delaunay")`、CLI `list` / `describe`へ自動反映される。

### 10.2 Tests

- [x] `tests/core/primitives/test_delaunay.py`（新規）
  - Primitive本体、canonical face、validation、resource、Effect合成を検証する。
- [x] `tests/core/test_builtin_catalog_bootstrap.py`
  - manifest件数を58から59へ更新する。
- [x] `tests/devtools/benchmarks/test_primitive_benchmark.py`
  - builtin primitive集合へ`delaunay`を追加する。
  - `primitives` suite件数を26から27へ更新する。
  - Delaunay固有work metricsと全line closed契約を検証する。

動的契約のため通常は変更せず、実行して同期を確認するtest:

- `tests/core/test_lazy_builtins.py`
- `tests/core/parameters/test_description_completeness.py`
- `tests/api/test_operation_catalog.py`
- `tests/sketch/test_primitive_showcase.py`
- `tests/stubs/test_api_stub_sync.py`
- `tests/core/effects/test_fill.py`

### 10.3 Showcaseとbenchmark

- [x] `sketch/showcase/primitives.py`
  - `PRIMITIVE_NAMES`末尾へ`delaunay`を追加する。
  - 22番目の軽量sampleを追加し、return順をmanifestと一致させる。
  - 現行4列×6行、canvas高さ530のまま掲載する。
  - `E.fill`との合成が視認できるsampleにするか、閉三角形群をそのまま示すかを
    headless PNG比較で決定する。
- [x] `src/grafix/devtools/benchmarks/primitive_benchmark.py`
  - `site_count=500`, `candidates=8`程度のactual-work caseを1件追加する。
  - `run_seed_argument="seed"`でbenchmark seedを注入する。
  - `work.site_count`, `work.candidates`, `work.candidate_checks`,
    `work.triangle_count`を記録する。
  - common `closed_lines`が`n_lines`と一致するhard contractをtest側で確認する。

### 10.4 Docs

- [x] 本計画書のcheckbox、実行command、検証結果、未完了事項を実装に合わせて更新する。
- READMEはbuiltin Primitiveの全件を列挙していないため、今回は変更しない。

## 11. Test計画

### 11.1 Primitive単体とface topology

- [x] zero-argument呼び出しが非空Geometryを返す。
- [x] `coords`がC-contiguous / writable / `float32 (N, 3)` / finiteである。
- [x] `offsets`がC-contiguous / writable / `int32 (T+1,)`でpacked契約を満たす。
- [x] `np.diff(offsets)`が全要素4である。
- [x] 全faceで先頭と末尾がexact一致する。
- [x] 各faceの三頂点が相異なり、local XY面積が正でCCWである。
- [x] face keyに重複がない。
- [x] 共有辺のmultiplicityが1または2で、少なくとも一つの内部共有辺が2回現れる。
- [x] 全XY座標が指定矩形bounds内、全Zが`center[2]`と一致する。
- [x] `site_count=3`で一つの閉三角形を返す。
- [x] 同じ引数とseedからcoords / offsetsがbyte単位で一致する。
- [x] 連続呼び出しがfreshかつ非共有のwritable配列を返す。
- [x] `seed`, `site_count`, `candidates`, `width`, `height`の変更が出力へ反映される。
- [x] `center`変更が平行移動とZだけへ反映される。

### 11.2 Triangulation helper

- [x] 小さな非共円の固定point集合から期待するcanonical triangle集合を返す。
- [x] input point順を変えてもface順・頂点順を含めて同じ出力になる。
- [x] exact duplicate pointを含んでも重複faceを生成しない。
- [x] 共線pointだけでは標準空Geometry相当のface列になる。
- [x] 共円4点では重ならない二三角形だけを返し、特定の対角線を公開契約にしない。
- [x] sliver、非有限値、float32化で潰れるfaceを出力しない。

### 11.3 `E.fill`とEffect合成

- [x] `E.fill(remove_boundary=True)(G.delaunay(...))`が非空ハッチを返す。
- [x] boundary除去時のfill出力が2頂点線分だけで構成される。
- [x] hatch線分の中点が元三角形群のunion外へ出ない。
- [x] 共有辺で接する三角形がhole扱いされず、隣接両側へハッチが生成される。
- [x] `remove_boundary=False`では元の閉face数が保持される。
- [x] `E.translate`、`E.rotate`と合成してstandard Geometryを返す。
- [x] `activate=False`でevaluatorを実行せず標準空Geometryを返す。

### 11.4 Validationとresource

- [x] 0以下・非有限の`width` / `height`を拒否する。
- [x] 3未満またはbool / 非intの`site_count`を拒否する。
- [x] 負値またはbool / 非intの`seed`を拒否する。
- [x] 1未満またはbool / 非intの`candidates`を拒否する。
- [x] 非有限`center`とfloat32非表現boundsを拒否する。
- [x] site hard capとcandidate work上限をGEOS呼び出し前に拒否する。
- [x] active `ResourceBudget`を尊重し、点生成前に`ResourceLimitError`を送出する。
- [x] 不正入力をsite数の縮小や別形状への置換で黙って処理しない。

### 11.5 Catalog、metadata、stub、showcase、benchmark

- [x] builtin manifestが22 Primitive / 37 Effect / 合計59件になる。
- [x] builtin locator、lazy bootstrap、import-order契約が維持される。
- [x] `G.describe("delaunay")`が`n_inputs=0`、default、meta、helpを正しく返す。
- [x] 全parameterにdescriptionがあり、metadata completeness testを通る。
- [x] checked-in stubとgenerator出力が一致する。
- [x] showcaseの22件がmanifestと一致し、全sampleが非空・有限・決定的である。
- [x] benchmarkの27 caseが全22 builtin Primitiveを覆う。
- [x] Delaunay benchmarkで`closed_lines == n_lines == triangle_count`となる。

## 12. 視覚・plotter確認

- [x] primitive showcaseをheadless PNGへ出力する。
- [x] 仮想site自体を描かず、Delaunay三角形群だけが見えることを確認する。
- [x] face closure、外周の不規則さ、線密度、sample clippingを目視確認する。
- [x] 同じDelaunay geometryへ`E.fill(remove_boundary=True/False)`を適用した比較PNGを作る。
- [x] fillが各三角形へ入り、隣接faceをholeとして抜かないことを確認する。
- [x] `remove_boundary=False`で共有辺が重ね描きされる仕様をGeometry統計で確認する。
- [x] `remove_boundary=True`のG-codeを一度出力し、閉境界を再描画せずハッチだけをplotできることを確認する。
- [x] 確定確認PNGを衝突しない名前で`data/output/png/codex_generated/`へ保存する。

## 13. 実装フェーズ

### Phase 0 — 実装直前確認

- [x] `git status --porcelain`を再確認し、並行差分へ触れない。
- [x] 本計画の公開API、共有辺重複、Shapely採用が承認内容と一致することを確認する。
- [x] 現行builtin / showcase / benchmark件数をbaselineとして記録する。

### Phase 1 — 失敗testと低水準helper

- [x] 閉face、canonical順、固定point集合、退化入力の失敗testを先に追加する。
- [x] best-candidate site生成を実装する。
- [x] Shapely triangulationとface canonicalizationを実装する。
- [x] validationとresource preflightを実装する。

### Phase 2 — Primitive本体とfill合成

- [x] `@primitive(meta=delaunay_meta)` evaluatorを完成させる。
- [x] `[a, b, c, a]` faceを標準packed Geometryへpackする。
- [x] `E.fill`、transform、`activate=False`の統合testを通す。
- [x] 公開docstringとParamMetaを完成させる。

### Phase 3 — 公式登録と同期面

- [x] builtin manifest末尾へ登録する。
- [x] primitive showcaseへ22番目のsampleを追加する。
- [x] actual-work benchmark caseとmetricsを追加する。
- [x] fresh processでchecked-in API stubを再生成する。
- [x] catalog、metadata、stub、showcase、benchmark testを通す。

### Phase 4 — 視覚・plotter検証

- [x] showcaseとfill比較PNGをrenderして画像確認する。
- [x] geometry統計で全face closureと共有辺重複を確認する。
- [x] `remove_boundary=True`のG-codeを確認する。
- [x] 確定PNGを`data/output/png/codex_generated/`へ保存する。

### Phase 5 — 最終検証と計画更新

- [x] 対象pytestを通す。
- [x] 変更対象へRuffを実行する。
- [x] `mypy src/grafix`を実行する。
- [x] `git diff --check`を通す。
- [ ] full test suiteは長時間実行の承認を得た場合のみ実行する。
- [x] 本計画の完了項目、検証command、結果、未完了事項を更新する。

## 14. 主な検証command

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/core/primitives/test_delaunay.py \
  tests/core/effects/test_fill.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/core/test_lazy_builtins.py \
  tests/core/parameters/test_description_completeness.py \
  tests/api/test_operation_catalog.py \
  tests/sketch/test_primitive_showcase.py \
  tests/devtools/benchmarks/test_primitive_benchmark.py \
  tests/stubs/test_api_stub_sync.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m grafix stub \
  --no-default-import \
  --config src/grafix/resource/default_config.yaml \
  --output src/grafix/api/__init__.pyi

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m grafix describe primitive delaunay

ruff check \
  src/grafix/core/primitives/delaunay.py \
  src/grafix/core/builtins.py \
  src/grafix/devtools/benchmarks/primitive_benchmark.py \
  tests/core/primitives/test_delaunay.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/devtools/benchmarks/test_primitive_benchmark.py \
  sketch/showcase/primitives.py

mypy src/grafix
git diff --check
```

## 15. 完了条件

- `G.delaunay()`がzero-argumentで利用でき、GUIからsite配置を操作できる。
- 出力する全polylineが4頂点の非退化Delaunay三角形で、先頭末尾がexact一致する。
- 全faceがCCW、canonical seam、canonical face順を持ち、同じ引数からbyte-stableな
  Geometryが得られる。
- `E.fill(remove_boundary=True)`が全三角形を独立領域としてハッチングできる。
- 共有辺の二重境界が、fill可能な独立face出力に伴う意図的な仕様としてdocstringとtestへ残る。
- 出力は仮想siteの凸包を覆い、人工cornerや矩形全域fillを暗黙に追加しない。
- 無効入力、過大sampling work、active resource budgetを処理前に拒否する。
- builtin manifest、stub、showcase、benchmark、metadataが新Primitiveと同期する。
- 対象test、lint、type check、render、G-code確認が成功し、結果が本計画へ記録される。
- Voronoiを含む他の試作案を今回の変更へ混ぜない。

## 16. 実装結果（2026-08-09）

### 16.1 実装内容

- `G.delaunay`をbuiltin Primitiveとして登録した。
- best-candidate方式で仮想siteを散布し、出力と同じfloat32精度へ量子化後、
  Shapely / GEOSで三角形分割する。
- 各faceをCCW、辞書順最小頂点始点、face key順へ正規化し、
  `[a, b, c, a]`の独立した閉polylineとしてpackする。
- 共有辺は隣接faceごとに意図的に重複させ、
  `E.fill(remove_boundary=True)`では境界を除いたハッチだけを得られる。
- validation、site / candidate work上限、active `ResourceBudget`の事前検査を追加した。
- 大きな`center`でfloat32丸めがDelaunay topologyを変えるケースを回帰test化し、
  量子化後siteの再triangulationでface重なりを防いだ。
- float32量子化用配列もscratch resource見積りへ含めた。
- builtin catalog、生成stub、primitive showcase、benchmarkを同期した。

### 16.2 Geometry・描画確認

既定値で次のGeometry統計を確認した。

- 83 face、332 vertices
- 全83 faceが4頂点かつexact closure
- edge multiplicityは最大2
- 外周edge 11本、共有edge 119本

fill-only確認結果は265本（530 vertices）の2頂点lineで、閉boundaryを含まないことを確認した。
比較PNG上でも全三角形へハッチが入り、隣接faceがhole扱いされないことを確認した。

- primitive showcase:
  `data/output/png/codex_generated/delaunay_primitive_showcase_20260809.png`
- fill比較:
  `data/output/png/codex_generated/delaunay_fill_comparison_20260809.png`
- fill-only G-code:
  `/tmp/delaunay_fill_only_20260809.gcode`（pen-down 265回、export成功）

### 16.3 検証結果

- Delaunay単体test: `53 passed`
- 対象統合pytest: `136 passed in 16.22s`
- Ruff（変更対象）: 成功
- mypy: `Success: no issues found in 293 source files`
- `git diff --check`: 成功
- 異なる`PYTHONHASHSEED`のfresh processで同一SHA-256
  `28396c1c1154853c37a28a931e668c441311f75b3cbcd4bb3f055fe546657376`
- CLI `grafix describe primitive delaunay`: 成功

### 16.4 未完了事項

- full test suiteは長時間実行に対する追加承認を得ていないため未実行。
  Delaunay、fill、catalog、lazy builtin、metadata、operation catalog、showcase、
  benchmark、stub同期を含む対象suiteで代替検証した。
- 並行作業由来の未追跡ファイル
  `docs/plan/gcode_layer_travel_optimization_implementation_plan_2026-08-09.md`
  には触れていない。
