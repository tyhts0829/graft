# `G.delaunay` Soft Boundary / Guard Band改善計画（2026-08-09）

- 状態: **承認待ち（production未変更）**
- 計画作成時branch: `main`
- 計画作成時HEAD: `f970243`
- 関連計画:
  `docs/plan/delaunay_primitive_closed_faces_implementation_plan_2026-08-09.md`

本計画は、`G.delaunay`の凸包付近に現れる細長く潰れた三角形を、
厳密な矩形被覆より三角形品質を優先するsoft boundary方式で改善する。
本ファイルの承認を得るまではproduction、test、stub、showcase、benchmarkを変更しない。

計画作成時点ではbenchmark report、CI、`sketch/main.py`などに並行差分がある。
それらは本改善の対象外とし、整理、再生成、巻き戻し、stageを行わない。

## 1. 現状と原因

現行実装は、`width × height`の矩形内だけへ`site_count`個のsiteを散布し、
その全siteのDelaunay三角形を出力する。内部siteは全方向に隣接siteを持つ一方、
凸包上のsiteは外側に隣接siteを持たない。この一方向性により、best-candidate配置が
内部で均一でも、凸包を閉じる三角形には非常に小さい角度が生じ得る。

Delaunay分割は固定site集合に対する角度を改善するが、凸包の形そのものや
境界siteの不足は解決しない。`candidates`を増やすだけでは根治しない。

計画作成時の探索では、`width=80`, `height=100`, `site_count=48`,
`candidates=8`, `seed=110`の現行境界に、正規化品質
`q ≈ 6.5e-6`のほぼ退化したfaceが確認できた。

## 2. 採用方針

### 2.1 Guard bandを標準化する

元矩形の外側へ、nominal site pitchの既定2層分のguard bandを設ける。
拡張矩形へ同じ目標密度でsiteを散布してから一度だけDelaunay分割し、
**三頂点すべてが元矩形内にあるfaceだけ**を出力する。

これにより、元矩形の境界はDelaunay全体の凸包ではなく内部領域になる。
境界付近のsiteも外側siteから近傍制約を受けるため、凸包由来のsliverを除去できる。

### 2.2 領域は内側へ後退してよい

出力faceを矩形へclipしない。三頂点が元矩形内にあるfaceだけを丸ごと残すため、
出力輪郭は矩形より少し内側へ後退し、不規則になる。これは今回の
「領域は厳密でなくてよい」という判断に合わせた意図的な仕様とする。

一方で、全出力頂点が従来の`width / height` bounds内にある契約は維持する。
矩形は凸なので、三頂点がbounds内なら三角形全体もbounds内である。

### 2.3 初版ではquality peelを追加しない

Guard bandだけで根本原因を取り除く。境界faceを反復削除するquality peelや
minimum-angle保証は、穴、過度な侵食、追加parameterを招くため初版の非ゴールとする。

最終float32 faceの品質をtestで測り、既定`candidates=8`の回帰fixtureで
境界最小品質が基準を満たすことを保証する。Guard bandだけで基準を満たさない場合は、
暗黙の複雑化をせず計画へ戻る。

## 3. 公開API変更

signatureへ`guard_band`を追加する。

```python
G.delaunay(
    width=80.0,
    height=100.0,
    site_count=48,
    seed=0,
    candidates=8,
    guard_band=2.0,
    center=(0.0, 0.0, 0.0),
)
```

### 3.1 `guard_band`

- nominal site pitchを単位とする、各辺の外側へ追加するband幅。
- 非負の有限実数を要求する。
- `0.0`ではguardを追加せず、現行の凸包出力になる。
- 既定`2.0`では上下左右へnominal pitchの2倍を追加する。
- GUI案:
  - kind: `float`
  - display name: `Guard Band`
  - range: `0.0 ... 4.0`
  - step: `0.25`
  - category: `Sites`

### 3.2 `site_count`の意味変更

`site_count`は「実際に生成する全site数」ではなく、元矩形内に対する
**目標site数 / 密度**を表す。Guard siteはこの個数へ含めない。

実際の生成site数は拡張面積から決まり、元矩形内の実site数はseedにより多少前後する。
ParamMetaとdocstringをこの意味へ更新する。

`guard_band`がsite pitchへ依存するため、`site_count`変更時に拡張矩形も変わる。
従って公開結果についてsite列のprefix維持は契約にしない。
低水準`_best_candidate_sites`自体の固定矩形に対するprefix決定性は維持する。

### 3.3 低site数

