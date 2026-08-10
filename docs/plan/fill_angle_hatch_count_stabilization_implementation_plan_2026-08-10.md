# `E.fill` 角度別ハッチ本数安定化 実装計画（2026-08-10）

- 状態: **承認待ち・未実装**
- 計画作成時 branch: `main`
- 計画作成時 HEAD: `4de01ce`
- 対象: `E.fill` の `angle` / `angle_sets` によるハッチ線分本数の変動
- 方針: 0°時の現行本数を基準に、境界 edge の投影変動量で方向別 spacing を補正する

## 0. 要約

現行 `E.fill` は、未回転の基準高さから一つの spacing を作り、その spacing を全角度へ
そのまま使う。このため、同じ `density` でも角度によって走査幅と境界交差回数が変わり、
生成される 2 点ハッチ線分数が大きく変わる。

本計画では、各 hatch angle `theta` に対して境界 edge の走査法線方向への投影変動量
`V(theta)` を一度だけ計算し、0°時の現行 spacing `s0` を次のように補正する。

```text
V(theta) = 0.5 * sum_edges(abs(-sin(theta) * dx + cos(theta) * dy))
spacing(theta) = s0 * V(theta) / V(0)
```

一般位置の scanline では、境界交点数の半分が生成ハッチ線分数になる。上式の
`V(theta)` は、その「1 scan level あたりの線分分割」まで含めた積分量に相当するため、
単なる回転後 bbox 高さより、穴・凹形状・複数 outer group を含む実出力本数へ近い補正になる。

高価な scanline intersection / sort を試し打ちしない。edge 差分を一度作り、各 family で
軽い reduction を一回行うだけに限定する。追加計算量は `O(angle_sets * edge_count)`、
追加メモリは `O(edge_count)` とし、scanline hot path 自体は変更しない。

## 1. 作業ツリー境界

計画作成開始時点で次の依頼外 tracked 差分が存在する。

- `sketch/work/260810_measurement_poster.py`

計画作成中にも並列作業による次の依頼外差分が追加された。

- `.agents/skills/grafix-art-loop/agents/openai.yaml`
- `.agents/skills/grafix-reference-reproduction/`
- `docs/plan/grafix_reference_reproduction_skill_plan_2026-08-10.md`
- `sketch/agent_art/`

これらは本計画の対象ではない。実装時も restore、移動、整形、stage を行わず、以下で列挙する
`fill`、test、benchmark、生成 stub、計画書だけを変更する。実装開始時に
`git status --porcelain` と HEAD を再記録し、並列作業で増えた差分も同様に触らない。

## 2. 背景と現行挙動

### 2.1 現行 `density` の意味

`src/grafix/core/effects/fill.py` は概ね次の計算を行う。

```python
target = clamp(round(density), 2, MAX_FILL_LINES)
base_spacing = reference_height / target
```

planar-global 経路では `reference_height` は入力全体の未回転 local Y extent、
nonplanar-local 経路では各 planar polyline の未回転 local Y extent である。
`density` は実線分数そのものではなく、0°の基準 spacing を作るスケールである。

### 2.2 角度で本数が変わる理由

形状を `-angle` 回転した work 座標で水平 scanline を生成するため、実際の走査範囲は
ハッチ法線 `n=(-sin(theta), cos(theta))` への投影幅になる。一方、spacing は未回転高さ由来の
ままなので、投影幅と境界交差構造の変化がそのまま本数差になる。

現行実測例（`min_spacing=0.0`, `spacing_gradient=0.0`）:

| 入力 | density | 現行の角度別ハッチ線分数 |
| --- | ---: | ---: |
| 10 x 10 square | 10 | 10〜14 |
| 30 x 5 rectangle | 10 | 10〜61 |
| 10 x 10 outer + 4 x 4 hole | 10 | 14〜20 |

hole や凹形状では一つの scan level が複数の 2 点線分へ分かれる。そのため、distinct scanline
level 数だけを揃えても、renderer、plotter、packing、実行時間へ直接効く実線分数は揃わない。

### 2.3 性能上の制約

現行 hot path は、scanline ごとに全 edge との交点を数え、sort と even-odd pairing を行う
Numba 2-pass kernel である。本数を知るために一度 fill して再生成する方式は、この高価な処理を
ほぼ二重化するため採用しない。

