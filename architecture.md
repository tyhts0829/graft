<!--
どこで: `architecture.md`。
何を: `src/grafix/` の現行実装に対応する責務境界、依存方向、状態・resource の所有権。
なぜ: 機能追加やリファクタで、変更理由の異なる責務を再び混ぜないため。
-->

# Grafix アーキテクチャ

## 1. 中心となる設計

Grafix は、線の生成と変形を **不変な Geometry DAG** として記述し、必要な時点で
`RealizedGeometry` へ評価する creative-coding toolkit である。

設計の中心は次の五点にある。

1. `Geometry` は配列ではなく、operation、入力、引数、operation version を持つレシピである。
2. evaluator、parameter schema、preset は session/generation ごとの immutable catalog に固定する。
3. quality、評価へ影響する最小の `EvaluationConfig`、operation、外部 asset を cache identity に明示する。
4. cache と外部 resource は lifetime を持つ owner が保持する。composition 内の子 session は
   明示注入された dependency を借用し、standalone session は省略された dependency だけを所有する。
5. coordinator は call order と配線だけを持ち、state mutation、encode、publish、window policy を
   それぞれの owner へ委譲する。

描画 style は Geometry から分離し、`Layer` が Geometry と色・線幅を束ねる。同じ Geometry を
異なる style で描いても CPU の geometry cache を共有できる。

## 2. レイヤと依存方向

| レイヤ | 主な責務 |
|---|---|
| `grafix.api` | 公開 DSL (`G` / `E` / `L` / `P`)、`run`、`render`、`export` の facade / composition root |
| `grafix.core` | Geometry、catalog、評価、parameters、immutable runtime/evaluation config の domain contract |
| `grafix.core.geometry_kernels` | packed geometry、平面、grid、raster、marching、resample の数値 kernel |
| `grafix.runtime_config_loader` | YAML/package resource、CWD/HOME 探索、merge、path 解決 |
| `grafix.parameter_storage` | ParamStore の非変更 read、明示 recovery、atomic commit |
| `grafix.export` | 形式別 encode、出力 path、staging、no-clobber publish、provenance collection |
| `grafix.interactive` | diagnostics / transport / telemetry の中立 contract と GL / MIDI / GUI の leaf 実装 |
| `grafix.interactive.runtime` | window loop と interactive subsystem の composition |
| `grafix.devtools` | CLI、stub、diagnostics、benchmark tooling |

依存規則は次のとおり。

```text
user sketch
    |
    v
grafix.api -----------------------> grafix.interactive.runtime
    |                                        |
    |                                        +--> grafix.interactive leaf
    |                                        +--> grafix.export
    v                                        |
grafix.core <--------------------------------+
    ^
    |
grafix.export
```

- `core` は `api`、`export`、`interactive`、`runtime_config_loader`、`parameter_storage` に依存しない。
  また YAML/file 探索、ParamStore file mutation、Git subprocess、fsync、capture publish、出力 path
  policy を持たない。
- `export` は `api` と `interactive` に依存しない。
- `interactive` は `api` に依存しない。
- `interactive/gl`、`interactive/midi`、`interactive/parameter_gui` は composition layer の
  `interactive.runtime` に逆依存しない。
- `api` / `interactive.runtime` は必要な I/O を `runtime_config_loader` / `parameter_storage` に委譲し、
  これら top-level module だけが対応する core value/codec と filesystem policy を接続する。
- `api` が外側の実装を組み立て、公開型と内部 protocol の変換を担当する。

これらは `tests/architecture/test_dependency_boundaries.py` で検査する。

## 3. Authoring declaration と immutable catalog

### 3.1 declaration の単一路

`@primitive`、`@effect`、`@preset` は live evaluator registry を変更しない。各 decorator は
次の immutable declaration を作り、`RegistrationTarget.register()` へ渡す。

- `OpDeclaration`: evaluator、`ParameterOpSchema`、arity、cache contract、二種類の fingerprint
- `PresetDeclaration`: callable、invoker、`ParameterOpSchema`
- `ParameterOpSchema`: meta、default、parameter order、`ui_visible`

scoped target がある source/config candidate では、その target だけに登録する。通常の Python
module import では `DefaultAuthoringDefinitions` に declaration を記録する。これは module-scope
decorator の使い勝手を支える唯一の process-level authoring store であり、評価中の catalog、
cache、resource は保持しない。session は短い lock 内で得た snapshot だけを所有する。

