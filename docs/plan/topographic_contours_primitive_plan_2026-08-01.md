# `G.topographic_contours` 公式Primitive実装計画（2026-08-01）

- 状態: **実装完了（対象検証成功、full suiteの依頼外2 failuresを記録）**
- 計画作成時branch: `main`
- 計画作成時HEAD: `3bbd71f`
- 移植元:
  `sketch/work/260801_codex.py` の `fault_garden_contours`
- 参照画像:
  `data/output/png/codex_generated/concept_03_fault_garden.png`

本計画は、Fault Garden作品内で定義した「Topographic Contours」を、再利用可能な
組み込みPrimitive `G.topographic_contours` として公式採用するためのものとする。
production code、test、生成stub、既存作品の移行は、本計画への明示承認後に変更する。

## 1. 現状と課題

現在の実装は、複数のGaussian焦点と二つの周期的な歪みから二次元scalar fieldを作り、
13段の非線形レベルをMarching Squaresで抽出している。

現在の実装には次の制約がある。

- `fault_garden_contours` という作品固有のcustom Primitiveであり、他のsketchから
  組み込みAPIとして利用できない。
- 描画領域 `TTRR`、絶対座標、六つの焦点位置がFault Gardenのlayoutへ直結している。
- Marching Squaresの各cellで得た線分を連結せず、すべて独立した2点polylineとして返す。
- 現行既定値の実測は5,826頂点、2,913 polylineで、2,913本すべてが2点線分である。
- PNG上では線が連続して見えるが、G-codeでは最大2,913回の独立strokeとなり、
  pen-up / pen-downと移動が過剰になる。

## 2. 採用する分類と責務

### 2.1 Primitiveとして追加する

公開APIは `G.topographic_contours` とする。

今回の処理は入力Geometryを加工せず、parameterから新しい線群を生成するため、
Grafixの契約上はEffectではなくPrimitiveが自然である。組み込みEffectは1個以上の
入力Geometryを必要とするが、この機能へ形式的な入力を要求しない。

### 2.2 `E.isocontour`とは分離する

- `G.topographic_contours`: Gaussian焦点から地形fieldを生成し、その等値線を出力する。
- `E.isocontour`: 入力した閉曲線のsigned distance fieldから等距離線を出力する。

両者で共用するのは低水準Marching Squares kernelだけとし、`E.isocontour`の公開API、
意味、既存出力は変更しない。任意形状への切り抜きは、既存の二入力
`E.clip`と合成する。

```python
mask = G.polygon(...)
contours = G.topographic_contours(...)
clipped = E.clip()(contours, mask)
```

## 3. ゴール

- 組み込みPrimitive `G.topographic_contours`を追加する。
- Fault Garden固有の絶対座標を除き、任意の矩形領域で再利用できるようにする。
- `seed`を含む同一parameterからbyte-stableなGeometryを生成する。
- 現在の非線形な等高線間隔と複数焦点の見た目を、公式版の基本的な造形として残す。
- GUIから焦点数、焦点の広がり、等高線数、fieldの歪み、位相、sampling精度を操作できるようにする。
- Marching Squaresの断片をopen path / closed loop単位へ連結し、plotterのstroke数を大幅に減らす。
- 既存の3色・3レイヤー構成を変えず、Fault Gardenを公式Primitive呼び出しへ移行する。
- builtin manifest、生成stub、showcase、benchmark、metadata、testを同期させる。

## 4. 非ゴール

- scalar-field callback、任意のNumPy height map、画像を入力するAPI
- `Field2D`など新しい公開データ型の導入
- Geometryの各pointをGaussian焦点として解釈するEffect版
- 焦点ごとの位置・強度・縦横半径をGUIで個別編集するnested parameter UI
- Fault Garden固有のcell名、layout、絶対座標、`variant`をcoreへ持ち込むこと
- `E.isocontour`のrename、置換、互換wrapper
- G-code export側で異なるpolylineを推測して結合・並べ替えること
- primitive showcase全体の動的収集refactor

## 5. 公開API案

初版のsignatureを次とする。既定値はzero-argument呼び出しで、非空かつ見栄えのする
Geometryが得られる値とする。視覚調整で数値を微調整しても、parameter名と単位は変えない。

```python
G.topographic_contours(
    width=80.0,
    height=100.0,
    seed=271,
    focus_count=6,
    focus_spread=1.0,
    level_count=13,
    field_warp=1.0,
    warp_frequency=1.0,
    phase=0.0,
    grid_pitch=1.0,
    center=(0.0, 0.0, 0.0),
)
```