また、planar-global の各 outer group へ `density` 本ずつ割り当てる方式は、小さな島、句読点、
Delaunay face が多い入力で出力本数を `group_count` 倍へ膨らませ得る。現行どおり、同じ family
では全 group が一つの共通 spacing を使う。

### 2.4 既知の `min_spacing` default 不整合

現 HEAD では runtime signature だけが `min_spacing=0.05` だが、承認・完了済みの
`docs/plan/fill_min_spacing_implementation_plan_2026-08-09.md`、`fill()` docstring、catalog test は
いずれも default `0.0` を契約としている。正値を既定にすることは同計画の非ゴールでもあるため、
test / docs を実装へ合わせて弱めない。

本計画では、実装開始時の前提修正として runtime signature を `0.0` へ戻し、既存の canonical
契約へ同期することを提案する。この一行は角度補正と別差分・別 baseline として確認する。
もし `0.05` が意図した新契約である場合は、本計画を実装する前にユーザー確認を行い、
`min_spacing` の変更を別計画へ分離する。角度別本数 test では、どちらの場合も責務を分離するため
`min_spacing=0.0` を必ず明示する。

## 3. ゴール

- 同一 planar scope、同一 `density`、単一 angle family の 2 点ハッチ線分数を、角度間で
  0°時の現行本数へ近づける。
- convex だけでなく、hole、凹形状、複数 outer group による線分分割も補正量へ反映する。
- planar-global では全 group 合計を一つの scope とし、family 内の共通 spacing を維持する。
- nonplanar-local では各 planar polyline / face を独立 scope として同じ補正を適用する。
- `angle=0`, `angle_sets=1` の代表入力は coords / offsets / 順序 / 位相を array-exact に維持する。
- `min_spacing` の hard floor、`density=0`、even-odd hole、boundary、3D plane 復元を維持する。
- scanline intersection を二度実行せず、生成後に線を捨てる後処理も追加しない。
- 角度やアスペクト比により scanline 数が大きく増える現状を抑え、angle sweep の
  worst-case 実行時間改善を測定目標とする。
- 生成意味の変更を evaluator ABI、metadata、docstring、生成 stub へ反映する。

## 4. 非ゴール

- すべての形状・角度で線分数を厳密に一致させること
- `density` を厳密な「本数」または scene 単位あたりの絶対密度へ変更すること
- boundary polyline をハッチ本数へ含めること
- `min_spacing` 発動中も本数一致を優先すること
- `spacing_gradient` 使用時の位置依存密度を厳密積分すること
- angle family 間で `density` を分配し、総本数を一定にすること
- group ごとに `density` 本を割り当てること、または group ごとの spacing を別々にすること
- group 間へ共通 lattice phase を導入すること
- 面積当たり総線長、インク量、PNG の濃さを角度間で一定にすること
- exporter、Layer、RealizedGeometry に fill provenance を追加すること
- 後処理による thinning、ランダム間引き、pilot scan、反復収束を追加すること
- 旧挙動を残す mode、compatibility wrapper、shim、typo alias を追加すること
- 既存作品や preset の parameter を一括調整すること

## 5. 本数の定義と公開契約

### 5.1 第一指標: 出力ハッチ線分数

本計画で「本数」と呼ぶ第一指標は、ある planar scope / angle family が clipping 後に出力する
**2 頂点 hatch polyline の数**とする。

- `remove_boundary=False` の入力境界は数えない。
- hole や凹部で同じ scan level が二つへ分割された場合は 2 本と数える。
- planar-global の複数 outer group は family ごとに合計する。
- `angle_sets>1` は線分方向で family を分類し、family ごとに数える。
- nonplanar-local は plane / face ごとに数える。

これは packed output サイズと scanline pairing の実仕事に最も近い指標である。

### 5.2 第二指標: distinct scanline level

隣接ピッチ、`min_spacing`、convex region の基礎挙動には、線分 midpoint を hatch 法線へ射影し、
float32 tolerance 内の重複をまとめた distinct scanline level 数を使う。hole で分割された同一
level を重複カウントしない。

### 5.3 best-effort 契約

新しい `density` の説明は次の意味に更新する。

> 0°時の基準高さから nominal spacing を決め、他の hatch angle では境界の投影変動量を用いて、
> clipping 後のハッチ線分数が 0°時から大きく変わらないよう spacing を補正する密度スケール。

