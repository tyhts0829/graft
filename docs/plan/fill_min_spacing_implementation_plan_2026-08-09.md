# `E.fill(min_spacing)` 実装計画（2026-08-09）

- 状態: **承認待ち（実装未着手）**
- 計画作成時 branch: `main`
- 計画作成時 HEAD: `4d76235`
- 対象: `E.fill` が生成する平行ハッチ走査線の最小ピッチ
- 公開名: `min_spacing`（`min_spaceing` という綴りは採用しない）

## 0. 要約

`E.fill` に、次の keyword-only 引数を追加する。

```python
E.fill(
    density=800.0,
    min_spacing=0.3,
    spacing_gradient=0.0,
)
```

`density` は従来どおり図形サイズに対する希望密度を表し、`min_spacing` はそれより優先する
絶対的な下限とする。`min_spacing=0.0` は制約無効で、既存出力を変えない。

この制約は G-code exporter で近接線を推測して削除するのではなく、ハッチの意味情報を持つ
`fill` の走査線生成時に適用する。これにより SVG、PNG、プレビュー、G-code が同じ
RealizedGeometry を使い、過密な線を最初から生成しない。

本計画は `E.fill` の制御点を実装するものであり、共有 preset と `sketch/readme/grn/18.py`
には正の値を設定しない。そのため、本計画だけを実装して同じ `18.py` を再 export しても
出力は変わらない。実機で値を較正した後の作品/preset への opt-in は、別タスクとして扱う。

保証するのは、**fill 評価時の作業平面における、同一 filled region・同一 hatch 方向の
連続する異なる走査線レベル間の垂直な中心線ピッチ**である。全出力線分どうし、境界線、
異なる文字、cross-hatch、後段変形後の機械空間まで含む clearance ではない。

## 1. 作業ツリー境界

計画作成時点では、直前の G-code レイヤ最適化実装に関する tracked 差分と、次の未追跡
ファイルが存在する。

- `docs/migration_2026-08-09.md`
- G-code、Layer、stub、test、architecture 関連の tracked 差分

これらは本計画の作成対象ではない。実装時も既存差分を restore、移動、削除、stage せず、
この計画で列挙した `fill` 関連差分だけを追加する。特に生成 stub は既存の
`gcode_optimize` 差分を保持したまま再生成する。

実装開始時に改めて `git status --porcelain` を記録し、並列作業で追加された依頼外差分を
作業対象へ混ぜない。

## 2. 背景と現行挙動

### 2.1 現在の密度指定

`src/grafix/core/effects/fill.py` は、参照高さ `height` と `density` から概ね次のように
基準間隔を計算する。

```python
num_lines = clamp(round(density), 2, 1000)
base_spacing = height / num_lines
```

したがって `density` は mm 単位の線間隔ではなく、図形高さに対する本数スケールである。
小さい文字や非常に大きい density では、実プロッターのペン、紙、インクに対して
ハッチ中心線が過密になり、滲みや塗り潰れが起こり得る。

`spacing_gradient` が 0 でない場合、各反復 step は `base_spacing` に指数係数を掛けて
決まる。公開範囲 `-4 <= spacing_gradient <= 4` では、局所 step が基準間隔の約 7.46%
まで縮み得る。そのため基準間隔だけを一度 clamp しても、局所的な最小ピッチは保証できない。

### 2.2 `grn/18.py` で問題が顕在化する理由

`sketch/readme/grn/18.py` が使う `P.grn_a5_frame` では、共有 preset 内の文字 fill に
次の高い density が使われている。

- `Grafix / Design / Studies`: `838.488`
- 説明文字: `450.0`
- 作品番号: `100.0`
- bar: `80.0`

現在の template layer は 11,610 polylines を含み、その大半が 2 頂点の fill hatch である。
これは `density` だけでは実プロッター上の最小ピッチを直接指定できないことを示す実例である。

### 2.3 なぜ exporter ではなく fill 側か

G-code exporter が受け取る `RealizedGeometry` は最終的な `coords + offsets` だけであり、
次の provenance を持たない。

- fill 由来か、輪郭・文字・通常線由来か
- filled region、outer/hole、glyph の所属
- hatch の方向 family
- scanline level

2 頂点 polyline や近接平行線を fill と推測して間引くと、文字輪郭、意図的な反復線、
cross-hatch、別 effect の線を誤って消す。これは意味情報のない heuristic を exporter に
再導入することになる。

