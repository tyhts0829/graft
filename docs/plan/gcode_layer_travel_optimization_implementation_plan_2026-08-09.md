# G-codeレイヤ単位ストローク最適化 実装改善計画（2026-08-09）

- 状態: **承認待ち（実装未着手）**
- 計画作成時branch: `main`
- 計画作成時HEAD: `05cb4bb`
- 対象: G-code exportのレイヤ単位master switch、ストローク順最適化、逆向き描画、短距離bridge

本計画は、`export.gcode.optimize_travel`、`allow_reverse`、
`bridge_draw_distance`を、設定コメントどおり**レイヤ内の全ストローク**へ適用するための
実装改善計画である。併せて`L.layer(..., gcode_optimize=False)`という一つの粗い
master switchを追加し、レイヤごとにG-code最適化全体を使うかどうかだけを指定できるようにする。

レイヤごとの順序最適化、反転、bridge距離を個別設定するAPIは作らない。詳細値は従来どおり
globalな`export.gcode`設定を唯一のownerとし、Layerはその設定群を適用するか否かだけを持つ。

旧`face_block` heuristicは復活させない。face、glyph、fill producerの出力順を推測せず、
作者が明示的に作った`Layer`そのものを最適化とbridgeの境界として扱う。

production code、test、architecture、migration、設定コメントの変更は、本計画への承認後に行う。

### 作業ツリー境界

計画作成開始時には、依頼外の未追跡ファイル
`docs/plan/delaunay_primitive_closed_faces_implementation_plan_2026-08-09.md`が存在した。
計画作成中にも別作業によるDelaunay関連のtracked/untracked差分が発生している。
本タスクでは、それらを編集、移動、削除、stage、restoreしない。実装開始時に改めて
`git status --porcelain`を記録し、G-code対象差分だけを管理する。

## 1. 背景と現在の問題

現在の`.grafix/config.yaml`と同梱default configは、次の利用者向け契約を示している。

- `optimize_travel=true`: レイヤ内でストローク順を並べ替え、ペンアップ移動距離を減らす。
- `allow_reverse=true`: 最適化時にストロークの逆向き描画を許可する。
- `bridge_draw_distance=d`: 次ストロークまでの距離が`d`未満なら、ペンアップを省略して
  描画で繋ぐ。
- `bridge_draw_distance=null`: bridgeを無効にする。

これに加え、利用者が必要としているLayer APIは次の一項目だけである。

- `gcode_optimize=true`: そのレイヤへglobalなG-code最適化設定群を適用する。
- `gcode_optimize=false`: そのレイヤでは並べ替え、反転、bridgeをすべて行わない。

Layerごとの`optimize_travel`、`allow_reverse`、`bridge_draw_distance`個別overrideは要求しない。

しかし現行exporterは、異なるinput polylineをsemantic boundaryとし、上記3設定を
「同じsource polylineが紙クリップで複数fragmentになった場合」にしか適用しない。

- `src/grafix/export/gcode.py::_collect_layer_strokes()`はstrokeをsource polylineごとに分ける。
- `_order_polyline_fragments()`は各source内だけを並べ替える。
- `_emit_polyline_fragments()`はsourceごとにbridge状態をresetする。
- `E.fill`は各ハッチ線を独立した2頂点polylineとして返すため、通常のfillでは全設定が
  実質no-opになる。

`sketch/readme/grn/18.py`の再現計測では、次の状態を確認済みである。

- template layer: 11,610 source polylines
- うち2頂点のfill hatch: 11,560本
- 全source polylineがclip fragment 1本だけ
- `optimize_travel`、`allow_reverse`、`bridge_draw_distance`の有効・無効を変えた
  G-codeがbyte単位で同一
- 現入力順のstroke間距離合計: 約14,181.67 mm
- 既存optimizerをlayer全体へ仮適用した距離: 約609.40 mm（約95.70%減）

optimizer自体は空間index付きの決定的なnearest-neighbor実装として機能している。
問題はproduction経路が1本ずつoptimizer/emitterへ渡していることである。

## 2. ゴール

- `L.layer(..., gcode_optimize=True/False)`で、レイヤごとにG-code最適化全体を
  一つのboolで有効・無効化できる。
- `gcode_optimize=true`を既定とし、既存sketchと暗黙Layerはglobal設定を従来どおり使う。
- `gcode_optimize=false`では、global設定値にかかわらず入力順・入力方向を保持し、
  stroke間を必ずpen-up移動する。