同じ target 内の同名 declaration は拒否する。operation の `overwrite=True` は対象 name だけを
置換するが、preset の上書きは許可しない。旧 registry、互換 alias、dual-write は存在しない。

### 3.2 builtin と custom declaration

組み込み operation は `core/builtins.py` の静的 manifest が所有する。組み込み decorator は
declaration を callable に付与するだけで default authoring store へ登録せず、bootstrap が
manifest の module/attribute から回収する。このため direct import、bootstrap、stub generation の
順序で catalog の意味が変わらない。

通常 import された custom module の declaration は default authoring snapshot に含まれる。
config の `paths.preset_module_dirs` と source reload は、隔離した candidate namespace と scoped
target で operation/preset をまとめて構築し、全体が成功したときだけ新 snapshot を採用する。
失敗した candidate は default definitions と last-good generation を変更しない。

### 3.3 catalog の選択規則

- draw の外側の `G` / `E`: builtin catalog + default authoring operation snapshot
- draw の外側の `P`: default authoring preset snapshot のみ
- `RenderSession` / interactive generation: 構築時に選んだ
  `AuthoringDefinitionsSnapshot(operations, presets)`
- draw/evaluator 呼び出し中: その generation の catalog を短時間だけ `ContextVar` に束縛

config directory の preset を draw の外側の `P` が暗黙に読み込むことはない。config-scoped
preset/operation は `run()`、`RenderSession`、対応する CLI が session snapshot を作るときにだけ
有効になる。同名 preset を別 session がそれぞれ所有でき、衝突は一つの candidate catalog 内に
限定される。

公開 inspection の `G.catalog()` / `E.catalog()` / `G.describe()` / `E.describe()` は、内部
`OperationCatalogEntry` を evaluator-free な frozen `OperationInfo` へ射影して返す。
`OperationCatalogEntry` は evaluator と内部 owner を持つ evaluation 用の型であり、公開 inspection
型ではない。GUI も evaluator を保持しない `ParameterGuiCatalog` へ schema を射影する。selector は
`ParameterOpSchema` だけを合成し、架空の evaluator を evaluation catalog へ登録しない。

## 4. Geometry DAG と operation identity

`core/geometry.py` の `Geometry` は次を持つ frozen node である。

- `op` と canonical な `args`
- `inputs` の immutable tuple
- primitive/effect の exact version を示す `EvaluationOpRef`
- cache policy と、子を含む参照 operation 集合
- recipe から計算した `GeometryId`

`G.<name>(...)` は node 作成時に `EvaluationOpRef(kind, name,
evaluation_fingerprint)` を固定する。`E.<name>(...)` は step 作成時に `EffectStepRef` として
evaluation fingerprint と schema fingerprint の両方を固定し、適用時にも同じ entry で
parameter 解決と node 化を行う。後から同名 declaration が置換されても、旧 schema と新 evaluator
を混ぜない。

`GeometryId` は operation ref、入力 GeometryId、canonical args を推移的に含む。realization 前に
全 ref を session catalog と照合し、一致しなければ `CatalogMismatchError` とする。同名の最新
operation への暗黙 fallback は行わない。

operation の identity は process counter、object id、absolute path、import 順ではなく canonical
declaration signature から決定する。

- `EvaluationSpecFingerprint`: evaluator、arity、cache/external-dependency contract
- `ParameterSchemaFingerprint`: GUI/selector 用 schema

schema だけの変更は geometry cache を失効させず、使用していない operation の変更も別 DAG の
identityへ波及しない。`cache_policy="none"` の動的 operation は明示 `version` が必須で、CPU/GPU
content cache を迂回する。

## 5. 評価 context、cache、resource ownership

### 5.1 typed cache identity

`EvaluationContext` は一 generation の `OperationCatalog`、quality (`draft` / `final`)、
`EvaluationConfig` を固定し、`EvaluationFingerprint` を計算する。現行 `EvaluationConfig` が持つのは
Geometry 評価へ影響する `font_dirs` だけである。UI、MIDI、output path などを含む full
`RuntimeConfig` は application/authoring 境界に留まり、DAG evaluator へ渡さない。YAML と探索 policy
は `grafix.runtime_config_loader`、immutable value と pure mapping validation は
`grafix.core.runtime_config` が所有する。変更され得る font file 自体は context へ埋め込まず、lookup
時点の `ExternalDependenciesFingerprint` として分離する。