- raw evaluatorは引き続き`site_count >= 3`を受理する。
- Parameter GUIの下限は`8`へ上げる。
- guard後に元矩形内だけで有効faceを構成できなければ、標準空Geometryを返す。
- 三点から必ず一三角形を得たい場合は`guard_band=0.0`を使用する。
- 自動再試行、guardの暗黙無効化、site追加による救済は行わない。

これは特殊caseのために標準アルゴリズムを分岐させず、出力密度と決定性を明瞭にするためである。

### 3.4 Evaluator ABI

公開signatureと既定出力が変わるため、builtin primitive `delaunay`の
`evaluator_abi`を`1`から`2`へ上げる。

## 4. Samplingとface選別

nominal areaを`A = width * height`、目標site数を`n`、guardを`g`とする。

```python
pitch = sqrt(A / n)
margin = g * pitch
expanded_width = width + 2 * margin
expanded_height = height + 2 * margin
expanded_area = expanded_width * expanded_height
expanded_site_count = ceil(n * expanded_area / A)
```

`g == 0.0`では明示的に`expanded_site_count = n`とし、浮動小数点丸めによる
不要な`n + 1`を避ける。

処理順は次のとおり。

1. 公開parameterを検証する。
2. pitch、margin、拡張寸法、実生成site数をfloat64 / Python intで求める。
3. 拡張矩形のfloat32有限boundsとresource上限を検査する。
4. 拡張矩形へ`expanded_site_count`個をbest-candidate散布する。
5. `center`を加え、出力と同じfloat32へ量子化する。
6. 量子化済み全siteをShapely / GEOSで一度だけDelaunay分割する。
7. 元矩形のfloat32 boundsを求め、各faceの三頂点すべてがinclusive bounds内か判定する。
8. bounds外の頂点を一つでも持つfaceを丸ごと破棄する。
9. canonical face順を維持したまま、従来どおり`[a, b, c, a]`へpackする。

clipによる三角形以外のpolygon生成、交点追加、境界への頂点snapは行わない。

## 5. 維持するGeometry契約

Guard band導入後も次を維持する。

- `coords`: C-contiguous / writable / finiteな`float32 (4T, 3)`
- `offsets`: C-contiguous / writableな`int32 [0, 4, ..., 4T]`
- 全faceが三つの相異なる頂点を持つ非退化三角形
- 全faceがCCW、辞書順最小頂点始点、canonical face key順
- 4点目が1点目のbit-exact copy
- 同引数がbyte deterministic
- face重複なし、面積を持つface重なりなし
- 共有辺は隣接faceごとに意図的に二重出力
- `E.fill(remove_boundary=True)`で各faceを独立領域として処理可能
- 全出力頂点が元矩形bounds内、全Zが`center[2]`

出力unionが元矩形全域を覆うこと、矩形辺へ接すること、凸であることは保証しない。

## 6. Triangle quality契約

final float32 triangleの三頂点を`a, b, c`とし、次の正規化品質を使う。

```python
q = 2 * sqrt(3) * abs(cross(b - a, c - a)) / (
    length2(a, b) + length2(b, c) + length2(c, a)
)
```

- `q = 1`: 正三角形
- `q -> 0`: 細長い / 退化に近い三角形

undirected edgeの出現数が1のedgeを持つfaceを出力境界faceとみなす。
既定`candidates=8`の固定fixtureでは次をtest契約とする。

- `guard_band=2.0`の境界最小品質 `q >= 0.30`
- 同じfixtureの`guard_band=0.0`より明確に改善する
- exact face数や共円時の対角線は固定しない

探索baseline:

- `80 × 100`, 48 sites, candidates 8, seed 110:
  - guard 0: boundary min `q ≈ 0.0000065`
  - guard 2: boundary min `q ≈ 0.525`
- `54 × 46`, 18 sites, candidates 8, seed 150:
  - guard 0: boundary min `q ≈ 0.000062`
  - guard 2: boundary min `q ≈ 0.648`
- 既定寸法 / candidates 8の100 seeds探索ではguard 2の全face最小`q > 0.34`

`candidates=1`は一様乱数配置であり、同じ`q >= 0.30`を保証しない。
ただしguard 0より品質が改善するfixtureを別途確認する。

## 7. Validationとresource preflight

実際にGEOSへ渡す`expanded_site_count`を基準に事前検査する。

- `guard_band`: finiteかつ`>= 0`
- 拡張width / height / bounds: finiteかつfloat32で表現可能
- `expanded_site_count <= 10_000`
- candidate work:
  - `candidates == 1`: `0`
  - それ以外:
    `candidates * expanded_site_count * (expanded_site_count - 1) // 2`
  - 上限は現行`64_000_000`