一方 `fill` は走査線生成時に region、angle family、作業平面、scanline level をすべて
知っている。生成 step を O(1) で clamp でき、後処理も provenance 拡張も不要である。

## 3. ゴール

- `E.fill(..., min_spacing=x)` で過密なハッチ生成を抑えられる。
- `min_spacing=0.0` または引数省略時は、現行の coords、offsets、順序、位相を維持する。
- 一様間隔と `spacing_gradient` の正負両方向で、実際の各走査 step に下限を適用する。
- planar-global 経路と nonplanar-local 経路の両方で同じ契約を適用する。
- `density=0` の「ハッチなし」契約を維持する。
- `remove_boundary`、even-odd hole、複数 angle family、3D planar 復元を壊さない。
- 高 density 時は scanline を生成後に捨てず、生成前から本数を抑える。
- Inspector、parameter GUI、`describe`、生成 stub へ新引数を自動反映する。
- builtin `fill` の evaluator ABI を更新し、旧 evaluator/cache identity と混同しない。
- SVG、PNG、G-code など exporter ごとの特別処理を追加しない。

## 4. 非ゴール

- ペン幅、インク量、紙の吸収性、描画速度から安全値を自動推定すること
- `min_spacing` の既定値を特定のペン向けの正値にすること
- 全線分ペア間、ink edge 間、境界との間に正の clearance を保証すること
- `angle_sets > 1` の異方向 family の交差を避けること
- 別 outer group、別 glyph、別 fill call、別 Layer 間のピッチを揃えること
- group 間で共通 lattice phase を導入すること
- hole により同一 scanline が分割された線分間を離すこと
- `remove_boundary=False` の入力境界を移動・削除すること
- G-code の `bridge_draw_distance` が追加する connector を制御すること
- exporter で近接線を自動削除・間引き・警告すること
- generic な lossy line-thinning effect を新設すること
- `spacing` という density 代替 mode、または density と排他的な absolute pitch modeを追加すること
- `min_spaceing` の typo alias、互換 wrapper、shim を追加すること
- `sketch/presets/grn/a5_frame.py` や既存作品へ、未較正の物理値を一括設定すること
- 既存 PNG / SVG / G-code artifact を一括再生成すること

## 5. 公開 API 契約

### 5.1 signature と metadata

公開 signature は次の順にする。全引数は既に keyword-only なので、既存呼び出しは
source-compatible である。

```python
def fill(
    g: GeomTuple,
    *,
    angle_sets: int = 1,
    angle: float = 45.0,
    density: float = 35.0,
    min_spacing: float = 0.0,
    spacing_gradient: float = 0.0,
    remove_boundary: bool = False,
) -> GeomTuple:
    ...
```

`fill_meta` へ次を追加する。

- `kind="float"`
- `ui_min=0.0`
- `ui_max=10.0`（scene 単位での編集用初期レンジ。runtime clamp ではない）
- 説明: 同一方向の隣接ハッチ走査線に適用する、fill 評価時の作業平面の scene 座標単位での
  最小ピッチ。標準 2D plot では通常 mm に対応し、0 で無効。

`unit="mm"` は設定しない。通常の 2D plot 用 canvas では 1 scene unit を 1 mm として
G-codeへ渡すが、fill 後の scale、warp、3D 投影まで含む一般契約ではないためである。
NumPy style docstring では、値が **fill 評価時の作業平面上の scene 座標単位**であることと、
標準 2D plot では通常 mm と一致することを説明する。

### 5.2 値域と優先順位

- `min_spacing` は有限な `float` として解決し、`0.0 <= min_spacing` を要求する。
- `0.0` は floor 無効であり、現行挙動と配列単位で同一にする。
- 負値、`nan`、`+inf`、`-inf` は受理しない。
- `density` は希望密度、`min_spacing` は hard constraint とする。
- floor 無しで生成した全ての実 step が下限以上なら、結果を変更しない。
- nominal step または gradient step が下限未満なら、`min_spacing` を優先して実 step を広げる。
- `density=0` は従来どおりハッチを生成しない。正の `min_spacing` が線生成を有効化してはならない。
- 下限が領域の scan extent より大きい場合、当該 region/family の走査線は高々 1 level になる。