- clipping後に得られた**同一レイヤ内の全stroke**を、一つの最適化対象として扱う。
- `optimize_travel=true`で、source polyline境界を越えてstroke順を最適化する。
- `allow_reverse=true`で、source polyline境界を越えた候補も含めて各strokeの反転を許可する。
- `bridge_draw_distance`を、最終的に確定したレイヤ内stroke列の隣接stroke間へ適用する。
- bridge距離未満では、利用者が許可した短いconnectorをpen-downで描き、Zの上下回数を減らす。
- `bridge_draw_distance=null`では、異なるstroke間へconnectorを一切追加しない。
- レイヤ境界は常に維持し、並べ替え、反転候補探索、bridgeを別レイヤへ跨がせない。
- `face_block`、頂点数、closed-like判定、glyph/fill順からgroupを推測しない。
- `18.py`の文字fillで、設定値の変更が実際のG-code順序、反転、Z回数へ反映される。
- 現在の決定性、clip、bed検証、座標変換、安全なZ commandを維持する。

## 3. 非ゴール

- Layerごとの`optimize_travel`、`allow_reverse`、`bridge_draw_distance`個別override
- Layerごとのbridge距離、reverse policy、ordering algorithmの指定
- Inspector/Parameter GUIからの`gcode_optimize`変更とParamStore永続化
- configでlayer名や`site_id`を指定して最適化policyをmappingする機能
- `E.fill`のハッチ生成方法、packed Geometry、offset構造の変更
- core DAG、effect return、`RealizedGeometry`へのface/group metadata追加
- glyph、face、hole、primitive単位の最適化scope追加
- layerを跨ぐstroke最適化またはbridge
- nearest-neighborをTSP solverや全体最適解へ置き換えること
- 先頭stroke選択規則の変更
- G-code dialect、feed、Z、origin、Y反転、bed検証の変更
- config keyの追加・改名、既定値の変更
- 旧`face_block` heuristicまたは互換実装の復活
- `sketch/readme/grn/18.py`やpreset作品側でstroke順を手動調整する回避策
- 既存生成済みG-code artifactの一括再生成

## 4. 変更後の契約

### 4.1 Layerのmaster switch

公開APIへ`L.layer(..., gcode_optimize: bool = True)`を追加し、生成される
`Layer.gcode_optimize`へ保持する。このboolは詳細値ではなく、そのLayerへglobalな
G-code最適化設定群を適用するかどうかだけを表す。

- `true`（既定）:
  - globalな`GCodeParams.optimize_travel`、`allow_reverse`、
    `bridge_draw_distance`を、そのLayerへ通常どおり適用する。
- `false`:
  - global設定値にかかわらずstroke順を並べ替えない。
  - strokeを反転しない。
  - stroke間をbridgeせず、必ずpen-up移動する。

exporterはLayerごとに、次のeffective policyを一度だけ解決する。

```python
if not layer.gcode_optimize:
    effective_optimize_travel = False
    effective_allow_reverse = False
    effective_bridge_draw_distance = None
else:
    effective_optimize_travel = params.optimize_travel
    effective_allow_reverse = params.optimize_travel and params.allow_reverse
    effective_bridge_draw_distance = params.bridge_draw_distance
```

`gcode_optimize`は`bool`だけを受け付け、`None`による継承、Layerごとの距離値、
reverse設定、algorithm設定は持たせない。既存sketchと暗黙に生成されるLayerは既定の
`true`となる。

### 4.2 最適化単位

- master switchが有効な場合、最適化とbridgeの単位は`RealizedLayer`一つとする。
- input polylineはstrokeの出典を示す`poly_idx`として保持するが、
  `effective_optimize_travel=true`時の順序境界にはしない。
- 各input polylineをpaper safe rectへclipして得た全fragmentを、入力の
  `(poly_idx, seg_idx)`順で一つのflat stroke列へ詰める。
- empty layerは何も出力せず、1 stroke layerはそのstrokeだけを元向きで出力する。
- Layer境界ではordering候補とbridge状態を必ずresetする。

### 4.3 global `optimize_travel`

`gcode_optimize=true`のLayerに対して、global設定を次のように適用する。

- `false`:
  - flat stroke列の入力順を維持する。
  - strokeを反転しない。
- `true`:
  - 既存`_order_strokes_in_layer()`をflat stroke列へ一度だけ適用する。
  - 先頭strokeは、現契約どおり入力先頭に固定し、反転しない。
  - 2本目以降は、直前stroke終点から次候補の始点までの量子化距離を最小化する
    greedy nearest-neighbor順とする。
  - 同距離時の`(poly_idx, seg_idx, reversed, stroke_index)` tie-breakを維持する。

先頭strokeを機械原点や前レイヤ終点に合わせて選び直す変更は、今回の範囲へ含めない。