本数は scan phase、float32 丸め、頂点との一致、微小線分除外、`spacing_gradient`、
`min_spacing` によりずれ得る。単一または少数 group では通常数本だが、multiple group では
各 group の独立 phase と midpoint fallback による離散誤差が group 数に応じて累積し得る。
厳密保証ではなく best-effort と明記する。

## 6. 採用アルゴリズム

### 6.1 境界 edge の投影変動量

各 valid ring を暗黙に閉じ、各 edge 差分 `(dx, dy)` を一度だけ作る。hatch angle `theta` の
work-Y、すなわち法線 `(-sin(theta), cos(theta))` への edge 変動量を次で集約する。

```python
variation = 0.5 * sum(abs(-sin_theta * dx + cos_theta * dy))
```

一般位置の水平線に対し、境界交点数の半分が even-odd で生成される線分数になる。
scan coordinate に沿ってその本数を積分すると上の `variation` になるため、一様 spacing `s` の
実線分数は概ね `variation / s` で見積もれる。

全 ring の edge を加算することで、outer だけでなく hole、凹形状の往復、複数 group の分割も
一次近似へ含める。ring の向き、開始頂点、明示 close の有無には依存しない。

### 6.2 0°を基準とする spacing 補正

現行の未回転参照高さから `base_spacing` を従来どおり作る。

```python
base_spacing = _spacing_from_height(reference_height, density)
reference_variation = variation(0.0)
family_spacing = base_spacing * variation(angle_i) / reference_variation
```

これにより予測線分数は次のように 0°時へ揃う。

```text
variation(angle_i) / family_spacing
    = reference_variation / base_spacing
```

`angle=0`, `angle_sets=1` は edge buffer 自体を作らない fast path とし、現行 spacing と出力を
そのまま使う。複数 family に 0°が含まれる場合も、その family は `base_spacing` を直接使い、
比率計算による微小な phase 変化を避ける。

### 6.3 planar-global

1. `_build_evenodd_groups()` が採用した全 ring から edge 差分を一度作る。
2. `V(0)` と `angle_sets` 個の `V(angle_i)` / spacing を group loop の外で一度だけ計算する。
3. 同じ family spacing を全 outer group の既存
   `_generate_line_fill_evenodd_multi(..., spacing_override=...)` へ渡す。
4. group 単位の even-odd clipping、独立 phase、boundary packing 順は変更しない。

これにより、小 group へ個別に `density` 本を割り当てず、現行の共通 spacing と相対サイズ感を
維持したまま、family 全体の合計本数を揃える。

### 6.4 nonplanar-local

全体が非平面で各 polyline を別 plane として処理する経路では、各 valid planar polyline の
local 2D edge から `V(0)` と family spacing を作る。共通作業平面が存在しないため、現行どおり
face ごとの独立 scope とする。

### 6.5 `min_spacing` と `spacing_gradient`

- 補正済み `family_spacing` を既存 `_generate_y_values()` の base spacing として渡す。
- `min_spacing` はその後で各 step の hard floor として適用し、本数安定化より常に優先する。
- `spacing_gradient=0` を第一契約とする。
- gradient 使用時も同じ variation 比を用いるが、位置依存 weight を積分しない best-effort とする。
- gradient 専用の weighted variation は、簡易式が受入基準を満たさない場合だけ別計画で検討する。

### 6.6 数値ガード

- `density<=0`、空、退化入力の既存 early return を維持する。
- `V(0)<=0`、非有限 variation、非正の補正 spacing では既存 `base_spacing` へフォールバックする。
- angle の正規化や trig の演算順を不必要に変えない。
- variation reduction は安定性のため float64 で行い、scanline 座標と packed output の dtype は
  現行どおり float32 とする。

過度な fallback 分岐や旧 helper の wrapper は追加しない。

## 7. パフォーマンス設計

### 7.1 追加仕事を hot path の外へ限定する

- edge 差分 buffer は planar scope ごとに一度だけ作る。
- `E x angle_sets` 行列は作らず、family ごとに一つの `(E,)` 投影 temporary を reduction 後に破棄する。
- variation と spacing は group loop の外で計算し、group ごとに繰り返さない。
- scanline intersection / sort / pairing kernelは変更しない。
- pilot fill、生成後 count、再生成、post-filter を行わない。
- 新しい Numba kernelや依存を追加せず、既存 NumPy 配列演算で完結させる。