CPU cache、inflight、`RealizedLayer`、GPU cache は同じ `GeometryCacheKey` を使う。

```text
GeometryCacheKey =
    GeometryId
  + EvaluationFingerprint(quality, EvaluationConfig)
  + ExternalDependenciesFingerprint
  + uncached generation (cache_policy="none" のみ)
```

これにより draft/final、evaluation config A/B、operation A/B、font A/B を同一 process/cache store
上で安全に区別する。UI/output だけが異なる full config は geometry key を不必要に分断しない。

### 5.2 owner と close 順

`RealizeCacheStore` は byte/entry 上限を持つ LRU である。`RealizeSession` の ownership は
dependency ごとに constructor 引数で決まる。

- 明示注入された `EvaluationResources` / `RealizeCacheStore` は borrowed であり、session は閉じない。
- 省略された `resources` / `cache_store` は session-owned であり、`close()` が
  `EvaluationResources -> RealizeCacheStore` の順に閉じる。
- `EvaluationContext` は immutable value であり close 対象ではない。
- 実行中の realization がある状態で `close()` された場合、最後の caller が終了するまで owned
  dependency の close を遅延する。

constructor、context manager body、close のいずれで `BaseException` が発生しても、owned dependency
は後続 cleanup まで試し、最初の error を保持する。

headless の `RenderSession` は次を所有する。

```text
RenderSession
  ├─ AuthoringDefinitionsSnapshot / EvaluationContext(final, EvaluationConfig)
  ├─ ParamStore / StyleResolver
  ├─ RealizeCacheStore
  ├─ EvaluationResources
  └─ RealizeSession (explicit dependency borrower)
```

親 owner の終了順は `RealizeSession -> EvaluationResources -> RealizeCacheStore` である。interactive の
`SceneRunner` は generation をまたぐ一つの `RealizeCacheStore` を所有し、各 generation は
一つの `EvaluationResources` と draft/final の `RealizeSession` を持つ。reload では新 generation
を完成させてから交換し、旧子 session と resource を閉じる。cache store は `SceneRunner` 終了時
だけ閉じる。

### 5.3 external asset と font

external-dependency preflight は cache lookup の直前に fingerprint と評価用 lease を同じ bytes
から作る。evaluator はその lease だけを使い、preflight 後に path を再解決・再 open しない。

text primitive では `FontAssetFingerprint` が canonical path、face index、file stat、content
digest を含む。`EvaluationResources.fonts` 配下の `FontResources` / `TextRenderer` が TTFont と
glyph outline の bounded LRU を所有し、eviction、`clear()`、`close()` で解放する。同じ session
中に font file が置換された場合も、次の lookup が新 fingerprint と geometry を正式に観測する。

## 6. Parameter domain と更新規則

### 6.1 frame snapshot

`parameter_context(store, cc_snapshot)` は一 frame の読み取り境界である。

1. `ParamStore` から immutable `ParamSnapshot` を固定する。
2. `G` / `E` / `P` / Layer style が base、UI、MIDI を解決し、`FrameParamsBuffer` に観測を積む。
3. context 終了時に effect topology、label、parameter record を store へ merge する。

値の優先順位は `MIDI > UI > CODE`。量子化は resolver で一度だけ行い、DAG signature と実計算に
同じ値を使う。明示 kwargs は初期状態で code を、省略 kwargs は GUI 値を優先する。

### 6.2 query、command、rollback

`ParamStore` が論理 state、revision、history integration を所有する。API/interactive は private
container を読まず、`ParamRuntimeView`、`frozenset`、copy された mapping などの immutable query
を使う。`ParamRuntimeView` は作成時に三つの runtime mapping を浅く copy して固定するため、後続
frame の store mutation を既存 view が観測しない。key/value/source は canonical immutable value
なので deep copy は行わない。変更は `ParameterEdit` / `apply_parameter_edits()`、collapse、variation、
effect order、MIDI などの狭い command を通す。