### 4.4 global `allow_reverse`

- `effective_optimize_travel=true`かつglobal `allow_reverse=true`の場合だけ、各候補strokeの
  終点側から入る反転候補を比較する。
- `effective_optimize_travel=false`では、global `allow_reverse`の値にかかわらず入力方向を
  維持する。
- 反転は点列の巡回方向だけを変え、bridgeを除くpen-down geometry自体は変えない。

### 4.5 global `bridge_draw_distance`

- bridge判定は、最適化と反転が確定した**後**の隣接stroke間で行う。
- `bridge_draw_distance=d`は、「同一レイヤ内の隣接stroke間距離が`d`未満なら、
  その短いgapを描いて繋いでよい」という利用者の明示的な許可として扱う。
- 判定単位はmmで、現契約どおり`decimals`に対応するcanvas量子化端点間の
  Euclidean distanceを使う。
- 比較は現行どおり厳密な`distance < d`とし、`distance == d`ではbridgeしない。
- bridgeする場合はpen-downとdraw feedを維持して次stroke始点へ移動する。
- bridgeしない場合はpen-up、travel feed、次stroke始点へ移動、pen-downの順とする。
- global値が`null`では全stroke間でbridgeを無効にする。
- global `optimize_travel=false`かつbridge有効の場合は、入力順で隣接するstroke間へ適用する。
- `gcode_optimize=false`のLayerでは、global距離にかかわらずbridgeを無効にする。
- レイヤ先頭strokeと直前レイヤ末尾strokeの間は、距離にかかわらずbridgeしない。

### 4.6 geometryと描画順の意味

- global `optimize_travel`と`allow_reverse`はstrokeの順序・方向を変えるが、stroke内部の
  pen-down線分集合を増減させない。
- global `bridge_draw_distance`だけが、指定距離未満のconnector線分を意図的に追加する。
- レイヤ単位ですべての最適化を止めたい利用者は`gcode_optimize=false`を使う。
- 全Layerで描画順や方向を保持したい利用者はglobal `optimize_travel=false`を使う。
- 全Layerでconnectorを一切追加したくない利用者はglobal `bridge_draw_distance=null`を使う。

## 5. 実装設計

### 5.1 Layer modelと公開API

- `src/grafix/core/layer.py`の`Layer`末尾へ`gcode_optimize: bool = True`を追加する。
- `Layer.__post_init__()`で`exact_bool(..., name="Layer.gcode_optimize")`を使い、
  `0`、`1`、文字列などの暗黙boolを拒否する。
- `src/grafix/api/layers.py`の`L.layer()`へkeyword-only
  `gcode_optimize: bool = True`を追加し、生成する`Layer`へそのまま渡す。
- `src/grafix/devtools/generate_stub.py`と二つの生成stubを同期する。
- `Layer`は既存の`RealizedLayer.layer`に保持されたままexport workerまで運ばれるため、
  Frame、snapshot、worker protocol、capture manifestへ新fieldを追加しない。
- geometryを変えないexport hintなので、geometry/GPU cache keyへ含めない。
- SVG、PNG、GL exporterはこのfieldを参照せず、出力を変えない。
- Inspector/Parameter GUI、ParamStore、Layer style永続化には接続しない。

### 5.2 stroke収集をflat化

`src/grafix/export/gcode.py`を次の形へ単純化する。

- `_collect_layer_strokes()`を、`list[tuple[int, list[_Stroke]]]`ではなく
  `list[_Stroke]`を返す実装へ変更する。
- すべてのsource polylineを入力順で走査し、各clip fragmentをflat listへappendする。
- `_Stroke.poly_idx`、`seg_idx`、`points_canvas`、`start_q`、`end_q`は維持する。
- source、face、ringを表す追加containerや推測処理は作らない。

関数名は責務に合わせて`_collect_strokes_in_layer()`へ変更する。旧private helperの
compatibility wrapperは残さない。

### 5.3 effective policy解決とordering経路の一本化

- exporterのLayer loop冒頭で、4.1のeffective policyを一度だけ解決する。
- `_order_polyline_fragments()`を削除する。
- `effective_optimize_travel=false`では`[(stroke, False), ...]`をそのまま作る。
- `effective_optimize_travel=true`ではflat listへ`_order_strokes_in_layer()`を一度だけ呼ぶ。
- `effective_allow_reverse`だけをoptimizerへ渡す。
- `_order_strokes_in_layer()`と`_StrokeEndpointGrid`のアルゴリズムは変更しない。
- spatial-indexのreference testとtie-break testを維持する。

### 5.4 emitterをレイヤ単位に変更