### 7.2 角度による仕事量の上限

`spacing_gradient=0` とする。ある scope で独立処理される各 group の実 scan span の合計を
`D_sum(theta)` とすると、各 group の span はその境界 variation 以下なので、合計でも
`D_sum(theta) <= V(theta)` である。したがって補正後の候補 scan level 総数は、連続近似では
概ね次を満たす。

```text
D_sum(theta) / spacing(theta)
    <= V(0) / base_spacing
```

つまり、角度やアスペクト比だけで候補 scanline が現行 0°基準を大幅に超える現象を抑えられる。
従来少なかった角度では本数が増える場合があるが、これは本数を揃えるための実仕事であり、
予測上は現行 0°相当を基準とする。実際には group ごとの端数と最低 1 level fallback により
`O(group_count)` の加算誤差があり、gradient 使用時には上式を適用しない。

また、上式が抑えるのは scan level 総数であり、実行時間そのものではない。kernel の仕事は
概ね `sum(level_count[group] * edge_count[group])` なので、level が edge の多い group へ
再配分されると同じ総本数でも時間は変わり得る。worst-angle latency の改善は benchmark で
確認する測定目標であり、数式上の保証とはしない。

### 7.3 不採用案

#### 回転後 bbox span / density

convex region の distinct level は安価に揃うが、hole / concavity の分割線分数を扱えず、
multiple group で共通 spacing を維持しにくい。本数の第一指標に対して精度が不足する。

#### group ごとの補正

`V_group(theta) / V_group(0)` で補正すれば、各 group の現行 0°本数を保つこと自体はできる。
しかし family 内の共通 spacing を失い、今回の第一指標である planar-global の aggregate 本数より
group 個別の均一性を優先することになるため採用しない。なお、各 group へ単純に
`density` 本ずつ割り当てる別案は、小 glyph や多数 face の出力を増やすため同様に採用しない。

#### 長い方向だけ thinning する補正

既存より本数を増やさない利点はあるが、縦長形状などで少ない角度を残すため、角度間の本数差が
大きく残る。今回の主目的である本数安定化を十分満たさない。

#### pilot scan -> count -> 再生成

実本数へ最も直接的だが、最も高価な交点計算と sort を二回行うため採用しない。

## 8. テスト計画

### 8.1 variation helper 単体

- [ ] rectangle の `V(0)`, `V(45°)`, `V(90°)` を解析値と比較する。
- [ ] asymmetric triangle / concave ring で、`-angle` 回転後 work-Y edge 差分の総変動と一致する。
- [ ] outer + hole は各 ring variation の和になる。
- [ ] ring の開始点変更、向き反転、明示 close の有無で variation が変わらない。
- [ ] empty / degenerate scope の fallback 条件を固定する。
- [ ] trig / float64 reduction の比較は `rtol=1e-12`、
      `atol=eps64 * max(1.0, abs(expected)) * 16` を目安とし、bitwise 一致を要求しない。

### 8.2 単一 convex region

`density=17`, `min_spacing=0.0`, `spacing_gradient=0.0`, `remove_boundary=True` を明示し、
角度 `[0, 15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165]` を比較する。

- [ ] 10 x 10 square の 2 点ハッチ線分数が 0°本数から各角度で ±1 本以内。
- [ ] 40 x 10 wide rectangle が同じ基準を満たす。
- [ ] 10 x 40 tall rectangle が同じ基準を満たし、少ない角度だけを残す thinning-only 実装を防ぐ。
- [ ] asymmetric triangle が同じ基準を満たす。convex polygon では variation と投影 span が
      一致するため、bbox 補正との差は後段の hole / concavity / multiple-group test で検出する。
- [ ] 各ケースで distinct scanline level を方向投影から復元し、2 levels 以上あることを先に確認する。
- [ ] 既存 `_level_tolerance(..., min_spacing)` へ 0.0 を渡さない。本数 dedupe 用に nominal corrected
      pitch を基準とする helper を追加するか、既存 helper の引数名を `reference_spacing` へ
      一般化し、positive-floor test の意味を維持する。

### 8.3 hole / concavity / multiple groups