variation、history、snapshot slot が保存する GUI-owned state は frozen
`ParameterAdjustmentSnapshot` に統一する。これは `ParamStateSnapshot` / `ParamMeta`、collapse、effect
order、topology signature の immutable tuple だけを持ち、live `ParamState` や mutable mapping を
露出しない。capture/apply と atomic revision 更新は `ParamStore` が所有する。旧
`ParamStoreMemento` は存在しない。

- no-op command は revision/history/observer を進めない。
- 一つの command の複数変更は revision/history/observer を一回に集約する。
- GUI table は `TableRenderInput -> TableEdits` の pure boundary とし、renderer は store を変更しない。
- `store_bridge` と controller が edit intent を command として commit する。

variation batch の一時評価は `ParamStore.begin_transient_rollback()` だけを使う。
`ParamStoreRollback` は owner-bound、one-shot、opaque であり、正常・例外終了の双方で開始時の
論理 state、revision/runtime counter、change log を exact restore する。rollback は observer や
history event を発生させず、scope 中または開始前の derived cache は再利用しない。

parameter の prune/final save は operation/preset module を探索せず、application 境界から渡された
known-operation schema snapshot を使う。direct writer と session finalization は別責務である。

### 6.3 file read、recovery、commit

`grafix.parameter_storage` が ParamStore の filesystem policy を所有し、三つの経路を混ぜない。

- `read_param_store()` は missing/loaded/partial/invalid を `ParamStoreReadResult` で返し、原本を
  rename、write、unlink しない。
- `recover_primary_param_store()` / `recover_param_store_session()` は明示的に recovery journal を選び、
  破損 file を quarantine し得る。
- `write_param_store()` / `write_param_store_recovery()` は atomic write を行い、
  `finalize_parameter_session()` は既知 schema で prune した primary commit の成功後だけ recovery を
  削除する。

codec と `ParamStore` domain は `core.parameters` に残り、filesystem mutation は持たない。

## 7. 共通 scene pipeline と headless render

`core/pipeline.py:realize_scene()` は interactive と headless が共有する境界である。

1. generation の operation/preset/full runtime config を draw/authoring 区間だけ束縛し、evaluator には
   session の quality / `EvaluationConfig` だけを見せる。
2. `draw(t)` の `SceneItem` を `normalize_scene()` で Layer 列へする。再帰 container として受理するのは
   `list` / `tuple` だけで、`str`、`bytes`、custom `Sequence`、set、generator は拒否する。
3. global/Layer style を解決する。
4. scene aggregate transaction 内で各 Geometry を評価する。
5. resource limit を満たした場合だけ新 cache entry を commitし、`RealizedLayer` を返す。

`RenderSession` は config、authoring definitions、ParamStore、final quality の evaluation context、
cache/resource を構築時に固定する。`render(t)` は immutable `Frame` を返すだけで filesystem I/O を
行わない。複数 frame では一つの `RenderSession` を使って cache/resource を再利用し、単発の
`grafix.render()` は内部で session を作って必ず閉じる。

公開 property は `options`、`param_store`、`config`、`runtime_limits`、`metadata` だけである。
`OperationCatalog`、`EvaluationContext`、`RealizeSession`、cache/resource child owner は公開せず、
caller が lifetime や evaluator capability を横取りできないようにする。

`grafix.export(frame, path)` は公開 `Frame` を export-side `CaptureFrame` contract へ変換し、
encode/publish を実行する。render と保存を分けるため、同じ frame を複数形式へ安全に出力できる。

## 8. Interactive composition

`api.runner.run()` は public 引数 validation、effective `RuntimeConfig` / `RenderOptions` の確定、
private `_InteractiveApplication` の実行だけを行う。`_InteractiveApplication` が authoring definitions
を一度確定し、同じ snapshot を `ParameterSession`、`DrawWindowSystem`、`SceneRunner`、GUI catalog
へ渡す。部分構築に失敗した場合も、取得済み resource だけを逆順に閉じ、cleanup error より root
error を優先する。主な owner は次の通り。

- `_InteractiveApplication`: workspace、parameter、MIDI、DWS、GUI、activation、window loop の lifetime
- `ParameterSession`: load/recovery、history、autosave、known-operation schema、終了時 persist
- `WorkspaceWindowController`: 二 window の配置、visibility、workspace persistence
- `DrawWindowSystem`: renderer、SceneRunner、input/reload と frame call order の配線
- `SceneRunner`: sync/mp draw、generation、draft/final evaluation、last-good scene。background 実装は
  constructor の `mp_draw_factory` から private `_MpDrawClient` Protocol として受け取り、initial/reload
  の両方を同じ factory で作る