- `_emit_polyline_fragments()`を`_emit_layer_strokes()`へ変更する。
- 1 layerにつき一度だけ呼び、`current_end_q`をlayer内で維持する。
- `effective_bridge_draw_distance`だけをemitterへ渡す。
- layer呼び出しごとに`current_end_q=None`から始め、layer間bridgeを防ぐ。
- bridge判定はsource polyline境界を特別扱いしない。
- `_GCodeEmitter`の座標変換、丸め、bed範囲検証、重複XY抑制、Z/feed状態管理は
  変更しない。

### 5.5 G-code comment

レイヤ単位の並べ替え後は、同一source polylineのstrokeが連続する保証がない。

- `; source_polyline {n} start/end`の連続block表現を削除する。
- 各strokeにある
  `; stroke polyline {poly_idx} seg {seg_idx}[ reversed]`
  を出典・反転確認用commentとして維持する。
- `gcode_optimize`専用の新しいcommentやmanifest fieldは追加しない。
- executable G-codeと無関係な旧comment形式の互換shimは作らない。
- testsやdiagnostic parserはstroke commentを唯一の出典表現として使う。

## 6. テスト計画

### 6.1 Layer modelと公開API test

対象: `tests/core/test_layer.py`、`tests/api/test_layer_helper.py`、
`tests/stubs/test_api_stub_sync.py`

- [ ] `Layer.gcode_optimize`と`L.layer(..., gcode_optimize=...)`の既定値が`true`である。
- [ ] `L.layer(..., gcode_optimize=False)`が生成Layerへ`false`を保持する。
- [ ] 複数Geometryをconcatする場合も値を保持する。
- [ ] `True`と`False`以外の`0`、`1`、文字列、`None`をexact bool validationで拒否する。
- [ ] `Layer`を直接構築する場合も同じvalidationになる。
- [ ] Geometryから暗黙に作られるLayerが既定の`true`になる。
- [ ] stub生成結果とrepository内の二つの公開stubが一致する。

### 6.2 ordering unit test

対象: `tests/export/test_gcode_ordering.py`

- [ ] 既存spatial indexとquadratic referenceの順序一致を維持する。
- [ ] `allow_reverse=false/true`、empty、1本、完全tieを維持する。
- [ ] source polylineが異なるstrokeを混在させても、距離と既存tie-breakだけで
  決定されることを明示する。
- [ ] 先頭stroke固定・非反転を明示する。

### 6.3 exporter contract test

対象: `tests/export/test_gcode.py`

- [ ] `gcode_optimize`未指定と明示的な`true`が同じG-codeを出す。
- [ ] global 3設定がすべて有効でも、`gcode_optimize=false`のLayerは入力stroke順・
  入力方向を保持し、近距離stroke間も必ずpen-upする。
- [ ] 同一frame内で`gcode_optimize=true`のLayerだけがreorder/reverse/bridgeされ、
  `false`のLayerは一切最適化されない。
- [ ] global `optimize_travel=false`かつbridge有効、Layer master有効の場合は、
  入力順を保ったままbridgeだけが働く。
- [ ] global設定が無効な項目を、`gcode_optimize=true`が勝手に有効化しない。
- [ ] `optimize_travel=false`で、異なるsource polylineを含むflat stroke列の入力順と
  向きが保持される。
- [ ] `optimize_travel=true`で、異なるsource polyline間が実際に並び替わり、
  fixtureのpen-up距離が減る。
- [ ] `allow_reverse=false`では順序最適化しても各strokeを反転しない。
- [ ] `allow_reverse=true`では、異なるsource polylineのstrokeも必要に応じて反転する。
- [ ] 一つのsource polylineから生じた複数clip fragmentも、他strokeと同じlayer-wide候補になる。
- [ ] `bridge_draw_distance`有効時、異なるsource polyline間でもgapが閾値未満なら
  Z-upなしでconnectorを描く。
- [ ] 上記bridgeは`optimize_travel=false`でも入力順に対して働く。
- [ ] 上記bridgeは`optimize_travel=true`では最適化・反転後の端点に対して働く。
- [ ] gapが閾値と厳密に等しい場合はbridgeしない。
- [ ] gapが閾値を超える場合はpen-upする。
- [ ] `bridge_draw_distance=null`では、近距離かつ別sourceでも必ずpen-upする。
- [ ] 2 layer fixtureで、巨大なbridge距離でもlayer境界をbridgeしない。
- [ ] `optimize_travel=false`かつbridge無効では、現行の入力順G-codeを維持する。
- [ ] 同じframeを2回exportしてbyte-exactで一致する。
- [ ] bridge無効時、最適化前後でstroke内部のpen-down線分集合が、順序・方向を除いて一致する。
- [ ] bridge有効時に追加されたpen-down connectorが、すべて指定閾値未満である。
- [ ] paper clipping、bed bounds、Y反転、origin、decimal丸め、安全なfooterが退行しない。

