# `E.fill` サイズ非依存 density / ハッチ間隔 実装計画（2026-08-10）

- 状態: **全面見直し済み・承認待ち・未実装**
- 見直し時 branch: `main`
- 見直し時 HEAD: `f6b52b7`
- 対象: `E.fill` の `density` とハッチ間隔の意味
- 旧計画: 角度ごとの本数補正案は撤回し、本計画で置き換える
- 優先順位: **uniform時の実ピッチ / 常時のnominal pitchの一貫性 > 本数一致 > 既存値の見た目維持**

## 0. 結論

現行の「入力 geometry の基準高さを `round(density)` で割る」定義を廃止し、
`density` を round / clamp した有効値 `N` を、
**100 scene units あたりの nominal scanline 数**として再定義する。

```text
DENSITY_REFERENCE_LENGTH = 100.0  # scene units
N(density) = clamp(round(density), 2, 1000)  # density > 0
nominal_spacing S(density) = 100.0 / N(density)
```

- `density == 0` は従来どおり hatch を生成しない。
- `density=35` なら、geometry の大小に関係なく nominal pitch は約 `2.857 scene units`。
- 標準的な 2D plot で scene unit が mm の場合、`density=35` は 100 mm あたり約35本を意味する。
- `spacing_gradient=0` かつ floor 非発動なら、すべての planar scope / group / angle family で
  隣接 scanline の垂直ピッチを `S(density)` にする。
- 大きい geometry は同じ間隔でより広い範囲を覆うため、小さい geometry より多くの線を生成する。
- angle によって法線方向の投影幅が変わるため、本数は angle ごとに変わる。これは固定ピッチの
  必然であり、spacing を angle ごとに変えて本数を揃えない。

`100.0` を固定基準にする理由は、既存 default `35`、UI範囲 `0..1000`、作品内の数値感を保ち、
`spacing=1/density` への直接変更で既存 `density=35..1000` が最大100倍過密になるのを避けるためである。
canvas size や入力 bbox は固定基準に使わない。

### 承認時に確認する3つの trade-off

1. angle別の本数を揃えず、uniformな垂直pitchを優先する。
2. 固定基準は100 scene units、有効本数上限は従来どおり1000とする。
   したがって表現できる最小nominal pitchは `0.1 scene units`である。
3. 巨大入力のallocation前resource guardは、共通budget / packing設計に及ぶため
   本計画に混ぜず、必要性を記録した別計画とする。

## 1. 見直し理由

### 1.1 現行の基準高さ

現行 `src/grafix/core/effects/fill.py` は次のように spacing を作る。

```text
N = clamp(round(density), 2, 1000)
spacing = reference_height / N
```

`reference_height` は次で決まる。

- planar-global: 同じ `fill()` 入力に含まれる全 coords の、未回転 local Y bbox 高さ
- nonplanar-local: 各 planar polyline / face の、未回転 local Y bbox 高さ
- local X/Y 軸: `PlanarFrame` が選んだ面内基底

したがって現行は次の状態である。

- 高さ10と高さ100を別々に fill すると、同じ `density` でも pitch は10倍違う。
- 同じ geometry でも別 call、同一 packed input、nonplanar local path のどこを通るかで基準 scope が違う。
- 遠くの大きな ring を同じ planar input に追加すると、既存の小 ring の pitch まで変わり得る。
- angle を変えても pitch 自体は同じだが、投影幅が変わるので本数が変わる。

これは「同じ `density` なら大きい geometry と小さい geometry で概ね同じ線間隔」という希望と
一致しない。

### 1.2 旧角度本数補正案を撤回する理由

旧計画は、境界 edge の投影変動量 `V(theta)` を使って次の補正を提案していた。

```text
spacing(theta) = base_spacing * V(theta) / V(0)
```

これは angle ごとの線分数を近づける代わりに、同じ `density` でも angle / shape によって pitch を
変える。30 x 5 rectangle では方向により pitch が最大約6倍変わり得る。