- `MpDraw`: process/Queue/restart/close。ACK、known revision、latest-task、stale-result の副作用を持たない
  transition は process/Queue/thread/clock を所有しない `_MpDrawState` に委譲する
- `PresentedFrameState`: last-good layers/t、snapshot revision/frame ID、fresh serial、preview/capture
  snapshot、provenance token の accept/prepare/publish
- `CaptureQueue`: immutable capture intent、件数/geometry-byte admission、worker drain
- `RecordingSession`: transport pause/restore、window size、video staging/publish lifecycle
- `ParameterGUI`: backend frame と panel/controller の順序
- `VariationController` / `RangeEditController` / `ParameterGuiSessionState`: GUI domain mutation
- `WidgetSessionState`: GUI instance ごとの font/choice filter と snippet popup text/focus。widgets/table は
  この state を明示的に受け取り、module-global mutable state を持たない

`MultiWindowLoop` が preview と Inspector を一つの event loop で駆動する。GUI 変更は次 frame の
parameter snapshot に反映され、実行中 frame の値は変えない。

`PygletImguiBackend` は ImGui context、renderer、font texture と
`sync IO -> new_frame -> render` の順序を所有する。`DrawRenderer` は ModernGL context、framebuffer、
viewport、RGB readback と GPU cache を所有し、runtime が `.ctx` へ到達することはない。

diagnostics、transport、telemetry の immutable/Protocol contract は `interactive/` 直下に置き、
GL/MIDI/GUI leaf が runtime concrete class に依存しない。

source reload は entry source と、静的な package-relative import で到達する local helper の bytes
だけを candidate generation として隔離実行し、draw signature、declaration snapshot、worker startup
の成功後にだけ交換する。到達しない `.py` は監視せず、同じ directory の helper を absolute import
することも許さない。失敗時は last-good callable、catalog、worker、frame、ParamStore を維持する。

## 9. Capture / export infrastructure

core に残すのは immutable provenance/manifest value と codec だけである。source/Git/package の
収集、output path policy、staging、fsync、publish/rollback は `export` が所有する。

`CaptureService` は完成した frame snapshot を形式別 encoder へ渡す。`CaptureStaging` が private
sibling directory と work path を所有し、`publish_capture_generation()` が artifact、manifest、
layer-split G-code family を一 generation として no-clobber publish する。allocation 後の late
collision は完成済み staging を再 encode せず、別 version path で bounded retry する。失敗時は
今回の inode だけを rollback する。

interactive の PNG/G-code は `ExportJobSystem` の長寿命 spawn worker を使う。親 process の
`CaptureQueue` が in-flight 1 件と bounded FIFO/aggregate geometry byte を管理し、満杯時は明示的に
拒否する。provenance は keypress 時点の frame とともに親で固定し、worker は Git/config/source を
再探索しない。

### G-code の stroke-order contract

G-code encoder は clipping 前の input polyline 順を semantic boundary として保持する。

- 頂点数、閉曲線らしさ、producer 順から face/group を推測しない。
- `optimize_travel` は一つの input polyline から clipping で生じた fragment の順序・向きだけを
  最適化する。
- `bridge_draw_distance` は異なる input polyline 間に pen-down bridge を追加しない。

cross-polyline optimization が必要なら、意味を持つ export-side grouping artifact を別途設計する。
core Geometry へ推測 metadata を追加して補わない。

## 10. Geometry kernel

effect 間で共有する数値処理は `core/geometry_kernels/` に領域別に置く。

- `packed.py`: canonical empty representation と `pack_polylines`
- `planar.py`: 平面基底/PCA/ring
- `grid.py`: bbox と `max_points` resource budget からの副作用のない grid planning
- `raster.py` / `marching.py`: raster/SDF/contour
- `resample.py`: polyline resampling/filter support

kernel は effect に依存せず、import graph は acyclic である。`plan_grid_from_bbox()` が pure
`GridPlanResult` を返し、`operation_diagnostics.grid_spec_from_bbox_with_diagnostic()` がそれを
`OperationDiagnostic` と `GridSpec` へ変換する唯一の adapter である。metaball、growth、
reaction-diffusion、isocontour はこの adapter を共有する。grid budget の語彙は cell ではなく、実際に
確保する `point_count` / `max_points` に統一する。旧 `effects/util.py` と packed helper の重複実装や
re-export shim は存在しない。