### 6.4 fill相当の統合test

- [ ] 複数の2頂点ハッチstrokeを持つsynthetic fill-like layerを作る。
- [ ] `optimize_travel=true`でsource順が変わり、pen-up距離が明確に減ることを確認する。
- [ ] `allow_reverse=true`でserpentine相当の反転が発生することを確認する。
- [ ] `bridge_draw_distance`未満の隣接ハッチでZ-upが省略されることを確認する。
- [ ] boundaryあり/なし、open/closed polyline混在でもface推測をせず、layer-wideで
  同じ契約が使われることを確認する。
- [ ] 同じfixtureを`gcode_optimize=false`にすると入力順・方向・全pen-upへ戻る。

fontやplatform resourceへ依存する`18.py`全体はunit testへ固定せず、後述の実作品acceptanceで使う。

### 6.5 export経路と非G-code exporter test

- [ ] `tests/export/test_capture_service.py`で、通常G-codeとlayer split G-codeの双方が
  Layer masterを保持する。
- [ ] `tests/interactive/runtime/test_export_job_system.py`で、spawn workerへ渡したLayerの
  `gcode_optimize=false`がexport結果へ反映される。
- [ ] `tests/export/test_svg.py`で、同じgeometryの`gcode_optimize=true/false`が
  byte-exactな同一SVGを出す。
- [ ] capture manifest schema、ParamStore、Layer style recordが増えていないことを確認する。

### 6.6 現行contract testの置換

次の現行testは、新契約と正反対なので名前とassertionを置き換える。

- [ ] `test_export_gcode_keeps_input_polyline_order_when_optimization_is_enabled`
  - layer内の異なるsourceが距離順へ並び替わるtestにする。
- [ ] `test_export_gcode_draw_bridge_never_crosses_input_polyline_boundary`
  - layer内の近距離source境界をbridgeするtestにする。
- [ ] `test_export_gcode_keeps_mixed_open_and_closed_polylines_in_input_order`
  - face推測なしで全strokeをlayer-wide最適化するtestにする。
- [ ] `test_export_gcode_keeps_multiple_face_and_hole_source_order`
  - boundary有無に依存せず同じlayer-wide契約になるtestにする。
- [ ] `test_export_gcode_draw_bridge_does_not_cross_mixed_polyline_boundaries`
  - sourceではなくlayer境界だけをbridge禁止境界として確認するtestにする。

旧contractを残すfeature flag、legacy mode、互換testは追加しない。

## 7. 実作品acceptance

`sketch/readme/grn/18.py`を、repositoryの`.grafix/config.yaml`を使って`/tmp`へ
G-code exportし、次を測定する。

- [ ] manifestのeffective configが
  `optimize_travel=true`、`allow_reverse=true`、`bridge_draw_distance=0.5`である。
- [ ] `18.py`が生成する既存Layerは、API既定値により`gcode_optimize=true`である。
- [ ] template layerでsource polyline境界を越えた並べ替えが発生する。
- [ ] `reversed` strokeが0本ではない。
- [ ] bridgeされたtransitionが0件ではなく、全件0.5 mm未満である。
- [ ] 全設定有効版と全設定無効版のG-codeがbyte同一ではない。
- [ ] 同じrealized geometryのtemplate layerだけを`gcode_optimize=false`へ置き換えた比較版では、
  そのLayerの入力順・入力方向が維持され、stroke間connectorが0本になる。
- [ ] 上記比較版でも、masterが有効な他Layerのpolicyは変わらない。
- [ ] template layerのstroke間距離合計が、現入力順に対して90%以上減る。
- [ ] template layerのZ-up回数が、現行11,610回から大幅に減る。
- [ ] 同じ設定で2回exportしたartifactがbyte-exactで一致する。
- [ ] 全XY commandがpaper/bed検証を通り、footerで安全なZへ退避する。

計測結果、実行時間、stroke数、reverse数、bridge数、pen-up距離、Z-up回数を
本計画の実施結果欄へ記録する。生成G-codeはrepositoryへ追加しない。

## 8. Documentationとmigration

### 8.1 現行contract文書

- [ ] `src/grafix/core/layer.py`
  - `gcode_optimize`がG-code exporterだけで使う一括on/off hintであることを
    `Layer` docstringへ明記する。