固定ピッチ `S` なら、convex shape の scanline 数は概ね次になる。

```text
line_count(theta) ~= projected_normal_span(theta) / S
```

したがって、一般の非円形 geometry について「angle 間で同じ本数」と「angle 間で同じ pitch」は
同時には満たせない。本計画では最新要望を優先して pitch を固定し、旧 variation 補正、補正係数、
edge buffer、angle fast path をすべて削除する。

## 2. 公開契約

### 2.1 `density`

正の `density` を丸めてclampした `N(density)` を、
「100 scene units あたりの nominal scanline 数」とする。

```python
if density == 0.0:
    # hatchなし
else:
    n = min(max(round(density), 2), 1000)
    nominal_spacing = 100.0 / n
```

契約上の注意:

- 既存どおり整数へ丸め、2〜1000へ clamp する。したがって `0<density<2`や
  fractional値は、入力数値そのものと本数が一致しない。この量子化をmetadataに明記する。
- `density` は有限な0以上に限定し、`NaN` / `inf` / 負値は `ValueError` とする。
- `density=0` は特別値であり、正の `min_spacing` があっても hatch を生成しない。
- `angle_sets` ごとに `density` を分配しない。各 family が同じ nominal spacing を使う。
- boundary polyline は density / 本数に含めない。

### 2.2 実際のピッチ

`spacing_gradient=0` のとき、隣接 scanline の実 step は次になる。

```text
actual_step = max(S(density), min_spacing)
```

`spacing_gradient!=0` では、現行の正規化係数と位置係数を維持する。

```text
step(t) = max(S(density) * normalized_gradient_factor(t), min_spacing)
```

この場合も geometry の bbox 高さは `t=(y-min_y)/(max_y-min_y)` の正規化にだけ使い、
nominal spacing の算出には使わない。同じ normalized position では、大小 geometry が同じ係数を使う。

### 2.3 保証する範囲

- 同じ `density`、同じ gradient / floor 条件なら、別 `fill()` call 間でも nominal spacing は同じ。
- 同一 planar input の大小 group 間でも nominal spacing は同じ。
- nonplanar-local path の異なる face 間でも nominal spacing は同じ。
- `spacing_gradient=0`かつ同じfloor条件なら、angle が違っても垂直 pitch は同じ。
- gradient使用時はnominal `S`と係数式は共通だが、回転後spanとsampleされる `t` が
  angleごとに異なり得るため、actual stepのangle間一致は保証しない。
- uniformly scaled copy を **fill 前**に作った場合、pitch は変わらず、本数が概ね scale 倍になる。
- fill 後に scale / nonuniform transform / warp / 3D projection を適用した場合、最終出力 pitch は変わり得る。
- span が1 pitch未満の領域は、現行 midpoint fallback により1 levelだけ生成し得る。この場合は
  隣接線がないため pitch を測定できない。
- 別 region / call 間の lattice phase や最寄り線同士の距離は保証しない。

### 2.4 本数について

本数を同一にする契約は設けない。

- convex region: distinct level 数は `projected span / actual step` の端数差程度。
- hole / concavity: 同じ level が複数segmentへ分割されるため、raw polyline 数はさらに増える。
- multiple groups: group ごとの独立 phase と midpoint fallback により端数が累積し得る。
- angle sweep では投影幅に応じて本数が変わることを、むしろ回帰テストで固定する。

これにより、面積当たり総ハッチ長 `area / pitch` と見かけの線密度は angle 間で概ね安定する。

## 3. 実装設計

### 3.1 geometry 非依存の spacing 変換

`_spacing_from_height(height, density)` を削除し、小さな pure helper へ置き換える。

```python
_DENSITY_REFERENCE_LENGTH = 100.0
_MAX_NOMINAL_LINES_PER_REFERENCE = 1000
_MIN_NOMINAL_LINES_PER_REFERENCE = 2


def _spacing_from_density(density: float) -> float:
    line_count = int(round(float(density)))
    line_count = min(
        max(line_count, _MIN_NOMINAL_LINES_PER_REFERENCE),
        _MAX_NOMINAL_LINES_PER_REFERENCE,
    )
    return _DENSITY_REFERENCE_LENGTH / float(line_count)
```