値域検証は empty geometry の early return より前に行う。parameter schema の finite/type 検証に
加え、evaluator 自身も非有限値と負値を明示拒否し、直接呼び出しでも契約を保つ。

### 5.3 厳密な保証範囲

正の `min_spacing=d` に対し、float32 の数値許容差内で次を保証する。

> 同じ `fill` 評価に属する一つの planar filled region/group と、一つの hatch angle family
> の中で、連続する異なる scanline level の作業平面上の垂直距離は `d` 以上である。

ここで hole により一つの scanline が複数の 2 点線分へ分割されても、それらは同じ level と
数える。線分 endpoint 間距離や最終 stroke 順の隣接距離を測る契約ではない。

### 5.4 明示する非保証

- `angle_sets > 1` の異方向 family は交差でき、線分間距離は 0 になり得る。
- 別 face/group/glyph は独立した scan phase を持ち、group 横断の下限は保証しない。
- boundary と最初の hatch、boundary と hatch endpoint の距離は保証しない。
- 同じ level が hole で分割された線分同士の supporting-line 距離は 0 である。
- 別 `E.fill` 呼び出し、別 Layer、通常線との距離は保証しない。
- ペン幅を差し引いた ink edge clearance や、滲みそのものを保証しない。
- fill 後の uniform shrink、non-uniform scale、warp/displace、3D から G-code XY への投影は
  ピッチを縮め得る。距離を変える transform は原則として fill より前へ置く。
- G-code の Y 反転と origin 移動は等長だが、`decimals` 量子化で微小な差が生じ得る。
- `bridge_draw_distance` は別契約で connector を追加でき、`min_spacing` とは連動しない。

## 6. 実装設計

### 6.1 `_generate_y_values` で実 step を clamp する

`_generate_y_values()` に `min_spacing` を渡し、一様・gradient の双方で次を適用する。

```python
uniform_step = max(base_spacing, min_spacing)
minimum_gradient_step = max(base_spacing * 1e-3, min_spacing)
gradient_step = max(base_spacing * factor, minimum_gradient_step)
```

実装では `min_spacing == 0.0` の旧算術経路を保ち、`np.arange`、gradient の正規化、
float32 化、fallback midpoint を不必要に変更しない。これにより引数省略時と明示 0.0 の
array-exact 互換をテスト可能にする。

### 6.2 最初の走査線 phase は変更しない

最初の level は現行どおり次で決める。

```python
start = min_y + 0.5 * base_spacing
```

`0.5 * max(base_spacing, min_spacing)` へは変更しない。`min_spacing` は boundary clearance では
なく隣接 level 間ピッチの契約であり、phase まで移動する必要はないためである。これにより
正の floor を使った場合も必要な箇所以外の位置変化を最小化する。

### 6.3 引数伝播

`min_spacing` を次の一経路で渡す。

1. 公開 `fill()`
2. planar-global の group × angle family call site
3. nonplanar-local の polyline × angle family call site
4. `_generate_line_fill_evenodd_multi()`
5. `_generate_y_values()`

global planar 経路の全体参照高さ、group ごとの even-odd 処理と独立 phase、nonplanar 経路の
polyline ごとの PlanarFrame は変更しない。private helper の旧 signature を残す wrapper は作らない。

### 6.4 geometry と exporter

- hatch は従来どおり各 2 点を独立 polyline として pack する。
- boundary、offsets、polyline 順序の規約を変えない。
- G-code、SVG、PNG、GL exporter に条件分岐を追加しない。
- `RealizedGeometry`、Geometry DAG、Layer、GCodeParams、runtime config に field を追加しない。
- scanline 生成後の pairwise 距離検査や削除 pass を追加しない。

### 6.5 builtin evaluator ABI と cache identity

builtin operation は実装 source ではなく manifest の明示 ABI と固定 marker から evaluation
fingerprint を作る。したがって `fill` の生成意味を変える本実装では、
`src/grafix/core/builtins.py` の `fill` evaluator ABI を `"1"` から `"2"` へ上げる。

現状の primitive/effect 一括 comprehension は維持しつつ、最小の明示 override map、例えば
`{"fill": "2"}` を effect manifest 生成時に参照し、他 effect の ABI は `"1"` のままにする。
全 operation の ABI を一括更新しない。