### 5.1 Parameterの意味

- `width`, `height`
  - 出力矩形の物理サイズ。
  - 正の有限値を要求する。
- `center`
  - 出力矩形の中心 `(x, y, z)`。
  - 全等高線は `z=center[2]` の平面上へ生成する。
- `seed`
  - 焦点layout、焦点強度、warpの決定的variationを制御する整数。
- `focus_count`
  - Gaussian焦点数。1以上を要求する。
  - 個数を増やしたとき既存焦点が不用意に移動しない、安定した生成順にする。
- `focus_spread`
  - 全焦点のGaussian半径へ掛けるdimensionless倍率。正の有限値を要求する。
- `level_count`
  - 抽出する等高線level数。1以上を要求する。
- `field_warp`
  - scalar fieldへ加える細かな周期歪みの強さ。0以上の有限値を要求する。
- `warp_frequency`
  - 正規化座標上のwarp周波数倍率。0以上の有限値を要求する。
- `phase`
  - warp位相。Grafixの`wave`、`spiral`、`lissajous`と単位を揃え、degreeで受け取る。
- `grid_pitch`
  - sampling gridの目標間隔。物理座標と同じ単位で、正の有限値を要求する。
  - GUIではAdvanced / Sampling扱いにする。

### 5.2 Parameter GUI

全公開parameterへ日本語description付き`ParamMeta`を定義する。

- Layout: `width`, `height`, `center`
- Terrain Structure: `seed`, `focus_count`, `focus_spread`, `level_count`
- Terrain Detail: `field_warp`, `warp_frequency`, `phase`
- Sampling（Advanced）: `grid_pitch`

`variant`、`phase_offset`、raw focus tupleは公開しない。自動追加される`activate`を含め、
GUI、saved parameter、MIDI、capture manifestの通常契約へ乗せる。

## 6. 地形fieldの生成方針

### 6.1 正規化座標

- fieldはまず `[-0.5, 0.5] x [-0.5, 0.5]` の正規化座標で構成する。
- 焦点位置、Gaussian半径、warp周波数を正規化座標で定義し、`width` / `height`の変更で
  field topologyが不用意に変わらないようにする。
- 最後に物理矩形へ写像し、`center`を加える。
- 現在の六焦点layoutは絶対座標のまま移植せず、正規化した構成とseed生成の調整基準に使う。

### 6.2 焦点生成

- localな乱数generatorを`seed`から作り、global RNG状態を変更しない。
- 各焦点を `(cx, cy, strength, sigma_x, sigma_y)` として決定的に生成する。
- 焦点をdomain端へ密集させず、closed loopと境界で終わるopen contourの双方が現れる
  margin / spreadを採用する。
- `focus_count`増加では既存の生成列を保持し、末尾へ焦点を追加する。
- Fault Gardenの六焦点が持つ「大きな主峰、複数の小峰、重なり」を既定seedの視覚基準にする。

### 6.3 Level分布

- 現行13段の非線形なlevel profileをdimensionlessな単調templateとして保持する。
- `level_count=13`ではtemplateをそのまま使い、他の個数ではtemplate index上を補間する。
- 既定seedの造形を移植元と揃えるため、strengthもdimensionlessに正規化し、
  profile値そのものを使用する。上位levelが峰の高さを超える場合は空levelを許容する。
- 初版では`level_min`、`level_max`、`level_gamma`を公開parameterに増やさない。

### 6.4 Gridとresource budget

- `width`、`height`、`grid_pitch`から確保前にgrid点数を計画する。
- gridのX/Y端は出力矩形の境界と一致させる。必要ならX/Yで実効pitchを分け、
  requested `grid_pitch`以下のsampling間隔とする。
- `DEFAULT_MAX_GRID_POINTS`相当の上限とGeometry output budgetを配列確保前に検査する。
- 過大なサイズ、過小な`grid_pitch`、過大な`level_count`をGUIで指定しても、
  memory確保や長時間停止へ進ませず、既存diagnostic契約で明示する。
- final captureで要求精度を黙って変更しない。draft previewでcoarsenを採用する場合は、
  requested / effective pitchをdiagnosticへ記録し、finalとの違いを説明する。

## 7. Marching Squares kernelの改善

対象: `src/grafix/core/geometry_kernels/marching.py`