命名は「最大出力本数」や「density入力自体の上限」と誤読しないよう、
`MAX_FILL_LINES` から「固定基準長あたりの有効nominal本数上限」を表す名前へ変更する。

### 3.2 一度だけ計算して全scopeへ渡す

正の `density` の `base_spacing` は `fill()` 呼び出しごとに一度だけ計算する。

- planar-global: 全 group / angle family へ同じ値を渡す。
- nonplanar-local: 全 valid face / angle family へ同じ値を渡す。
- private generator の `density` と optional `spacing_override` の二重経路をなくし、必須の
  `base_spacing` 一つに整理する。
- work座標の `min_y/max_y` は scan範囲と gradient正規化にだけ使う。
- global / local の `ref_height` 算出と、それに基づく分岐を削除する。ただし既存の面積退化判定、
  planarity判定、work span判定は維持する。

scanline intersection kernel、even-odd pairing、回転、packing、境界順序は変更しない。

### 3.3 angle補正を追加しない

以下は実装しない。

- edge variation `V(theta)`
- rotated bbox spanからの direction別 spacing
- angleごとの thinning / densification
- pilot fill -> count -> 再生成
- 生成後の線間引き
- 補正強度 `alpha` や mode parameter

これにより追加計算は `O(1)`。旧計画の `O(edge_count * angle_sets)` overhead も発生しない。

## 4. 巨大入力とresource risk

固定絶対pitchでは、大きい geometry が多くの線を生成すること自体は仕様である。ただし
有効nominal本数上限1000は実scanline数を制限しない。高さ1,000,000、有効本数1000なら
約10,000,000 candidate levelsになり得る。

現行fillは `y_values`、endpoint、2D chunks、float64 local/world temporaryの全peak memoryを
allocation前に共通budgetと照合していない。これを正しく解決するには、次をまとめて設計する
必要がある。

- candidate-level数だけでなく `sum_group(levels_group * edges_group)` で予測するwork budget
- hole / concavityの実segment数とmultiple-group累積出力
- 保持中chunks、Y/edge/scratch配列、packing時のfloat64 temporaryを含むpeak bytes
- Numba count/writeの分離とPython境界のoverhead

これらは固定spacing変換より広いresource / packing変更であり、hot pathの5%受入判定も
切り分ける必要がある。よって本計画ではNumba / NumPy kernelと共通budgetを変更せず、
このresource guardを専用の新規計画へ分離する。

本計画では次だけを守る。

- spacingを暗黙に広げる本数capを追加しない。
- 既存の事後resource budget契約を弱めない。
- density変換と同じ実出力量のmatched fixtureでhot-path性能を独立に測る。
- maintained fixture / presetが過大化する場合は、実装をマージする前にdensity値の見直しまたは
  resource guard計画の先行を判断する。

## 5. `min_spacing` default の既知不整合

現HEADのruntime signatureは `0.05`、完了済みplan・docstring・catalog testは `0.0` で不一致である。
`min_spacing` を絶対densityの安全capとして流用しない。

どちらを正とするかはこのdensity変更からは決めない。実装時は次を守る。

- density関連testは `min_spacing=0.0` または意図した正値を必ず明示する。
- 本計画のdiffでruntime default、docs、catalog testのいずれも書き換えない。
- 現HEAD `0.05` を維持するか、完了済みplan / docs / testの `0.0` へ戻すかは、
  別のユーザ確認・別差分で同期する。

## 6. テスト計画

### 6.1 helper / 数値契約

- [ ] `density=25 -> S=4.0`、`35 -> 100/35`、`1000 -> 0.1` をliteral期待値で固定する。
- [ ] `2未満の正値 -> N=2`、fractional densityのround境界、`1000超 -> N=1000` を固定する。
- [ ] `density=0` はhatch無し、負値 / `NaN` / `inf` は `ValueError`。
- [ ] direct evaluatorのempty inputも含め、入力geometryのearly returnより前に同じ有限値validationを行う。