- [ ] square-with-hole は raw hatch segment 数を比較し、全角度が 0°本数から ±1 本以内になる。
- [ ] hole 内部に線を生成しない既存契約を維持する。
- [ ] concave U fixture は全角度が 0°本数から `max(2本, 5%)` 以内になる。
- [ ] 大小の disjoint outer と hole を含む fixture は、family 合計が 0°本数から
      `max(2本, 5%)` 以内になる。
- [ ] 5% tolerance は `ceil(0°基準本数 * 0.05)` とし、整数本数へ切り上げて比較する。
- [ ] multiple group の小領域の segment 数が `round(density)` 未満である固定 fixtureを使い、
      小領域へ個別に `density` 本が割り当てられていないことを確認する。必要なら private helper
      または call spy で、同一 family spacing が全 groupへ渡ることも直接固定する。
- [ ] hole 分割 level の distinct-level dedupe helper と pitch test は既存のまま維持する。

### 8.4 複数 family / gradient / floor

- [ ] `angle_sets=2` と `3` を方向で分類し、各 family の合計線分数を 0°基準と比較する。
- [ ] `angle_sets=3` の代表条件は wide rectangle + hole、base angle `13°`、density `17`、
      `min_spacing=0.0` とし、13° / 73° / 133° の各 family を、別途評価した
      `angle_sets=1, angle=0°` の基準本数と比較する。
- [ ] 単一 convex fixture（必要なら square-with-hole も追加）で
      `spacing_gradient=-4.0, 0.0, 4.0` の angle sweep を行う。gradient 非ゼロでは
      0°本数との差を `max(2本, ceil(基準本数 * 10%))` 以内とする。
- [ ] concave / multiple-group と gradient の組合せは厳密 acceptance にしない。test-local の
      legacy spacing で得た角度 spread より新 spread が小さくなることを characterization し、
      実測値を計画へ記録する。weighted variation は本計画へ追加しない。
- [ ] floor が発動しない小さい正値の `min_spacing` では本数安定化を維持する。
- [ ] floor が発動する場合は既存最小ピッチ契約を優先する。同じ補正済み geometry / angle について、
      正の floor の distinct level 数が `min_spacing=0.0` より増えないことを確認する。
      旧角度補正無し出力や raw segment 数との非増加は要求しない。
- [ ] `density=0` は正の `min_spacing` と組み合わせても hatch を生成しない。

### 8.5 3D / local / regression

- [ ] 全体は nonplanar だが各 face は planar な既存 fixture で、face ごとの角度別本数を比較する。
- [ ] oblique plane 上でも出力を local frame へ戻し、方向・level・面上復元を確認する。
- [ ] `angle=0`, `angle_sets=1`, `min_spacing=0.0` の square は test-local に固定した期待
      coords / offsets または旧算術 reference と array-exact 比較する。hole / multiple group は
      実装前に固定した test-local checksum も併用し、CI が `/tmp` baselineへ依存しないようにする。
- [ ] boundary prefix、empty、degenerate、nonplanar fallback、translation / rotation、Numba / NumPy
      scanline path、決定性の既存 test を削除・弱体化しない。
- [ ] `remove_boundary=False` でも boundary を除外した hatch count だけを測る。

### 8.6 ABI / metadata / stub

- [ ] builtin `fill` evaluator ABI を `"2"` から `"3"` へ上げる。
- [ ] `effect:fill` だけが ABI `"3"`、他 effect は `"1"` のままであることを確認する。
- [ ] catalog declaration が `grafix-builtin-effect-3` を使うことを確認する。
- [ ] `density` の metadata / docstring / 生成 stub に best-effort 本数補正の意味を反映する。
- [ ] canonical plan / docstring / catalog test を維持し、runtime `min_spacing` default を `0.0` へ戻す。

## 9. ベンチマーク計画

### 9.1 baseline

production 実装前に、同じ benchmark definitions と同じ環境で `/tmp` へ baseline JSON を保存する。
Numba warmup 後の steady-state とし、正式比較は `long / warm / disable-gc / seed=0 / 30 samples`
を基本にする。

既存 case:

- `effect.fill.rings_2`
- `effect.fill.dense.rings_2`
- `effect.fill.many_rings`

追加 case:

- `effect.fill.many_rings.angle_37`（multiple-group の非ゼロ角補正 overhead）