新 parameter により schema fingerprint も更新される。ABI bump はそれとは別に evaluator/cache
契約を失効させる責務を持つ。旧 fingerprint を受け入れる shim、旧 cache の移行、
`@effect(version=...)` による代用は追加しない。

## 7. テスト計画

### 7.1 scanline generator 単体

`tests/core/effects/test_fill.py` に、走査線 level を直接検証する focused test を追加する。

- [ ] `spacing_gradient=-4.0, 0.0, 4.0` の各ケースで、`base_spacing < min_spacing` のとき
  level が単調増加し、全 `np.diff(levels)` が下限以上になる。
- [ ] 各ケースで level が 2 本以上生成される fixture を使い、空虚な assertion を避ける。
- [ ] `min_spacing=0.0` が、新 helper を呼ばない凍結した旧算術の test-local reference
  または実装前に固定した golden level 配列と array-exact で一致する。
- [ ] 一様間隔では `min_spacing < base_spacing`、gradient では `min_spacing` が legacy level
  列の全 `np.diff` 以下という、floor が実際に発動しない条件で旧 level 配列と
  array-exact に一致する。
- [ ] 有効な `base_spacing` と正の extent に対し、`min_spacing` が extent より大きいとき
  private generator が exactly 1 level を返す。公開 clipping 結果は形状により高々 1 level とする。
- [ ] float32 丸めを考慮し、下限判定には level の絶対座標スケール由来の小さい tolerance を使う。

数値判定の目安は次とし、fixture は原点近傍かつ `min_spacing` が tolerance より十分大きい
範囲に置く。helper は level の絶対座標スケールを含め、assert 自体が空虚にならないことも
確認する。

```python
tolerance = max(
    np.finfo(np.float32).eps * max(1.0, float(np.max(np.abs(levels)))) * 8.0,
    abs(min_spacing) * 2e-6,
)
assert 0.0 < tolerance < min_spacing * 0.1
assert np.all(np.diff(levels) >= min_spacing - tolerance)
```

### 7.2 公開 `E.fill` 統合

- [ ] 高 density の XY square、正の floor で、distinct scanline level 数が legacy より減り、
  level 差が下限以上になる。
- [ ] angle が 0 以外でも、2 点線分の midpoint を hatch normal へ射影して level を復元する。
  分類後に 2 distinct levels 以上あることを先に assert してから垂直ピッチを検証する。
- [ ] 引数省略と `min_spacing=0.0` を、coords と offsets の array-exact 比較で固定する。
- [ ] legacy の全実 step が既に floor 以上なら output が array-exact で変わらない。
- [ ] `spacing_gradient=-4.0` と `4.0` の両方で、全ての実 step に floor が効く。
- [ ] `angle_sets=2` は方向で family を分類し、各 family に 2 distinct levels 以上あることを
  assert してから family ごとにだけ下限を検証する。family 横断距離 0 は違反扱いしない。
- [ ] square-with-hole では、hole 帯を横断して同じ level が 2 segment へ分割された witness を
  1 本以上確認する。その重複 level を deduplicate してからピッチを測り、hole 内部に線が
  生成されない既存契約も維持する。
- [ ] x 方向に離した 2 square を同時に fill し、各 group に 2 distinct levels 以上あることと
  group 内 floor を確認する。group 横断の level 差や共通 phase は assert しない。
- [ ] 単一 square の `remove_boundary=False` では、入力 boundary polyline が出力 prefix として
  array-exact に保持され、先頭 offsets が `[0, input_vertex_count]` であることを確認する。hatch 追加後の
  全 offsets 配列との完全一致は要求しない。
- [ ] `density=0` と正の `min_spacing` の組み合わせでも hatch を生成しない。
- [ ] positive-floor 用の新規 test は上記の最小 fixture に集約し、既存の grouping、
  text `"o"`、boundary、rotation test は `min_spacing=0.0` 回帰としてそのまま通す。

### 7.3 3D / local 経路

- [ ] 全体は nonplanar だが各 polyline は planar な入力を用意し、入力全体が
  `is_planar=False`、各対象 polyline が `valid and is_planar=True` であることを先に assert する。
- [ ] 少なくとも一面を XZ または oblique plane にし、出力を面ごとに元の PlanarFrame へ
  投影する。各面で 2 distinct levels 以上を確認して local 経路の floor を測り、XY 平面だけで
  local/world 往復を空虚化しない。