### 6.2 最重要: uniform-scale copies

代表条件は `density=25`, `S=4`, `min_spacing=0`, `gradient=0`, boundary除去とする。

- [ ] 80 x 40 rectangle と、その4倍copyを別々の `fill()` callで処理する。
- [ ] angle `0°`, `37°`, `90°` のすべてで両者のdistinct level pitchが `4.0`。
- [ ] 4倍copyのlevel数は同じangleの小copyのおおむね4倍で、各 projected span / S の±1以内。
- [ ] copyへ異なるtranslationを与えてもpitchが変わらない。
- [ ] 1x / 2x / 4x disjoint ringsを一つのcoplanar packed inputへ入れても全groupのpitchが同じ。
- [ ] 同じringを別fillした場合と同一fillへまとめた場合でpitchが一致する。
- [ ] 遠くの大ringを追加しても、既存小ringのpitchが変わらない。
- [ ] span < S の小ringはmidpoint 1 level fallbackを維持する。

現行実装は別callで `spacing=height/N`、同一callでglobal height依存になるため、
このsuiteのliteral `S=4`、separate-vs-combined、remote-group不変の中核assertionはREDになる。
同一call内の共通pitchやangleに対する固定pitchなど、現行でGREENな部分は回帰guardとして残す。

### 6.3 angle / family

- [ ] squareと4:1 rectangleを `0,15,30,45,60,75,90°` で評価し、全角度でpitch=S。
- [ ] convex fixtureのlevel数が `projected_normal_span(theta)/S` の±1以内。
- [ ] `W=40S, H=10S` rectangleでは、0°と90°が概ね10対40本になり、`N90 >= 3*N0`。
- [ ] 十分大きいsquareの45°にも `abs(level_count - projected_span/S) <= 1` を適用する。
- [ ] 上記をGREEN regression guardとし、angle-dependent spacingで本数を揃える旧案を検出する。
- [ ] `angle_sets=2/3` は方向分類後の各familyでpitch=S。総本数一定は要求しない。

### 6.4 hole / concavity / multiple groups

- [ ] midpointをfamily法線へ投影し、hole分割された同一levelをdedupeしてpitchを測る。
- [ ] square-with-holeのraw segment数ではなくdistinct levelsがSピッチ。
- [ ] hole内部へ線を生成しない既存testを維持する。
- [ ] concave U、大小outer、holeを含むinputで全groupのpitchがS。
- [ ] remote group追加前後で既存groupのpitchが変わらない。
- [ ] boundaryを本数 / pitch測定から除外する。

既存 `_level_tolerance(..., min_spacing)` は `min_spacing=0` を受けられないため、引数を
`reference_spacing` へ一般化するか、本数dedupe専用helperを追加する。toleranceはfloat32座標scaleと
nominal spacingから `max(16 * eps32 * coordinate_scale, 2e-5 * S)` を目安に決め、
`reference_spacing * 0.1` 未満も先にassertする。projected spanはworldの公称W/Hではなく、
実work coordsまたはfamily法線への投影から求める。

### 6.5 gradient / floor

- [ ] gradient `-4,0,4` で、各stepを既存の解析式と比較する。
- [ ] `_generate_y_values` のunit referenceで、各geometryが実際にsampleした `t_i` ごとに
      `delta_i ~= max(S*c*exp(k*(t_i-0.5)), min_spacing, S*1e-3)` と適合することを検証する。
- [ ] 1x / 4xのlevelが同じ `t` をsampleすること自体は要求しない。
- [ ] `min_spacing<S` のuniform出力は `min_spacing=0` とarray-exact。
- [ ] `min_spacing>S` では大小・複数angleともpitchがfloor値になる。
- [ ] gradient使用時もすべてのstepがfloor以上。
- [ ] `density=0 + positive floor` でもhatch無し。