新 case は production 変更前に benchmark 定義だけ追加して baseline を取得する。高アスペクト比の
wide / tall rectangle は test-local または `/tmp` の固定 measurement で angle 0/45/90°を測り、
formal benchmark catalogへ汎用性の低い fixtureを増やさない。

### 9.2 記録する値

- median、p95、MAD、samples
- process-cold と compile-cold の代表 1 case
- peak RSS
- output `n_lines`, `n_vertices`, `output_bytes`
- family ごとの hatch segment 数と distinct level 数は、formal benchmark schema を増やさず
  固定 `/tmp` measurement で記録する。
- `ns / generated hatch segment` は formal metric にせず、median と hatch count から事後算出する
  補助観測値に留める。
- environment compatibility と warning
- angle=0 control だけは before / after checksum 一致を要求する。意味が変わる非ゼロ角 case は
  checksum 不一致を正常とし、各 run 内の決定性と geometry metrics を確認する。

### 9.3 性能受入基準

同等出力になる control case:

- angle=0 fast path（少なくとも `effect.fill.many_rings`）の warm median は baseline の `1.05x` 以内。
- p95 は `1.10x` 以内。
- peak RSS は hard gate にせず観測値とする。`max(8 MiB, 10%)` を超えた場合は、
  edge_count を増やす spot-check で `O(edge_count)` を超える保持がないか調査する。

出力本数が仕様として変わる case:

- 過剰本数だった角度では absolute time と output size が減ることを確認する。
- 従来本数が少なかった角度の absolute time 増加は、0°基準本数までの実仕事増と分離して記録する。
- `ns / hatch segment` は固定費の影響を受けるため hard gate にしない。
- head 内で出力本数が近い angle 同士の時間を比較し、補正計算の偏りがないことを確認する。
- 過剰角度の absolute time 非増加と、angle sweep の worst-angle time / output size 非悪化を目標とする。

閾値を超えた場合は、edge buffer の重複生成、family / group loop 内の再計算、不要な temporary を
profile して解消する。精度を落とす bbox 近似や thinning-only へ、測定なしで切り替えない。

## 10. documentation / cache identity

### 10.1 canonical 説明

`fill_meta["density"]` と `fill()` の NumPy style docstring を canonical 説明とする。

- 0°基準 spacing
- angle ごとの projected edge variation 補正
- best-effort であり厳密本数ではないこと
- hole / concavity / multiple group は aggregate されること
- `min_spacing` と gradient がずれを生むこと
- 本数優先により、垂直 pitch と面積当たり総線長は角度依存になり得ること

README、architecture、exporter docs へ重複説明は追加しない。生成 stub は正規 generator から
`src/grafix/api/__init__.pyi` と `typings/grafix/api/__init__.pyi` を同期し、手編集しない。

### 10.2 evaluator ABI

同じ DAG / parameter でも realized geometry が変わるため、
`src/grafix/core/builtins.py` の fill override を `"2" -> "3"` とする。全 effect の ABI を
一括更新しない。旧 cache の wrapper、移行、dual-read は追加しない。

## 11. 変更予定ファイル

### production

- [ ] `src/grafix/core/effects/fill.py`
- [ ] `src/grafix/core/builtins.py`

### generated API artifacts

- [ ] `src/grafix/api/__init__.pyi`
- [ ] `typings/grafix/api/__init__.pyi`

### tests

- [ ] `tests/core/effects/test_fill.py`
- [ ] `tests/core/test_builtin_catalog_bootstrap.py`

### benchmark

- [ ] `src/grafix/devtools/benchmarks/effect_benchmark.py`
- [ ] `tests/devtools/benchmarks/test_effect_benchmark.py`

### docs

- [x] `docs/plan/fill_angle_hatch_count_stabilization_implementation_plan_2026-08-10.md`

### 原則として変更しないファイル

- `src/grafix/core/realized_geometry.py`
- `src/grafix/core/geometry_kernels/planar.py`
- `src/grafix/devtools/benchmarks/cases.py`
- `src/grafix/export/`
- `src/grafix/core/layer.py`
- runtime / capture / worker / serializer
- `sketch/` と `data/`
- `README.md` と `architecture.md`
- 既存の完了済み plan / migration 文書

## 12. 実装フェーズ

### Phase 0: 境界・baseline 固定