- [ ] `src/grafix/api/layers.py`
  - `L.layer()`のNumPy style docstringへ既定値、global設定との関係、`false`時に
    reorder/reverse/bridgeをすべて止めることを明記する。
- [ ] `src/grafix/export/gcode.py`
  - module前提を「layer全strokeをclip後に最適化・bridge」へ更新する。
  - source polyline境界を保持する記述を削除する。
  - Layer masterとglobal詳細設定からeffective policyを解決する契約を記載する。
- [ ] `src/grafix/core/gcode_params.py`
  - 3設定のscope、単位、strict threshold、`null`の意味をdocstringへ明記する。
- [ ] `src/grafix/resource/default_config.yaml`
  - 「`gcode_optimize=true`の同一レイヤ内」「最適化後の隣接stroke」「mm」「未満」を
    明記する。
- [ ] `.grafix/config.yaml`
  - packaged defaultと同じ説明へ同期する。
- [ ] `architecture.md`
  - input polyline semantic boundary契約をlayer semantic boundary契約へ置き換える。
  - face/groupを推測しないことは維持する。
  - Layer masterからeffective 3値を解決する式と、他exporterが無視することを明記する。
- [ ] `docs/architecture_visualization.md`
  - `Layer master -> fragments -> layer-wide reorder/reverse/bridge`へ図と説明を更新する。
- [ ] `src/grafix/devtools/generate_stub.py`、`src/grafix/api/__init__.pyi`、
  `typings/grafix/api/__init__.pyi`
  - `L.layer(..., gcode_optimize: bool = ...)`を公開signatureへ同期する。

### 8.2 migration

- [ ] `docs/migration_2026-08-09.md`を新規作成する。
  - 2026-07-22のsource-polyline限定contractを置き換える破壊的変更と記録する。
  - `optimize_travel=true`で異なるsource polyline順が変わることを明記する。
  - `allow_reverse=true`で異なるsource polylineも反転し得ることを明記する。
  - `bridge_draw_distance`が許可距離未満のconnectorをlayer内へ追加することを明記する。
  - `L.layer(..., gcode_optimize=False)`がそのLayerの3機能を一括停止することを明記する。
  - global設定は詳細値、Layer boolは一括適用gateというowner関係を明記する。
  - Layer単位の一括停止と、globalな順序保持・connector禁止の設定例を示す。

`docs/migration_2026-07-22.md`は当時の履歴なので書き換えない。必要なら末尾に
新migrationへの短い参照だけを追加する。

## 9. 変更対象ファイル

### 9.1 Production

- [ ] `src/grafix/core/layer.py`
- [ ] `src/grafix/api/layers.py`
- [ ] `src/grafix/export/gcode.py`
- [ ] `src/grafix/core/gcode_params.py`
- [ ] `src/grafix/resource/default_config.yaml`
- [ ] `.grafix/config.yaml`
- [ ] `src/grafix/devtools/generate_stub.py`

### 9.2 生成stub

- [ ] `src/grafix/api/__init__.pyi`
  - 現在のDelaunay関連並列差分を保持し、`_L.layer` signatureだけを追加同期する。
- [ ] `typings/grafix/api/__init__.pyi`

### 9.3 Tests

- [ ] `tests/core/test_layer.py`
- [ ] `tests/core/test_pipeline.py`
- [ ] `tests/api/test_layer_helper.py`
- [ ] `tests/stubs/test_api_stub_sync.py`
- [ ] `tests/export/test_gcode.py`
- [ ] `tests/export/test_gcode_ordering.py`
- [ ] `tests/export/test_capture_service.py`
- [ ] `tests/export/test_svg.py`
- [ ] `tests/interactive/runtime/test_export_job_system.py`

### 9.4 Docs

- [ ] `architecture.md`
- [ ] `docs/architecture_visualization.md`
- [ ] `docs/migration_2026-08-09.md`（新規）
- [ ] `docs/migration_2026-07-22.md`（新migrationへの参照が必要な場合のみ）
- [ ] 本計画ファイルのchecklistと実施結果

変更中に追加対象が判明した場合は、理由を本計画へ追記してから変更する。

## 10. 実装手順

### Phase 0: baseline固定

- [ ] 作業開始時の`git status --porcelain`を確認し、依頼外差分へ触れない。
- [ ] G-code focused testsを実行し、開始時結果を記録する。
- [ ] `18.py`の現行G-codeを`/tmp`へ再生成し、stroke/reverse/bridge/Z-up/travel/時間を記録する。
- [ ] 現行config provenanceをcapture manifestで確認する。

### Phase 1: RED contract tests