### 6.6 3D / effect順

- [ ] oblique coplanar plane上の1x / 4x copyをlocal frameへ戻してpitch=S、local z≈0。
- [ ] 全体nonplanarだがfaceごとはplanarなinputでlocal pathを強制し、異なるface sizeでもpitch=S。
- [ ] fill前にgeometryを4倍scaleした場合はpitch=S、本数が増える。
- [ ] fill後に4倍scaleした場合は最終pitchが4SになることをNotesと回帰testで固定する。

### 6.7 既存回帰 / ABI

- [ ] 100-unit高さ、angle0、gradient0、floor0のmatched fixtureは旧/newでcoords・offsets array-exact。
- [ ] 現 `10x10,density10=>10本` testを、固定100-unit契約のpitch/count testへ置換する。
- [ ] boundary prefix、empty、degenerate、translation、rotation、determinismを弱めない。
- [ ] fill evaluator ABIだけを `2 -> 3`、他effect ABIは1のまま。
- [ ] catalog declaration `grafix-builtin-effect-3`、metadata、generated stubsを同期する。

## 7. performance計画

### 7.1 事前試算

固定100のprototype測定では、同じ既存density値を使った場合に次の変化があった。

| case | 現行 | 固定100 | 解釈 |
| --- | ---: | ---: | --- |
| `rings_2`, H=300, d=35 | 49 lines / 1.28 ms | 147 lines / 2.01 ms | 必要line 3倍 |
| `dense.rings_2`, H=300, d=1000, 3方向 | 4,200 / 33.84 ms | 12,600 / 98.05 ms | 必要line 3倍 |
| `many_rings`, H=90, d=20 | 512 / 27.18 ms | 512 / 26.44 ms | 実質同等 |

入力scope高さ `H` に対する旧/new scanline倍率は概ね `H/100`。A5高さ210は約2.1倍、A4高さ297は
約2.97倍になる一方、100未満の小形状は疎になる。これは固定絶対pitchの意味変更であり、
出力増に比例する時間増をalgorithm regressionとは扱わない。

`spacing=1/density` 案はH=300,d=1000の代表caseで約300倍のlineと約289倍の時間になったため不採用。

### 7.2 benchmark cases

既存:

- `effect.fill.rings_2`
- `effect.fill.dense.rings_2`
- `effect.fill.many_rings`

追加:

- 高さ100、angle0、gradient0、floor0のmatched-output control
- edge数一定の1x / 2x / 4x scale sweep
- angle `0/37/90°` のwide rectangle

### 7.3 記録値

- warm median / p95 / MAD、30 samples、GC無効、seed 0
- process-cold / compile-coldの代表case
- `n_lines`, `n_vertices`, `output_bytes`, checksum
- test-local measurementでgroupごのcandidate levels、hatch segments、edge count、
  `sum_group(levels_group * edges_group)`
- peak RSSは観測値
- normalized costは `ns / sum_group(levels_group * edges_group)` とし、formal benchmark schemaや
  production provenanceは増やさず、固定measurementから事後算出する

### 7.4 受入基準

matched-output control:

- coords / offsets / checksum一致
- warm median `<=1.05x`
- p95 `1.10x`超過は観測・再測定triggerとし、30 samplesの単一値をhard gateにしない
- Python固定費の揺らぎを減らすため、高さ100でedge数が十分多いfixtureを使う

scale sweep:

- level数 / outputはscaleにほぼ比例
- wall time / RSSは生成量で説明でき、超線形な増加がない
- 上のnormalized costは1x比 `1.15x`以内を目安とし、hard gateはmatched controlに限定

changed-output既存case:

- checksum差は正常
- absolute time差を出力line数と分離して記録
- 旧案の「worst angle absolute time非増加」は固定pitchと矛盾するため削除