既存`marching_squares_loops`は閉loopだけを収集するため、矩形境界へ到達する等高線を
公式Primitiveへそのまま利用できない。既存APIと出力を維持したまま、open / closedの
双方を返す新しい低水準APIを追加する。

### 7.1 新kernelの契約

- 仮称を`marching_squares_paths`とする。
- grid edge IDをnode identityとして使い、近接座標の丸め比較で連結しない。
- degree 1のnodeからopen chainをたどり、その後に未訪問のdegree 2 cycleをたどる。
- open pathは端点を重複させず、closed loopは先頭点を末尾へ一度だけ再掲する。
- raw segmentを重複・欠落させず、それぞれ一つの出力pathへ所属させる。
- saddle caseは既存と同じcell中心値によるpairingを維持する。
- X/Yの実効pitchが異なる矩形gridを扱えるようにする。
- 出力順はgrid edge IDに基づく安定順とし、同じ入力からbyte-stableなpacked Geometryを作れるようにする。

### 7.2 既存APIとの関係

- `marching_squares_loops`のsignature、閉loop限定の意味、既存testを壊さない。
- `E.isocontour`、`E.metaball`、`E.reaction_diffusion`の出力は変更しない。
- 新Primitiveだけがopen / closed両対応APIを使用する。
- 異なる等値線pathをpen-down bridgeで結合しない。連続性がfield topologyで保証された
  segmentだけを同一polylineへまとめる。

## 8. 追加・変更するファイル

### 8.1 Production

- [x] `src/grafix/core/primitives/topographic_contours.py`（新規）
  - `@primitive(meta=topographic_contours_meta)`を定義する。
  - field生成、parameter validation、resource確認、path packingを実装する。
  - NumPy styleの日本語docstringと型hintを付ける。
- [x] `src/grafix/core/geometry_kernels/marching.py`
  - open chain / closed loop対応のstitch処理と新APIを追加する。
  - 既存loop APIの挙動を維持する。
- [x] `src/grafix/core/builtins.py`
  - `_PRIMITIVE_NAMES`末尾へ`topographic_contours`を追加し、既存locator順を変えない。
- [x] `src/grafix/api/__init__.pyi`
  - 正規stub generatorから再生成する。

### 8.2 Showcaseとbenchmark

- [x] `sketch/showcase/primitives.py`
  - 21番目のsampleとして軽量な`topographic_contours`を追加する。
  - 4列のまま6行目を追加し、canvas高さを増やす。
  - 既存20件の位置とsample parameterを変えない。
- [x] `src/grafix/devtools/benchmarks/primitive_benchmark.py`
  - direct raw actual-work caseを1件追加する。
  - grid点数、focus数、level数、出力path数をwork metricsへ含める。
- [x] `tests/devtools/benchmarks/test_primitive_benchmark.py`
  - builtin集合へ追加し、通常suiteのcase数を25から26へ更新する。
  - exact checksumと共通hard contractを確認する。

### 8.3 Fault Garden移行

- [x] `sketch/work/260801_codex.py`
  - `_CONTOUR_LEVEL_TEMPLATE`、`_contour_levels`、`_contours`、
    `fault_garden_contours`を削除する。
  - `_layout(...).leaf_map()["TTRR"]`から`width`、`height`、`center`を算出し、
    `G(name="Topographic Contours").topographic_contours(...)`を呼ぶ。
  - GUI名と`key="topographic_contours"`を維持する。
  - `ink`へ含める位置と3色・3レイヤー構成を変えない。
  - 既存参照に近づくseed、spread、warp、phaseをPNG比較で調整する。

## 9. テスト計画

### 9.1 Marching kernel

対象: `tests/core/test_geometry_kernels.py`

- [x] 単純な線形fieldから、境界間を結ぶ1本のopen pathが得られる。
- [x] 円形fieldから、先頭末尾が一致するclosed loopが得られる。
- [x] open pathとclosed loopが混在しても両方を欠落なく返す。
- [x] 矩形境界で終わるpathの端点が正しい位置になる。
- [x] X/Y pitchが異なるgridでも物理座標が正しい。
- [x] saddle caseのpairingが既存規則と一致する。
- [x] 同じfieldからpath順、座標、closureが決定的に得られる。
- [x] 既存`marching_squares_loops`のtestと利用Effectが退行しない。

### 9.2 Primitive単体

新規: `tests/core/primitives/test_topographic_contours.py`