- [ ] Layer/APIの既定値、`false`伝播、exact bool validation testを先に追加する。
- [ ] global全設定が有効でも`gcode_optimize=false`なら3機能すべて止まるtestを追加する。
- [ ] 同一frame内でLayerごとにmasterのon/offが分かれるtestを追加する。
- [ ] source polylineを越えるreorder/reverse testを先に新契約へ変更し、現行実装で失敗することを確認する。
- [ ] source polylineを越えるbridge testを先に新契約へ変更し、現行実装で失敗することを確認する。
- [ ] layer境界を越えないtestを追加する。
- [ ] bridge無効時のpen-down geometry不変testを追加する。
- [ ] strict thresholdと最適化後bridgeのtestを追加する。

### Phase 2: Layer APIとexporter単純化

- [ ] `Layer`と`L.layer()`へ`gcode_optimize: bool = True`を追加する。
- [ ] exact bool validationを追加する。
- [ ] stub generatorを更新し、並列差分を保持したまま二つのstubを同期する。
- [ ] stroke収集をflat listへ変更する。
- [ ] Layer masterとglobal設定からeffective policyを一度だけ解決する。
- [ ] source単位ordering helperを削除し、layer単位orderingへ一本化する。
- [ ] emitterをlayer単位の一回呼び出しへ変更する。
- [ ] source block commentを削除し、stroke commentへ統一する。
- [ ] private compatibility wrapperやface heuristicが残っていないことを確認する。

### Phase 3: focused regression

- [ ] Layer model、Layer API、pipeline、stub sync testsを通す。
- [ ] ordering unit testsを通す。
- [ ] G-code exporter testsを通す。
- [ ] SVGでLayer masterが出力へ影響しないtestを通す。
- [ ] `tests/export/test_capture_service.py`のG-code encode/config/layer-split経路を通す。
- [ ] `tests/interactive/runtime/test_export_job_system.py`のG-code worker経路を通す。
- [ ] `tests/interactive/runtime/test_capture_export_safety.py`のG-code publish/rollback経路を通す。
- [ ] config parsingの既存focused testsを通す。
- [ ] `ruff`、`mypy`、`git diff --check`を通す。

### Phase 4: docsとmigration

- [ ] config、docstring、architecture、visualizationを新契約へ同期する。
- [ ] 2026-08-09 migrationを追加する。
- [ ] repository全体を検索し、source-polyline限定という現行契約の残存記述を解消する。
- [ ] 2026-07-22の歴史文書と現行契約を混同しないことを確認する。

### Phase 5: `18.py` acceptance

- [ ] default config、global全設定無効、bridgeのみ無効、template layer master無効の
  比較G-codeを`/tmp`へ生成する。
- [ ] travel、reverse、bridge、Z-up、実行時間、決定性を測定する。
- [ ] template layer master無効版だけが入力順・方向・全pen-upへ戻ることを検証する。
- [ ] 全connectorが閾値未満であることを機械的に検証する。
- [ ] 結果を本計画へ追記し、未完了項目を明示する。

### Phase 6: 最終検証

- [ ] focused test/lint/typeを再実行する。
- [ ] full pytestは長時間実行の承認境界を確認してから実行する。
- [ ] 最終`git status --porcelain`で依頼外差分を区別する。
- [ ] 完了項目だけを`[x]`へ更新し、未実施項目を残す。

## 11. 検証コマンド案

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/core/test_layer.py \
  tests/core/test_pipeline.py \
  tests/api/test_layer_helper.py \
  tests/stubs/test_api_stub_sync.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/export/test_gcode_ordering.py \
  tests/export/test_gcode.py \
  tests/core/test_runtime_config.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/export/test_capture_service.py \
  tests/export/test_svg.py \
  tests/interactive/runtime/test_export_job_system.py -k gcode

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/interactive/runtime/test_capture_export_safety.py -k gcode

ruff check \
  src/grafix/core/layer.py \
  src/grafix/api/layers.py \
  src/grafix/export/gcode.py \
  src/grafix/core/gcode_params.py \
  src/grafix/devtools/generate_stub.py \
  tests/core/test_layer.py \
  tests/core/test_pipeline.py \
  tests/api/test_layer_helper.py \
  tests/stubs/test_api_stub_sync.py \
  tests/export/test_gcode.py \
  tests/export/test_gcode_ordering.py \
  tests/export/test_capture_service.py \
  tests/export/test_svg.py \
  tests/interactive/runtime/test_export_job_system.py