benchmark rootは共通directoryを使い、比較対象を `runs/<run-id>.json` とする。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m grafix benchmark run \
  --case effect.fill.many_rings \
  --profile long --mode warm --samples 30 --disable-gc --seed 0 \
  --run-id fill-fixed-density-before \
  --out /tmp/grafix-fill-fixed-density-bench

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m grafix benchmark compare \
  /tmp/grafix-fill-fixed-density-bench/runs/fill-fixed-density-before.json \
  /tmp/grafix-fill-fixed-density-bench/runs/fill-fixed-density-after.json \
  --output /tmp/grafix-fill-fixed-density-bench/compare.json
```

## 8. 既存作品・保存値の移行

固定100方式は既存値をそのまま受け取れるが、見た目は変わる。旧pitchを特定scopeで保つ変換は次。

```text
old_N = clamp(round(old_density), 2, 1000)
new_density ~= 100 * old_N / old_reference_height
```

- 旧 `reference_height=100` のscopeは数値変更不要。
- 動的scaleや複数groupでは一つの旧値を完全変換できない。これは旧scope依存を廃止する結果である。
- 変換結果が1000を超える場合はclampされるため、旧0.1未満のpitchを再現できない。
  現存例の `0.015〜0.075` 程度は明示的な非互換とし、最小0.1を承認項目にする。
- runtime compatibility mode、legacy flag、shim、自動bbox換算は追加しない。
- 保存済みGUI / ParamStore値を無条件に一括書換えしない。入力高さが保存されておらず安全に変換できないため。
- `sketch/agent_loop` 外のParamStoreだけでも48 files / 218 density statesがあり、そのうち77件が1000以上である。
  既存store再読込時の見た目変化をmigration noteに明記する。

実装時のaudit対象:

- READMEの公開例
- `sketch/presets/`
- `sketch/readme/`
- benchmark fixtures
- tests

代表renderはparameter persistenceを無効にしてbefore / after比較し、線が消える小glyphや
過密になるA4/A5全域だけ、新しい絶対pitchの意図で
値を調整する。`sketch/work/`、archive、historical benchmark JSON、進行中の他作業は変更しない。
移行式とeffect順は公開Notesまたは専用migration noteへ記録する。

## 9. 変更予定ファイル

### production

- [ ] `src/grafix/core/effects/fill.py`
- [ ] `src/grafix/core/builtins.py`

### generated API artifacts

- [ ] `src/grafix/api/__init__.pyi`
- [ ] `typings/grafix/api/__init__.pyi`

### tests / benchmark

- [ ] `tests/core/effects/test_fill.py`
- [ ] `tests/core/test_builtin_catalog_bootstrap.py`
- [ ] `src/grafix/devtools/benchmarks/effect_benchmark.py`
- [ ] `tests/devtools/benchmarks/test_effect_benchmark.py`

### docs / maintained examples

- [x] `docs/plan/fill_size_independent_density_spacing_implementation_plan_2026-08-10.md`
- [ ] `README.md`（公開例のvisual auditで必要な場合）
- [ ] `sketch/presets/` と `sketch/readme/` の対象限定ファイル
- [ ] migration note（既存日付文書へ無関係な内容を混ぜず、必要なら専用file）

## 10. 実装フェーズ

### Phase 0: baseline / scope固定

- [ ] 実装開始時のbranch、HEAD、`git status --porcelain`を記録する。
- [ ] 並列作業の依頼外差分を列挙し、restore / delete / stageしない。
- [ ] 現行pitch / counts / checksum / benchmark JSONを `/tmp` へ保存する。
- [ ] matched-height100 fixtureとscale sweep benchmarkをproduction変更前に追加してbaselineを取る。
- [ ] `min_spacing` default不整合は既知failureとして分離し、本diffで変更しない。
- [ ] 最小nominal pitch 0.1の非互換とresource guard分離を承認範囲として再確認する。

### Phase 1: RED / GREEN baseline matrix

- [ ] density conversion helperの解析値testを追加する。
- [ ] 真にREDになるliteral `S(density)`、別callの1x/4x、separate-vs-combined、
      remote group追加、異なるサイズの3D local facesを分けて記録する。
- [ ] 現行でもGREENなangle本数比、hole dedupe、inactive floor、density=0、effect後scale、
      height=100 matched fixtureをcharacterization / 回帰guardとして記録する。
- [ ] gradient / floor / 3D local pathのsize-independent pitch testを追加する。
- [ ] 新規testをnode idで実行し、既知failureと分離する。

### Phase 2: 最小spacing実装

- [ ] fixed reference定数と `_spacing_from_density()` を追加する。
- [ ] `fill()` callごとにbase spacingを一度だけ計算する。
- [ ] global / local ref-height依存を削除する。
- [ ] private generatorをrequired `base_spacing` 一本へ整理する。
- [ ] variation / angle補正を一切追加していないことをdiffで確認する。

### Phase 3: metadata / ABI / generated stub

- [ ] density metadata / docstringを「100 scene unitsあたり」に更新する。
- [ ] angleによる本数変動、gradient、effect順、floorをNotesへ記載する。
- [ ] fill evaluator ABIを3へ更新する。
- [ ] canonical generatorで両stubを再生成し、手編集しない。
- [ ] migration説明を追加する。

### Phase 4: focused validation

- [ ] fill / catalog / benchmark catalog / stub sync testsを実行する。
- [ ] 対象限定ruff / mypy / `git diff --check`を実行する。
- [ ] 新規testだけでなく既存fill testを弱めていないことを確認する。

### Phase 5: benchmark / visual smoke

- [ ] matched controlのmedian 5% hard gateとp95観測値を確認する。
- [ ] scale sweep、既存3case、process/compile cold、RSSを記録する。
- [ ] persistence無効でactive preset / readmeの代表だけrender smokeする。
- [ ] 小shapeの疎化と大canvasの出力増を仕様差として一覧化する。
- [ ] threshold超過時はbase-spacing算出重複、group loop、Python固定費をprofileする。

### Phase 6: broader validation

- [ ] ユーザー承認後にbroader pytestを実行する。
- [ ] 全変更が依頼範囲内で、依頼外差分へ触れていないことを確認する。
- [ ] 完了checkbox、実測値、未完了事項を本計画へ追記する。

## 11. 非ゴール

- angle間の線分数を同一にすること
- geometryごとにdensity本を割り当てること
- direct `spacing` parameter、`density_mode`、補正強度parameterを追加すること
- fractional densityのroundを廃止すること
- gradientを絶対Y座標へ固定すること
- group / callをまたぐ共通lattice phaseを導入すること
- 旧挙動のcompatibility mode / wrapper / shimを残すこと
- resource超過時にspacingを暗黙調整すること
- allocation前resource / work budgetを本計画のhot pathへ追加すること
- exporter / Layer / RealizedGeometryへfill provenanceを追加すること
- historical work / archive / benchmark JSONを一括更新すること

## 12. 完了条件

次をすべて満たした時だけ完了とする。

1. `density` からの nominal spacing がgeometry bbox / size / angle / group / callに依存しない。
2. 1x / 4x copyでpitchが同じ、大copyの本数がおおむね4倍になる。
3. angle sweepでpitchが同じ、本数がprojected spanに応じて変わる。
4. hole、concavity、multiple groups、3D local pathでも同じ契約を満たす。
5. gradient / `min_spacing` / `density=0` の既存責務を維持する。
6. matched-output controlがarray-exactかつmedian `<=1.05x`。p95は観測・再測定triggerとする。
7. fill ABI、metadata、docstring、generated stubs、migration説明が同期する。
8. 代表visual smokeと既知の薄い/過密caseが記録され、必要な調整だけ別途判断できる。
9. 計画checkboxと実測値が更新され、未完了事項が明示される。

---

この見直しにより、`density` は「各geometryに何本入れるか」ではなく、scene上の共通物差しで
「どれくらいの間隔で線を置くか」を表す。大きいgeometryほど線が増えることを正しい挙動とし、
angle別の本数一致より、作品全体で一貫した物理ピッチと見かけ密度を優先する。