- 最大face数: `2 * expanded_site_count - 5`
- output / scratch resource見積りも`expanded_site_count`基準
- face選別用boolean maskの一時配列もscratch見積りへ含める
- 実face数確定後の`ensure_geometry_output`を維持する

上限超過時にguard、site数、candidate数を黙って縮小しない。
site散布やGEOS呼び出し前に`ResourceLimitError`を送出する。

## 8. 変更対象ファイル

### 8.1 Production

- [ ] `src/grafix/core/primitives/delaunay.py`
  - `guard_band` ParamMeta / validationを追加
  - sampling plan helperを追加
  - expanded site数基準のresource preflightへ更新
  - 元bounds内faceだけを選別
  - docstringと`site_count`説明をsoft boundary仕様へ更新
- [ ] `src/grafix/core/builtins.py`
  - primitive ABI overrideを追加し、`delaunay`だけABI `2`へ更新
- [ ] `src/grafix/api/__init__.pyi`
  - fresh process / `--no-default-import`で正規generatorから再生成

`typings/grafix/api/__init__.pyi`はproject固有operationを含む生成artifactで、
並行編集中の`sketch/main.py`状態を取り込む可能性がある。今回のcanonical builtin変更では
手編集も暗黙再生成も行わず、依頼外差分を混ぜない。

### 8.2 Tests

- [ ] `tests/core/primitives/test_delaunay.py`
  - sampling plan、guard selection、quality、低site、validation、resource、fillを追加・更新
  - 従来の厳密bounds testを維持
  - `site_count=3`の一face契約は`guard_band=0.0`で検証
- [ ] `tests/core/test_builtin_catalog_bootstrap.py`
  - Delaunay primitive ABI `2`と他primitive ABI `1`を検証
- [ ] `tests/devtools/benchmarks/test_primitive_benchmark.py`
  - generated site数と実candidate work metricsを検証
- [ ] `tests/stubs/test_api_stub_sync.py`
  - canonical stub同期と`guard_band`の引数位置を検証

動的契約確認として次を実行するが、原則編集しない。

- `tests/core/test_lazy_builtins.py`
- `tests/core/parameters/test_description_completeness.py`
- `tests/api/test_operation_catalog.py`
- `tests/sketch/test_primitive_showcase.py`
- `tests/core/effects/test_fill.py`

### 8.3 Showcase / benchmark

- [ ] `sketch/showcase/primitives.py`
  - Delaunay sampleで`guard_band=2.0`を明示
  - seed / site_countはquality改善とcell内収まりを画像で確認して決定
- [ ] `src/grafix/devtools/benchmarks/primitive_benchmark.py`
  - Delaunay caseをguard 2のactual-workへ更新
  - `work.site_count`（nominal）
  - `work.guard_band`
  - `work.nominal_pitch`
  - `work.guard_margin`
  - `work.expanded_site_count`
  - expanded count基準の`work.candidate_checks`
  - `work.triangle_count`
  - 必要ならtiming外postprocessで`quality.boundary_min`を記録

manifest件数59、Primitive件数22、showcase件数22、benchmark case件数27は変えない。

### 8.4 Docs

- [ ] 本計画のcheckbox、検証command、実測結果、未完了事項を更新
- READMEは全Primitive signatureを列挙していないため変更しない

## 9. Test項目

### 9.1 Sampling plan

- [ ] `guard_band=0.0`でexpanded寸法とsite数がnominal値と一致する
- [ ] guard 2でpitch、margin、expanded寸法、expanded site数が式どおりになる
- [ ] 同引数からsampling planとGeometryがbyte deterministic
- [ ] `guard_band`変更が出力へ反映される
- [ ] global NumPy RNG状態を変更しない

### 9.2 Soft boundary / quality

- [ ] 全出力faceの三頂点が元矩形inclusive bounds内
- [ ] bounds外siteを含むfaceが一つも残らない
- [ ] guard 2の固定fixtureでboundary min `q >= 0.30`
- [ ] seed 110 / seed 150でguard 0より品質が改善する
- [ ] candidates 1 fixtureでもguard 0よりboundary qualityが改善する
- [ ] 出力edge multiplicityが1または2
- [ ] face重複、退化、面積を持つface重なりがない
- [ ] closed / CCW / canonical順 / dtype / layout契約を維持する

### 9.3 低site / validation / resource