- [ ] tilted planar、真に nonplanar な単一 ring、transform 合成は既存 test を
  `min_spacing=0.0` の回帰として通す。後段縮小が保証外であることは docstring Notes に記し、
  新しい warp/scale characterization test は作らない。

### 7.4 validation

- [ ] 負値を empty input の early return より前に `ValueError` とする。
- [ ] `nan`、`+inf`、`-inf` を公開 schema 境界で拒否する。
- [ ] evaluator 自身の契約は、公開 `E` wrapper を通さない direct call と empty geometry を使い、
  負値と非有限値を early return より前に拒否することを確認する。
- [ ] `bool`、文字列、list/tuple の groupwise sequence を parameter schema で拒否する。
- [ ] `tests/api/test_operation_argument_validation.py` の fill scalar 一覧へ `min_spacing` を追加する。
- [ ] 整数など schema が受理する実数入力は canonical float として扱う。

### 7.5 metadata、stub、catalog、cache

- [ ] `fill_meta` と NumPy docstring に新引数の説明があり、description completeness test を通す。
- [ ] `describe effect fill` / catalog view が default、型、UI range、説明を表示する。
- [ ] `src/grafix/api/__init__.pyi` の `_E.fill` と `_EffectBuilder.fill` に新 keyword が出る。
- [ ] `typings/grafix/api/__init__.pyi` の同 2 signature にも新 keywordが出る。
- [ ] packaged stub は fresh generator output と byte-exact に一致する。
- [ ] project-local stub は、既存 preset に同名引数が存在しても global occurrence 数を数えず、
  `_E.fill` と `_EffectBuilder.fill` の各 signature 内に新 keyword があることを個別検証する。
- [ ] builtin manifest で `effect:fill` だけ ABI が `"2"`、他の既存 effect は `"1"` のままである。
- [ ] 独立に構築した同値 DAG を別 `RealizeSession` で評価し、cache hit の再利用だけに
  ならない条件で output が array-exact に決定的である。

### 7.6 既存回帰

既存の fill test が持つ以下の契約は削除・弱体化しない。

- even-odd outer/hole grouping
- touching polygon を hole と誤認しないこと
- boundary 有無
- tilted ring と collinear start
- empty / degenerate / nonplanar fallback
- angle family 数と方向
- Numba / NumPy scanline endpoint path の一致
- rotation / translation / scale 合成

## 8. stub と documentation の同期

### 8.1 自動生成物

signature と `ParamMeta` は catalog introspection に載るため、
`src/grafix/devtools/generate_stub.py` の生成ロジック変更は不要である。正規コマンドから次を
再生成する。

- `src/grafix/api/__init__.pyi`
- `typings/grafix/api/__init__.pyi`

両ファイルには既に別タスクの未 commit 差分があるため、再生成前後の diff を確認し、
`min_spacing` 以外の既存差分を消さない。

### 8.2 公開説明

canonical な公開説明は `fill_meta` と `fill()` の NumPy style docstring に置く。
GUI Help と生成 stub も意味を失わないよう、scene 座標単位、標準 2D plot では通常 mm、
0 で無効という単位契約を `fill_meta.description` にも含める。docstring には Parameters、
Raises、Notes を追加し、保証範囲と非保証範囲を簡潔に記録する。

本変更は default 0.0 の additive な keyword-only API であり、architecture の依存方向、config
schema、export contract を変えない。そのため `architecture.md`、runtime config、migration 文書、
README の変更は必須対象にしない。実装中に canonical な fill API 一覧が別途見つかった場合だけ、
重複説明を増やさず同期対象へ追加する。

## 9. 高 density 文字の再現 acceptance（preset 非変更）

unit test とは別に、`P.grn_a5_frame` の高 density 文字と同じ text geometry を `/tmp` の
読み取り専用計測スクリプトで再現する。これは実作品の問題を再現する計測であり、preset 本体や
実際の `18.py` 出力を変更するものではない。

比較条件の例:

1. `min_spacing=0.0`
2. `min_spacing=0.2`（測定用例。既定値や推奨物理値にはしない）

記録する値:

- full title の fill hatch polyline 数と vertex 数（経験的な削減量）
- 単一 glyph / 単一 even-odd group、または private generator に限定した angle family ごとの
  distinct level 最小ピッチ