- [x] zero-argument呼び出しが非空Geometryを返す。
- [x] `coords`がC-contiguous / writable / `float32 (N, 3)` / finiteである。
- [x] `offsets`がC-contiguous / writable / `int32 (M+1,)`でpacked契約を満たす。
- [x] 全座標が`width`、`height`、`center`から決まる矩形bounds内に収まる。
- [x] 全Z座標が`center[2]`と一致する。
- [x] 同じ引数とseedから座標・offsetsがbyte単位で一致する。
- [x] `seed`変更で出力が変わる。
- [x] `focus_count`、`focus_spread`、`level_count`、`field_warp`、
  `warp_frequency`、`phase`、`grid_pitch`がそれぞれ出力へ反映される。
- [x] `activate=False`で空Geometryとなり、evaluator本体を実行しない。
- [x] 不正なsize、count、spread、warp、pitch、非有限値を副作用前に拒否する。
- [x] resource上限を超えるgridを大規模配列確保前に拒否する。
- [x] 標準fixtureの出力に3頂点以上のpolylineが含まれ、すべてが2点断片へ退行しない。
- [x] 標準fixtureのstroke数が現行2,913本より大幅に少ない。
- [x] raw evaluatorの連続呼び出しが共有可変配列を返さない。
- [x] 固定fixtureのexact checksumを持つ。

### 9.3 Catalog、GUI、stub、合成

- [x] `tests/core/test_builtin_catalog_bootstrap.py`のmanifest件数を57から58へ更新する。
- [x] builtin locator、import-order、lazy bootstrapが維持される。
- [x] `G.describe("topographic_contours")`が`n_inputs=0`、default、meta、helpを正しく返す。
- [x] 全公開parameterにdescriptionがあり、metadata completeness testを通る。
- [x] `tests/stubs/test_api_stub_sync.py`を通し、checked-in stubとgenerator出力を一致させる。
- [x] `tests/sketch/test_primitive_showcase.py`を通し、manifestとshowcaseを一致させる。
- [x] `E.translate`、`E.rotate`、二入力`E.clip`と合成してstandard Geometryを返す。

## 10. 視覚・plotter検証

- [x] 変更前のFault Garden PNGとGeometry統計をbaselineとして保存する。
- [x] 公式Primitiveへ移行した`sketch/work/260801_codex.py`をheadless PNGでrenderする。
- [x] `concept_03_fault_garden.png`と比較し、主峰、小峰、線間隔、境界へ抜ける等高線を確認する。
- [x] 線分連結による微小な座標差は許容するが、主要なcontour topologyと密度を維持する。
- [x] Topographic Contours以外の赤・緑・黒geometryと3レイヤー構成が変わっていないことを確認する。
- [x] output offsetsからstroke数と各polyline頂点数を集計し、2点断片の大幅削減を確認する。
- [x] G-codeを一度出力し、等高線途中で不要なpen-upが発生しないことを確認する。
- [x] 確定PNGを衝突しない名前で
  `data/output/png/codex_generated/`へ保存する。

## 11. 実装フェーズ

### Phase 0 — 実装直前baseline

- [x] Primitive / Effectの責務を確認し、Primitive採用を決定した。
- [x] 現行出力が5,826頂点・2,913本の2点polylineであることを確認した。
- [x] 既存`marching_squares_loops`がclosed loop専用であることを確認した。
- [x] 計画作成時の`git status --porcelain`がcleanであることを確認した。
- [x] 実装開始時に`git status --porcelain`を再確認し、並行差分へ触れない。
- [x] 現行PNG、checksum、頂点数、line数をbaseline test/logへ記録する。

### Phase 1 — Marching path kernel

- [x] 先にopen / closed pathの失敗testを追加する。
- [x] degree 1 chainとdegree 2 cycleの決定的stitchを実装する。
- [x] 非正方pitchと境界端点を扱う。
- [x] 既存loop APIと既存Effectの回帰testを通す。

### Phase 2 — Primitive本体

- [x] 正規化field、seeded focus、level profile、warpを実装する。
- [x] parameter validationとresource budgetを実装する。
- [x] 新Marching APIから連続polylineをpackする。
- [x] ParamMeta、docstring、公開型契約を完成させる。
- [x] Primitive単体testを通す。

### Phase 3 — 公式登録と同期面

- [x] builtin manifestへ登録する。
- [x] showcase sampleと6行目を追加する。
- [x] actual-work benchmark caseとmetricsを追加する。
- [x] fresh processでchecked-in API stubを再生成する。
- [x] catalog、metadata、stub、showcase、benchmark testを通す。

### Phase 4 — Fault Garden移行