- [ ] `site_count=3, guard_band=0.0`で一つの閉三角形を返す
- [ ] guard後に有効faceがない場合は標準空Geometryを返す
- [ ] 負値、NaN、Infの`guard_band`を拒否する
- [ ] 拡張float32 bounds不成立をsampling前に拒否する
- [ ] expanded site hard capをsampling前に拒否する
- [ ] expanded count基準candidate work上限をsampling前に拒否する
- [ ] active `ResourceBudget`がexpanded arrays / filter maskを含む
- [ ] `activate=False`でsamplingを実行しない

### 9.4 Fill / catalog / stub / benchmark

- [ ] `E.fill(remove_boundary=True)`が全retained faceへ非空hatchを生成する
- [ ] hatchは2頂点lineだけで、中点がretained face union内にある
- [ ] `remove_boundary=False`で元の全閉faceを保持する
- [ ] `G.describe("delaunay")`が新default / meta / helpを返す
- [ ] 全parameter descriptionがmetadata testを通る
- [ ] Delaunay builtin evaluator ABIが`2`
- [ ] canonical stubがfresh generator出力と一致する
- [ ] showcase / benchmark / builtin集合と件数が同期する
- [ ] benchmark candidate checksがexpanded site数を使う

## 10. 視覚確認

- [ ] 同一seedの`guard_band=0.0`と`2.0`を左右比較するsketchを一時作成
- [ ] `G.text`で`HULL / GUARD 0`と`SOFT BOUNDARY / GUARD 2`を明記
- [ ] 境界sliver、輪郭後退、密度、空白、clipを目視確認
- [ ] fill比較も含め、全triangleが独立閉領域として描かれることを確認
- [ ] 確定PNGを
  `data/output/png/codex_generated/delaunay_guard_band_comparison_20260809_<run-id>.png`
  へ保存
- [ ] primitive showcaseを再renderし、Delaunay cellがshowcase枠内へ収まることを確認

## 11. 実装フェーズ

### Phase 0 — 承認後の差分確認

- [ ] `git status --porcelain`を再確認する
- [ ] 本計画以外の並行差分へ触れないことを確認する
- [ ] current Delaunay対象testをbaseline実行する

### Phase 1 — 失敗testとsampling plan

- [ ] guard parameter / sampling plan / quality回帰の失敗testを追加
- [ ] expanded planとvalidation / resource preflightを実装
- [ ] guard 0が現行挙動を維持することを確認

### Phase 2 — Face選別とfill

- [ ] nominal bounds内face選別を実装
- [ ] closed / canonical / quality / bounds testを通す
- [ ] fill / transform / activate=False testを通す

### Phase 3 — 公式同期面

- [ ] evaluator ABIを2へ更新
- [ ] canonical stubを再生成
- [ ] showcaseとbenchmarkを更新
- [ ] catalog / metadata / stub / showcase / benchmark testを通す

### Phase 4 — 画像確認と最終検証

- [ ] guard 0 / 2比較PNGとshowcase PNGをrender・目視確認
- [ ] 対象pytestを通す
- [ ] 変更対象へRuffを実行
- [ ] `mypy src/grafix`を実行
- [ ] `git diff --check`と新規file whitespace checkを通す
- [ ] 本計画を実測結果で更新

full test suite、長時間benchmark、CI実行はAsk-first対象とし、別途承認がない限り実行しない。

## 12. 主な検証command

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
  /opt/anaconda3/envs/gl5/bin/python -m ruff check \
  src/grafix/core/primitives/delaunay.py \
  src/grafix/core/builtins.py \
  src/grafix/devtools/benchmarks/primitive_benchmark.py \
  tests/core/primitives/test_delaunay.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/devtools/benchmarks/test_primitive_benchmark.py \
  tests/stubs/test_api_stub_sync.py \
  sketch/showcase/primitives.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m mypy src/grafix

git diff --check
```

## 13. 完了条件

- 既定`G.delaunay()`がguard band経由でsoft boundaryを生成する。
- 既定fixtureの境界最小triangle qualityが`0.30`以上になる。
- 全出力faceが元bounds内の独立した閉三角形で、fill可能である。
- 同引数からbyte deterministicなcanonical Geometryを返す。
- `guard_band=0.0`で従来の凸包モードを選べる。
- expanded workをallocation / GEOS前に正しく制限する。
- ABI、catalog、metadata、canonical stub、showcase、benchmarkが同期する。
- 比較PNGで境界sliverが消え、輪郭の後退が許容範囲であることを確認する。
- Voronoiやquality peelなど依頼外機能を混ぜない。
- 対象test、Ruff、mypy、diff checkが成功し、未実行項目を本計画へ明記する。