- realization 時間
- 独立 DAG・別 `RealizeSession` 間の checksum / array equality
- 必要なら `/tmp` へ出した preview の見た目

受入条件:

- 単純な convex region では正の floor により distinct level 数が legacy より増えない。
- full title の hatch polyline 数は観測値として記録し、高 density 文字で明確に減ることを確認する。
  ただし hole による分割数は level ごとに異なるため、全形状に対する総 polyline 数非増加を
  公開契約にはしない。
- provenance を混ぜない単一 group/family で測定した最小ピッチが tolerance 内で指定値以上になる。
- boundary と glyph hole が維持される。
- `min_spacing=0.0` の結果が実装前 baseline と array-exact に一致する。
- 正の floor を指定した結果も独立 session 間で決定的である。

ペン、紙、インクごとの実用値はこの acceptance では確定しない。実機で安全値を較正した後、
必要なら別タスクで `P.grn_a5_frame` に `fill_min_spacing` を公開し、各内部 `E.fill` へ渡す。
その follow-up では preset signature、metadata、project-local stub、対象作品の見た目を別途確認する。

## 10. 変更予定ファイル

### production

- [ ] `src/grafix/core/effects/fill.py`
- [ ] `src/grafix/core/builtins.py`

### generated API artifacts

- [ ] `src/grafix/api/__init__.pyi`
- [ ] `typings/grafix/api/__init__.pyi`

### tests

- [ ] `tests/core/effects/test_fill.py`
- [ ] `tests/api/test_operation_argument_validation.py`
- [ ] `tests/core/test_builtin_catalog_bootstrap.py`
- [ ] `tests/stubs/test_api_stub_sync.py`
- [ ] 必要な場合のみ `tests/devtools/test_generate_stub_semantic_meta.py`

### docs

- [x] `docs/plan/fill_min_spacing_implementation_plan_2026-08-09.md`（本計画）

### 原則として変更しないファイル

- `src/grafix/devtools/generate_stub.py`
- `src/grafix/export/gcode.py`
- `src/grafix/core/realized_geometry.py`
- `src/grafix/core/layer.py`
- runtime config / capture / worker / serializer
- `sketch/presets/grn/a5_frame.py`
- `sketch/readme/grn/18.py`
- `architecture.md`
- 既存 migration 文書

## 11. 実装フェーズ

### Phase 0: 境界確認

- [ ] `git status --porcelain` と HEAD を再記録する。
- [ ] 依頼外差分、特に既存 G-code/stub 差分を識別する。
- [ ] 現行 fill の omitted/explicit 0 相当となる baseline checksum を `/tmp` に記録する。

### Phase 1: RED test

- [ ] level generator の一様/gradient floor test を追加して RED を確認する。
- [ ] public square/hole/angle family/local planar test を追加する。
- [ ] invalid value、stub、ABI test を追加する。
- [ ] 既存 test を新仕様に合わせて安易に書き換えず、追加契約として失敗させる。

### Phase 2: 最小 production 実装

- [ ] public signature、metadata、validation、docstring を追加する。
- [ ] `min_spacing` を global/local 両経路へ伝播する。
- [ ] uniform と gradient の実 step を clamp する。
- [ ] start phase と packed output 契約を維持する。
- [ ] fill evaluator ABI だけを `"2"` へ上げる。
- [ ] exporter、Geometry metadata、generic thinning pass を追加していないことを diff で確認する。

### Phase 3: stub 同期

- [ ] packaged config / `--no-default-import` で packaged stub を正規生成する。
- [ ] project config を使う正規経路で project-local stub を生成する。
- [ ] 既存の並列差分を保持し、fill signature の差分だけが追加されたことを確認する。

### Phase 4: focused validation

- [ ] fill、argument validation、builtin catalog、stub、metadata の focused pytest を通す。
- [ ] 変更対象の ruff を通す。
- [ ] production source の mypy を通す。
- [ ] `git diff --check` を通す。
- [ ] `git status --porcelain` で依頼外差分を変更していないことを確認する。

### Phase 5: 高 density 文字の再現 acceptance とレビュー

- [ ] 高 density text の 0.0 / 0.2 比較値を本計画の実施結果欄へ記録する。
- [ ] floor、hole、boundary、決定性を確認する。
- [ ] 独立レビューで API、数値契約、ABI、test の過不足を確認する。
- [ ] finding を解消後、完了項目と未完了項目を本計画へ反映する。