- [ ] `git status --porcelain`、branch、HEAD を再記録する。
- [ ] 依頼外差分を確認し、対象外ファイルを触らない。
- [ ] angle=0 の coords / offsets baseline と、angle sweep の本数 JSON を `/tmp` に保存する。
- [ ] benchmark case を先に追加し、production 変更前の formal baseline を保存する。
- [ ] `min_spacing` default の既知 baseline failure を独立に記録し、canonical contract `0.0` へ
      戻す提案を本計画の承認範囲として再確認する。

### Phase 1: RED test

- [ ] variation helper の解析値 test を追加する。
- [ ] convex / hole / concave / multiple-group / angle-family / local-plane の本数 test を追加する。
- [ ] 新しい angle-count test だけを node id / `-k` で実行し、既知の `min_spacing` failure と分離して
      現行実装で RED になることを確認する。既存 test の期待値を安易に弱めない。
- [ ] angle=0 exact baseline と性能 fast path の契約を先に固定する。

### Phase 2: 最小 production 実装

- [ ] valid ring の closed edge delta を一度だけ作る private helper を追加する。
- [ ] edge variation と family spacing を計算する小さな private helper を追加する。
- [ ] planar-global で family spacing を group loop 外に一度だけ作り、全 groupへ再利用する。
- [ ] nonplanar-local で polyline / face ごとの family spacing を作る。
- [ ] angle=0 fast path、density=0、fallback、min-spacing 優先順位を維持する。
- [ ] scanline endpoint kernel と packed output処理を変更していないことを diff で確認する。

### Phase 3: API説明・ABI・stub 同期

- [ ] `density` metadata と docstring を新しい best-effort 契約へ更新する。
- [ ] runtime `min_spacing` default を canonical contract `0.0` へ戻し、既存 docstring / catalog testを維持する。
- [ ] fill evaluator ABI だけを `"3"` へ上げる。
- [ ] packaged / project-local stub を正規生成し、fill の説明以外の差分を混ぜない。

### Phase 4: focused validation

- [ ] fill、catalog、benchmark definition、stub、metadata の focused pytest を通す。
- [ ] 変更対象の Ruff を通す。
- [ ] production source の mypy を通す。
- [ ] `git diff --check` を通す。
- [ ] `git status --porcelain` で依頼外差分を変更していないことを確認する。

### Phase 5: performance / visual acceptance

- [ ] formal warm benchmark を baseline と比較する。
- [ ] process-cold / compile-cold / RSS の代表値を記録する。
- [ ] wide / tall / hole / concave / multiple-group の angle sweep を計測する。
- [ ] `/tmp` の比較 preview で、過度な疎化、境界漏れ、family 欠落がないことを確認する。
- [ ] 結果、採否、未達閾値を本計画の実施結果欄へ追記する。

### Phase 6: broader validation（承認がある場合）

- [ ] effects / API / catalog / stub / benchmark の broader suite を実行する。
- [ ] full pytest は長時間実行に当たるため、必要性を説明して承認後にのみ実行する。

## 13. focused 検証コマンド案

```bash
PYTHONDONTWRITEBYTECODE=1 \
NUMBA_CACHE_DIR=/tmp/grafix-numba-fill-angle-count \
PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/core/effects/test_fill.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/devtools/benchmarks/test_effect_benchmark.py \
  tests/core/parameters/test_description_completeness.py \
  tests/stubs/test_api_stub_sync.py

/opt/anaconda3/envs/gl5/bin/ruff check \
  src/grafix/core/effects/fill.py \
  src/grafix/core/builtins.py \
  src/grafix/devtools/benchmarks/effect_benchmark.py \
  tests/core/effects/test_fill.py \
  tests/core/test_builtin_catalog_bootstrap.py \
  tests/devtools/benchmarks/test_effect_benchmark.py

/opt/anaconda3/envs/gl5/bin/mypy \
  src/grafix/core/effects/fill.py \
  src/grafix/core/builtins.py \
  src/grafix/devtools/benchmarks/effect_benchmark.py

git diff --check
git status --porcelain
```

benchmark は各 case を同じ引数で個別実行し、標準 `benchmark compare` で比較する。

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m grafix benchmark run \
  --case effect.fill.dense.rings_2 \
  --profile long \
  --mode warm \
  --samples 30 \
  --disable-gc \
  --seed 0 \
  --run-id fill-angle-count-dense-before \
  --out /tmp/grafix-fill-angle-count-bench

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m grafix benchmark compare \
  /tmp/grafix-fill-angle-count-bench/runs/fill-angle-count-dense-before.json \
  /tmp/grafix-fill-angle-count-bench/runs/fill-angle-count-dense-after.json \
  --output /tmp/grafix-fill-angle-count-bench/fill-angle-count-dense-compare.json