mypy src/grafix
git diff --check
```

`18.py` acceptanceのCLI例:

```bash
PYTHONDONTWRITEBYTECODE=1 \
NUMBA_CACHE_DIR=/tmp/grafix-numba-gcode-layer-opt \
PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m grafix export \
  --callable sketch.readme.grn.18:draw \
  --canvas 148 210 \
  --out /tmp/grn18_layer_optimized.gcode \
  --overwrite
```

## 12. リスクと対処

### 12.1 G-code固有hintをLayerへ持たせること

汎用scene modelの`Layer`へ、G-code exporterだけが読むfieldを一つ追加する。

- 最適化scopeそのものがLayerなので、名前や`site_id`から外部mappingするより直接的である。
- 既存の`RealizedLayer.layer`とworker pickle経路をそのまま使え、新しいDTOや依存層を作らない。
- 一つのexact boolに限定し、per-setting fieldや汎用metadata dictは追加しない。
- 将来Layer別の詳細policyが本当に必要になった時だけ、typed options objectを別途再検討する。

### 12.2 G-code順序の破壊的変更

`optimize_travel=true`では、これまで保持していたsource polyline順が変わる。

- これは設定コメントどおりの意図した挙動としてmigrationへ記録する。
- 全Layerで順序だけを保持する方法はglobal `optimize_travel=false`、特定Layerの
  最適化全体を止める方法は`gcode_optimize=false`と明記する。
- legacy modeは追加しない。

### 12.3 bridgeによるconnector追加

bridgeは指定距離未満の短線を意図的に追加する。

- `bridge_draw_distance`を利用者の明示許可として扱う。
- global `null`で全Layer、`gcode_optimize=false`で対象Layerだけ完全無効にできる契約をtestする。
- 全connector長がstrict threshold未満であることをtestと実作品計測で検証する。
- face/glyph単位の暗黙除外は行わない。

### 12.4 大量strokeのordering cost

layer-wide化により、fillの数千〜数万strokeが実際にoptimizerへ渡る。

- 既存のspatial endpoint gridを再利用し、quadratic scanへ戻さない。
- ordering helperのreference一致testを維持する。
- `18.py`で実時間をbefore/after計測する。
- 明確な回帰があれば、契約を縮小せずspatial index側をprofileして直す。

### 12.5 comment consumer

source block commentを外部toolが非公式に読んでいる可能性がある。

- executable commandではないため互換shimは作らない。
- stroke commentに`poly_idx`と`seg_idx`を残し、追跡可能性は維持する。
- migrationへcomment形式変更を記録する。

### 12.6 歴史文書との不一致

2026-07-22 migrationはsource-polyline限定を意図的変更として記録している。

- 過去文書を書き換えて履歴を消さない。
- 新migrationと現行architectureを新しい真実とする。

## 13. 完了条件

- [ ] `L.layer(..., gcode_optimize=True/False)`が一つのexact bool masterとして公開され、
  既定値は`true`である。
- [ ] `gcode_optimize=false`のLayerは、global値にかかわらずreorder、reverse、bridgeを
  すべて行わない。
- [ ] `gcode_optimize=true`のLayerは、既存global 3設定を詳細policyとして適用する。
- [ ] Layerごとの距離、reverse、ordering algorithmなどの個別設定APIを追加していない。
- [ ] layer内の異なるsource polylineが`optimize_travel=true`で並び替わる。
- [ ] `allow_reverse=true`で異なるsource polylineのstrokeも反転候補になる。
- [ ] `bridge_draw_distance`未満のlayer内gapがsource境界に関係なくpen-down接続される。
- [ ] `bridge_draw_distance=null`ではconnectorが追加されない。
- [ ] layer境界をreorder/reverse/bridgeが越えない。
- [ ] face/ring/glyphを推測するコードまたはmetadataが追加されていない。
- [ ] bridge無効時のpen-down geometryが順序・方向を除いて不変である。
- [ ] `18.py`でtravelとZ-up回数が大幅に減る。
- [ ] deterministic export、clip、bed validation、安全commandが維持される。
- [ ] Layer masterが通常capture、split G-code、spawn worker経路で保持される。
- [ ] SVG、PNG、GL出力とgeometry/cache identityがLayer masterで変わらない。
- [ ] global config key、既定値、capture manifest schemaを変更していない。
- [ ] config、docstring、architecture、visualization、migrationが実装と一致する。
- [ ] focused test、ruff、mypy、`git diff --check`がpassする。
- [ ] full pytestは承認された場合にpassする。未実施なら未実施理由を明記する。
- [ ] 本計画の完了・未完了checklistと実測結果が更新される。

## 14. 承認後に行うこと

本計画への承認後、Phase 0から順に実装する。承認前にはproduction code、test、architecture、
configを変更しない。