## 11. Validation ownership

同じ値を producer と DTO の両方で無条件に再検証せず、信頼境界ごとに owner を決める。

| 境界 | validation owner |
|---|---|
| public API | その public method/function。暗黙 coercion を行わず error type/message を固定する |
| filesystem / IPC deserialize | decoder/receiver。型、範囲、payload 排他、schema を再検証する |
| custom evaluator output | evaluation boundary。shape/dtype/resource contract を検証する |
| trusted private DTO | producer が canonical 化し、DTO は残る意味的 invariant だけを持つ |

`MpDraw.submit()` が `_DrawTask` の public snapshot/time/revision/epoch/quality validation を所有し、private
`_DrawTask` は同じ field を再検証しない。export request の format/snapshot/G-code/output-size invariant
は `_validate_export_request()` 一箇所に置き、submit と `ExportJob` が同じ定義を使う。IPC 受信や file
decode の防御は trusted-path 簡略化の対象にしない。

## 12. Benchmark harness の依存方向

benchmark harness は `src/grafix/devtools/benchmarks/` 内で case 定義、収集、計測、workload、実行入口を
分離する。次の矢印は左の module が右の module に依存する向きであり、逆依存と循環を許さない。

```text
definition ------------------------------> schema
metrics ---------------------------------> schema
workload providers ----------------------> definition / metrics / schema
catalog ---------------------------------> definition / workload providers
executor --------------------------------> definition / metrics / schema
runner ----------------------------------> catalog / executor / definition / schema
```

- `definition.py`: immutable `CaseDefinition`、source fingerprint、case 定義 helper
- `metrics.py`: exact checksum、typed metric、warm/cold aggregation
- workload provider: 対象 subsystem の setup/workload/postprocess。catalog/runner/executor を知らない
- `catalog.py`: provider 収集、重複拒否、stable ordering、suite/case selection
- `executor.py`: in-process/fresh-process measurement、calibration、timeout、child kill/reap
- `runner.py`: catalog と executor の composition、および child entrypoint だけ

workload layer 内の再利用は public helper に限定する。現行の許可辺は
`interactive_scenario_benchmark -> parameter_hotpath_benchmark / renderer_benchmark` と
`parameter_edit_benchmark -> parameter_hotpath_benchmark` だけであり、private symbol 参照を禁止する。
`runner.py` の公開 surface は `run_case_isolated` だけである。親は definition を executor へ渡して
fresh child を起動し、child は catalog から case ID を解決して executor へ戻す。workload、metrics、
process supervision の実装を runner に置かず、旧 private symbol の re-export shim も置かない。
`tests/architecture/test_benchmark_dependency_boundaries.py` が非循環性、禁止依存、runner の公開 surface
と composition-only の大きさを検査する。

## 13. Test taxonomy

- marker なしを unit とし、multiprocessing/subprocess/実 resource lifecycle を要求しない local test を
  実行する。
- `integration` は multiprocessing、subprocess、または実 resource lifecycle を使う test に付ける。
- `e2e` は公開 CLI/application を entrypoint から往復する test に付ける。
- performance は pytest marker に混ぜず、決定的 benchmark CLI の smoke/full profile で検査する。

CI の分割は `pytest -m "not integration and not e2e"`、`pytest -m integration`、`pytest -m e2e` であり、
詳細は `docs/agent_docs/testing.md` を正本とする。

## 14. 変更時の判断基準

- semantic state を process-global cache/service locator に置かず、snapshot と owner を明示する。
- mutable builder を draw/evaluation へ渡さない。
- catalog mismatch や invalid config を「最新値」への fallback で隠さない。
- coordinator へ path allocation、encode、domain mutation、platform policy を戻さない。
- compatibility wrapper、deprecated alias、旧 import path の re-export shimを追加しない。
- 公開 API の破壊的変更では source、tests、stub、README、migration note を同じ change set で更新する。

図による overview は `docs/architecture_visualization.md`、利用者向けの更新手順は
`docs/migration_2026-07-22.md` を参照する。