- [x] custom contour実装を削除し、公式Primitive呼び出しへ置換する。
- [x] GUI名、parameter key、3レイヤー、他geometryを維持する。
- [x] 参照画像とのrender比較でparameterを調整する。
- [x] plotter向けstroke削減をGeometry統計とG-codeで確認する。
- [x] 確定PNGを`data/output/png/codex_generated/`へ保存する。

### Phase 5 — 最終検証

- [x] 対象pytestを通す。
- [x] `ruff check`を変更対象へ実行する。
- [x] `mypy src/grafix`を実行する。
- [x] `git diff --check`を通す。
- [x] 影響範囲確認後にfull `PYTHONPATH=src pytest -q`を実行する。
- [x] 本書の完了項目、検証command、結果、未完了事項を更新する。

## 12. 主な検証command

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m pytest -q -p no:cacheprovider \
  tests/core/test_geometry_kernels.py \
  tests/core/primitives/test_topographic_contours.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/sketch/test_primitive_showcase.py \
  tests/devtools/benchmarks/test_primitive_benchmark.py \
  tests/stubs/test_api_stub_sync.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python -m grafix stub --no-default-import \
  --output src/grafix/api/__init__.pyi

PYTHONPATH=src python -m grafix describe primitive topographic_contours
ruff check src/grafix tests sketch/showcase/primitives.py sketch/work/260801_codex.py
mypy src/grafix
git diff --check
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q -p no:cacheprovider
```

実装時はrepositoryで使用中のPython environmentへcommandを合わせる。依存追加は行わない。

## 13. 完了条件

- `G.topographic_contours()`がzero-argumentで利用でき、Parameter GUIから主要な造形を操作できる。
- 同じ引数とseedから同じ標準packed Geometryが得られる。
- 出力は指定矩形内に収まり、open contourとclosed contourの双方を保持する。
- 現行のcell単位2点線分ではなく、topology単位の連続polylineとして出力される。
- 標準fixtureとFault Gardenでstroke数が大幅に減り、不要なpen-upが抑えられる。
- `E.isocontour`および既存のfield系Effectの出力が退行しない。
- builtin manifest、stub、showcase、benchmark、metadataが新Primitiveと同期している。
- Fault Gardenがcustom contour定義を持たず、公式Primitiveを使用して参照画像に近い見た目を維持する。
- Fault Gardenの3色・3レイヤー構成が維持される。
- 対象test、lint、type check、render、G-code確認が成功し、full test suiteの結果が記録される。

## 14. 実装結果（2026-08-01）

### 14.1 Geometryとplotter

- 移植前custom実装:
  - 5,826頂点
  - 2,913 polyline
  - 2,913本すべてが2点線分
- Fault Gardenの公式Primitive移行後:
  - 2,923頂点
  - 24 polyline
  - 2点polylineは0本
  - 最長polylineは326頂点
- field topologyで連続するsegmentだけを結合し、異なるcontour間のbridgeは追加していない。
- full sketchのG-code出力に成功した。

### 14.2 Visual artifact

- 参照画像とheadless PNGを比較し、大主峰、小峰、線間隔、境界へ抜けるopen contourを維持した。
- 3色・3レイヤー構成を維持した。
- 確定PNG:
  `data/output/png/codex_generated/260801_codex_topographic_contours_builtin.png`

### 14.3 検証結果

- 変更対象Ruff: pass
- `mypy src/grafix`: 292 source files / no issues
- `git diff --check`: pass
- Topographic Contours、Marching kernel、既存field Effect、catalog、showcase、
  benchmark、stub同期の対象suite: 172 passed
- Primitive単体の最終追加test: 33 passed
- full suite: 4,116 passed / 2 failed

full suiteの2 failuresは変更対象外で、個別再実行でも再現した。

1. `tests/api/test_runner_parameter_recovery.py::test_gui_construction_failure_closes_completed_draw_system_and_midi_once`
   - test doubleの`capture_service=object()`に、現行productionが要求する
     `_export_owned`がないため失敗する。
   - 今回は`src/grafix/api/_runner_application.py`と当該testを変更していない。
2. `tests/sketch/test_active_sketch_entrypoints.py::test_active_sketch_inventory_is_not_empty`
   - 現在のentrypoint実数53に対して既存期待値が52のため失敗する。
   - 今回は新しいsketch entrypointを追加していない。

今回の実装範囲に未完了項目はない。上記2件は依頼外の既存test不整合として残す。