```

同じ形式で angle=0 control の `effect.fill.many_rings`、changed-output 代表の
`effect.fill.rings_2`、追加する `effect.fill.many_rings.angle_37` を個別に before / after
計測する。`--out` は JSON file ではなく benchmark root directory であり、比較対象はその
`runs/<run-id>.json` とする。

stub の exact 生成コマンドは実装開始時に `grafix stub --help` と既存 stub sync test から確認し、
生成物を手編集しない。

## 14. リスクと対策

### 14.1 本数は揃うが見た目の濃さが変わる

spacing を角度別に変えるため、垂直 pitch と面積当たり総線長は角度依存になる。特に凸形状では
斜め方向が従来より疎に見える可能性がある。

対策: この trade-off を公開 Notes に明記し、angle sweep preview で確認する。本計画はユーザーの
希望どおり本数を優先し、インク密度の同時厳密化は行わない。

### 14.2 hole / concavity で離散誤差が残る

variation は連続積分による近似であり、有限 scan phase、短線除外、頂点一致で数本ずれ得る。

対策: exact count を契約にせず、convex / hole は ±1、複雑形状は小さい相対許容差を使う。
pilot scan や反復補正は性能悪化のため追加しない。

### 14.3 edge reduction の固定費

低 density、小入力では `O(angle_sets * edges)` の補正が相対的に目立つ可能性がある。

対策: angle=0 fast path、scope ごとの一回計算、family ごとの逐次 temporary、formal benchmark の
5% / 10% gateを使う。閾値超過時は重複計算を profile し、推測で複雑化しない。

### 14.4 少なかった角度の実仕事が増える

本数を 0°基準へ揃えるため、縦長形状などでは従来より scanline が増える角度がある。

対策: `D_sum(theta)<=V(theta)` による 0°基準上限を test / measurement で確認し、仕様上の出力増と
アルゴリズム overhead を分けて報告する。この連続近似上限は `spacing_gradient=0` と
group span 合計に限定し、実行時間そのものの保証には使わない。

### 14.5 cache identity の混同

ABI を上げないと同一 DAG が旧 cache geometry を再利用し得る。

対策: fill だけ ABI `3` とし、catalog manifest / declaration test で固定する。

### 14.6 既知 default 不整合を誤った側へ同期する

runtime signature の `0.05` だけを正として既存 docs / test を書き換えると、承認済みの
`min_spacing=0.0` 契約を暗黙に破る。

対策: 本計画では signature を canonical `0.0` へ戻す提案を明示し、ユーザー承認範囲に含める。
意図した `0.05` だった場合は実装前に停止し、別計画へ分離する。本数 test は常に
`min_spacing=0.0` を明示し、差分と実施結果を分けて記録する。

## 15. 完了条件

- [ ] variation 補正により、convex / hole の角度別 hatch segment 数が 0°から ±1 本以内になる。
- [ ] concave / multiple-group と convex-gradient が定めた best-effort 許容差を満たし、
      complex-gradient の angle spread が legacy より改善する。
- [ ] planar-global の共通 spacing と nonplanar-local の独立 scope が維持される。
- [ ] active `min_spacing`、density 0、hole、boundary、3D、fallback の既存契約が維持される。
- [ ] angle=0 fast path が baseline と array-exact で、同等仕事の性能閾値を満たす。
- [ ] pilot scan、post-filter、新 JIT、依存追加、group ごとの density 再配分がない。
- [ ] fill ABI、metadata、docstring、両 stub が新しい生成意味と一致する。
- [ ] runtime `min_spacing` default が canonical contract `0.0` へ戻り、既存 docs / test と一致する。
- [ ] focused pytest、Ruff、mypy、diff-check が成功する。
- [ ] benchmark と visual acceptance の結果、未実施の broad/full test が計画書へ記録される。

## 16. 承認・実装状態

本計画は未承認・未実装である。承認後に Phase 0 から開始し、完了した項目だけを `[x]` へ更新する。
実装前に production code、test、generated stub は変更しない。