### Phase 6: broad validation（承認がある場合）

- [ ] effects/API/stub/catalog の broader test を実行する。
- [ ] full pytest は長時間実行に当たるため、必要性を説明して承認後にのみ実行する。

## 12. 検証コマンド案

```bash
PYTHONDONTWRITEBYTECODE=1 \
NUMBA_CACHE_DIR=/tmp/grafix-numba-fill-min-spacing \
PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/core/effects/test_fill.py \
  tests/api/test_operation_argument_validation.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/core/parameters/test_description_completeness.py \
  tests/devtools/test_generate_stub_semantic_meta.py \
  tests/stubs/test_api_stub_sync.py

/opt/anaconda3/envs/gl5/bin/ruff check \
  src/grafix/core/effects/fill.py \
  src/grafix/core/builtins.py \
  tests/core/effects/test_fill.py \
  tests/api/test_operation_argument_validation.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/stubs/test_api_stub_sync.py

/opt/anaconda3/envs/gl5/bin/mypy \
  src/grafix/core/effects/fill.py \
  src/grafix/core/builtins.py

git diff --check
git status --porcelain
```

stub の正規生成コマンドは、実装開始時の `grafix stub --help` と既存
`tests/stubs/test_api_stub_sync.py` を確認して exact path/config を確定する。生成物を手編集しない。

## 13. リスクと対策

### 13.1 「線間隔」の意味を広く読み過ぎる

リスク: 全線分間の最短距離や ink clearance と誤解される。

対策: scanline level の垂直中心線ピッチに契約を限定し、family/group/boundary/transform の
非保証を docstring と test 名に明示する。

### 13.2 gradient の base だけ clamp して局所違反を残す

リスク: 勾配の密側で `min_spacing` 未満になる。

対策: `-4, 0, +4` を直接 test し、各反復 step を clamp する。

### 13.3 floor により phase まで不必要に変える

リスク: ハッチ位置が大きく移動し、境界付近の見た目が余計に変わる。

対策: 現行 `base_spacing / 2` の start phase を維持し、2 本目以降の step だけを制約する。

### 13.4 `min_spacing=0` の隠れた回帰

リスク: arithmetic order、dtype、offset順の差で既存作品の checksum が変わる。

対策: omitted / explicit 0 / test-local legacy reference を array-exact に比較する。

### 13.5 ABI を上げず旧 cache と混同する

リスク: schema は変わっても builtin evaluator fingerprint が旧実装と同一になる。

対策: fill の manifest ABI だけを 2 に上げ、targeted test で固定する。

### 13.6 GUI range を物理保証と誤認する

リスク: `ui_max=10.0` が runtime clamp や mm 固定契約に見える。

対策: ParamMeta の既存契約どおり UI 初期範囲に限定し、runtime validation と scene-unit
docstring を別に置く。`unit="mm"` は付けない。

### 13.7 preset へ未較正値を波及させる

リスク: 共有 `P.grn_a5_frame` を使う多数作品の見た目と checksum が一括で変わる。

対策: core API の default を 0.0 とし、本タスクでは preset を変更しない。実機較正後に
作品側 opt-in を別タスクとして扱う。

## 14. 完了条件

- [ ] `E.fill(min_spacing=...)` が公開 API、metadata、docstring、両 stub に現れる。
- [ ] default は 0.0 で、旧出力と array-exact に一致する。
- [ ] uniform と gradient 正負で family 内 distinct scanline pitch が下限以上になる。
- [ ] planar-global / nonplanar-local、hole、cross-hatch、boundary の既存意味を維持する。
- [ ] invalid value を適切な境界で拒否する。
- [ ] fill ABI だけが更新され、cache identity が安全に失効する。
- [ ] exporter、Geometry provenance、preset に不要な複雑性を追加しない。
- [ ] focused pytest、ruff、mypy、diff-check が成功する。
- [ ] 高 density text acceptance で線数削減と floor 遵守を確認する。
- [ ] 実施結果と未実施の broad/full test を本計画に明記する。

## 15. 承認ゲート

この計画への承認後にのみ production code、test、generated stub を変更する。
承認前は本計画ファイル以外を編集しない。
