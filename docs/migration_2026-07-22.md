# Architecture/catalog migration notes (2026-07-22)

この変更は operation/preset registration、evaluation cache、resource ownership、ParamStore、
interactive/export の責務境界を正規実装へ一本化する破壊的変更である。旧実装を残す
compatibility wrapper、deprecated alias、re-export shim、dual-write は追加していない。

既存 sketch の通常の `G` / `E` / `L` / `P` 記法は維持する。内部 API を使う extension、tool、
test は以下の手順で更新する。

## 1. Operation / preset registration

削除した module:

- `grafix.core.op_registry`
- `grafix.core.primitive_registry`
- `grafix.core.effect_registry`
- `grafix.core.preset_registry`

更新方法:

1. decorator は公開入口から importする。

   ```python
   from grafix import effect, preset, primitive
   ```

2. operation の列挙・説明は `G.catalog()` / `E.catalog()` と
   `G.describe(name)` / `E.describe(name)` を使う。内部 consumer が snapshot を必要とする場合は
   `OperationCatalog` / `PresetCatalog` を composition root から受け取る。
3. evaluator と GUI/public inspection schema を一つの旧 `OpSpec` として扱わない。
   evaluator contract は内部の `OpDeclaration` / `OperationCatalogEntry`、公開 inspection は
   evaluator-free `OperationInfo`、parameter 表示は `ParameterOpSchema`、GUI は evaluator-free
   `ParameterGuiCatalog` を使う。
4. source/config loader は registry object を差し替えない。`RegistrationTarget` と
   `registration_scope()` で candidate を構築し、`AuthoringDefinitionsSnapshot` を採用する。

通常 module-scope の decorator は `DefaultAuthoringDefinitions` に immutable declaration を記録する。
`run()` / `RenderSession` の構築後に `overwrite=True` で同名 operation を宣言しても、既存 session
は旧 snapshot のままで、新しい session だけが新 declaration を見る。既存 Geometry を別 version
の catalog で評価すると明示的な `CatalogMismatchError` になり、同名最新 evaluator へ fallback
しない。

組み込み operation を追加する場合は decorator だけでなく `core/builtins.py` の manifest に
module、callable attribute、evaluator ABI version を追加する。旧 lazy registry helper や import
reload で復旧する経路はない。

公開 catalog consumer は core entry を import せず、返り値を `OperationInfo` として扱う。

```python
# 旧: evaluator/owner を持つ内部型を公開 inspection として利用
from grafix import G
from grafix.core.operation_catalog import OperationCatalogEntry

entry: OperationCatalogEntry = G.describe("circle")

# 新: public evaluator-free value
from grafix import G, OperationInfo

entry: OperationInfo = G.describe("circle")
```

`OperationCatalogEntry` を再公開する alias/shim はない。

## 2. Custom operation の cache contract

既定の `cache_policy="content"` は、code、default/closure、decorator option、引数から結果の意味を
決定できる operation 用である。canonical fingerprint を作れない動的 dependency は登録時に
拒否される。

動的 process state を意図的に読む operation は明示的に cache を外す。

```python
from grafix import primitive


@primitive(cache_policy="none", version="my-dynamic-source-v1")
def dynamic_source():
    ...
```

`cache_policy="none"` では空でない stable `version` が必須である。object id、absolute path、
process counter への fallback はない。外部 file を content cache の一部として扱う primitive は、
`external_dependency_hook` が fingerprint と evaluator 用 lease を同じ bytes から返す設計にする。

## 3. Preset と config-scoped authoring

旧挙動では、draw の外側で `P.<name>` に触れたとき config の preset directory を暗黙 load できた。
この挙動は削除した。

- 通常 import 済みの `@preset` は default authoring snapshot に入り、draw の外側の `P` と今後の
  session から使える。
- `paths.preset_module_dirs` の module は `run()`、`RenderSession`、対応 CLI が effective config
  から session catalog を作るときだけ load する。
- config-scoped preset を直接評価する tool/test は、config から explicit
  `AuthoringDefinitionsSnapshot` を作り、その preset catalog を束縛するか session を使う。
- duplicate/import failure は candidate 全体を失敗させ、default definitions や別 session を
  変更しない。
- config/source 管理 module の local helper は package-relative import を使う。candidate generation
  は隔離 namespace で実行され、canonical な live module registry として残らない。
- source reload が snapshot/監視するのは entry source から静的な package-relative import で
  到達する helper だけである。同じ directory の helper への absolute import は拒否し、到達しない
  `.py` の変更では reload しない。到達する helper の構文失敗、欠落、削除は last-good generation
  を維持したまま監視し、修復後に再試行する。

`@preset` の呼び出し identity は従来どおり
`P(name=..., key=..., instance_key=..., shared=...).foo(...)` に置く。`activate` は wrapper が追加する。

## 4. Runtime config と EvaluationConfig

process-global の mutable config path/cache/scope は削除した。immutable value と pure mapping validation
は `grafix.core.runtime_config`、YAML/package resource、CWD/HOME 探索、merge、path 解決は
`grafix.runtime_config_loader` が所有する。

```python
from grafix.runtime_config_loader import load_runtime_config

config = load_runtime_config(".grafix/config.yaml")
```

旧 core loader import は削除しており、re-export shim はない。

```python
# 旧: ImportError
from grafix.core.runtime_config import load_runtime_config

# 新
from grafix.runtime_config_loader import load_runtime_config
```

application 境界から `RuntimeConfig` を明示的に渡す。`run()` / `RenderSession` では
`config_path=` と `config=` を同時指定できない。短い draw/authoring 呼び出し中に
`bind_runtime_config()` を使うことはできるが、constructor から `close()` まで process state を
差し替える lifetime scope として使わない。

output path、worker、source reload、capture など application subsystem は session が確定した同じ full
config を受け取る。font resolver はそこから射影した `EvaluationConfig` だけを受け取る。別 config の
session は同一 process/thread 内で共存できる。

DAG evaluator の `EvaluationContext.config` は full `RuntimeConfig` ではなく、評価結果へ影響する最小の
`EvaluationConfig` である。現行 field は `font_dirs` だけで、UI、MIDI、output path は evaluator/cache
fingerprint に入らない。low-level context を組み立てる extension は明示的に射影する。

```python
from grafix.core.evaluation_config import EvaluationConfig
from grafix.core.evaluation_context import EvaluationContext

evaluation = EvaluationContext(
    catalog=catalog,
    quality="final",
    config=EvaluationConfig(font_dirs=runtime_config.font_dirs),
)
```

`EvaluationContext(config=runtime_config)` を受け付ける互換変換はない。

## 5. Geometry と cache key

旧 `(GeometryId, registry_revision)` tuple key は削除した。`Geometry` は exact
`EvaluationOpRef` を持ち、`GeometryId` が使用 operation fingerprint を推移的に含む。

cache consumer は `GeometryCacheKey` をそのまま伝播する。

```text
GeometryCacheKey(
    geometry_id,
    evaluation=EvaluationFingerprint(quality, EvaluationConfig),
    external_dependencies=ExternalDependenciesFingerprint(...),
    uncached_generation=None | int,
)
```

CPU cache、inflight、`RealizedLayer`、renderer GPU cache で独自 tuple/revision key を再構築しない。
quality、evaluation-semantic config、使用 operation、external asset が異なる結果は別 key になる。
UI/output だけが異なる full config、未使用 operation、schema-only の変更で全 geometry cache を
捨てない。

## 6. RealizeSession と resource ownership

`RealizeSession` の ownership は dependency ごとに constructor 引数で決まる。

- 明示注入した `EvaluationResources` / `RealizeCacheStore` は borrowed。caller が閉じる。
- 省略した `resources` / `cache_store` は session-owned。`RealizeSession.close()` が閉じる。
- 二つの引数は独立に判定する。片方だけを注入した場合、注入側は借用、省略側は所有となる。
- `EvaluationContext` は immutable value であり close 対象ではない。省略時は current context から作る。

session-owned dependency の close 順は次のとおり。

```text
EvaluationResources -> RealizeCacheStore
```

`close()` は新規評価を禁止し、実行中の realization がある場合は最後の caller が終了するまで owned
cleanup を遅延する。constructor partial failure、context manager body、close の `BaseException` でも
後続 cleanup を試し、最初の error を保持する。borrowed dependency はいずれの経路でも閉じない。

通常の headless code は `RenderSession` を使えばこの ownership を直接扱う必要はない。
interactive extension は generation ごとの resources/draft-final child session と、generation 外の
shared cache store を混同しない。これらの composition owner は dependency を明示注入するため、子
`RealizeSession` は borrower であり、親の終了順は
`RealizeSession -> EvaluationResources -> RealizeCacheStore` のままである。

module/class-global の text/font cache は削除した。font resource は `EvaluationResources.fonts` が
bounded LRU として所有し、同一 path の内容差替えも後続 lookup で新 fingerprint として観測する。

`RenderSession` の公開 property は `options`、`param_store`、`config`、`runtime_limits`、`metadata` の
allowlist に限定した。旧 extension が `style_resolver`、`realize_session`、`evaluation_context`、
`definitions`、`evaluation_resources`、`cache_store` を取得していた場合は、child owner を外から
close/mutate せず、必要な dependency を extension 自身の composition root で組み立てる。互換
property はない。

`SceneItem` の再帰 container は runtime/type contract とも `list` / `tuple` だけに統一した。

```python
# 旧: custom Sequence / generator は受理されない
import itertools

return itertools.chain((layer_a,), (layer_b,))

# 新
return [layer_a, layer_b]
```

`str`、`bytes`、custom `Sequence`、set、generator を list へ暗黙変換する shim はない。

## 7. ParamStore mutation と rollback

API/interactive から次のような private/live state access を削除する。

- `vars(store)`
- `_runtime_ref()` / `_variations_ref()` / `_collapsed_headers_ref()`
- `_snapshot_cache` / `_touch()`
- private container を別名で返す accessor

読み取りは `runtime_view()`、`collapsed_headers()`、variation/effect-order の snapshot/query を使う。
`runtime_view()` は生成時に三つの runtime mapping を浅く copy して固定する。既存 view は後続 frame
の mutation を観測せず、mapping の key/value/source は canonical immutable value なので deep copy
はしない。
更新は `ParameterEdit` / `apply_parameter_edits()` と各 domain の狭い command を使う。table renderer
には immutable `TableRenderInput` を渡し、戻った `TableEdits` を `store_bridge` で commit する。

variation batch など、処理後に必ず全 state を戻す用途は
`with store.begin_transient_rollback():` だけを使う。独自 memento、`vars(store)` copy、manual revision
書戻しは削除する。rollback は state/revision/runtime counter を exact restoreし、history/observer
event を発生させず、derived cache を無効化する。

history、snapshot slot、variation が保存する GUI-owned state は
`ParameterAdjustmentSnapshot` に統一した。snapshot は frozen state/meta、collapse、effect order、
topology signature だけを保持し、live `ParamState` / mutable mapping を公開しない。capture/apply は
`ParamStore.capture_adjustment_snapshot()` / `ParamStore.apply_adjustment_snapshot()` を使う。

```python
# 旧: ImportError。private representation を複製する memento は廃止
from grafix.core.parameters.memento import ParamStoreMemento

# 新
snapshot = store.capture_adjustment_snapshot()
store.apply_adjustment_snapshot(snapshot)
```

`ParamStoreMemento` の alias/re-export はない。

### 7.1 ParamStore file read、recovery、commit

旧 `grafix.core.parameters.persistence` は削除し、filesystem policy を
`grafix.parameter_storage` へ移した。

```python
from grafix.parameter_storage import (
    finalize_parameter_session,
    read_param_store,
    recover_param_store_session,
    write_param_store,
)

read = read_param_store(path)          # 原本を変更しない
store = recover_param_store_session(path)  # 明示 recovery。quarantine し得る
write_param_store(store, path)         # direct atomic commit
finalize_parameter_session(store, path, known_operations=schema)
```

- `read_param_store()` は `ParamStoreReadResult` を返し、rename/write/unlink を行わない。
- `recover_primary_param_store()` / `recover_param_store_session()` だけが破損 file の quarantine と
  recovery journal 選択を行う。
- `write_param_store*()` は atomic writer、`finalize_parameter_session()` は既知 schema で prune した
  primary commit 成功後だけ recovery journal を削除する。

旧 `load_param_store*` / `save_param_store*` / `finalize_param_store_session` 名や core import path を保つ
shim はない。read のつもりで recover を呼ばず、mutation policy を呼び出し側で明示する。

## 8. Interactive contract と composition owner

削除した旧 import path:

- `grafix.interactive.runtime.diagnostics` -> `grafix.interactive.diagnostics`
- `grafix.interactive.runtime.frame_clock` -> `grafix.interactive.transport`

re-export shim はない。telemetry の immutable snapshot/Protocol は
`grafix.interactive.telemetry` を使う。

runtime/GUI extension は次の owner を尊重する。

- `_InteractiveApplication`: `run()` validation/config/options 確定後の workspace、parameter、MIDI、DWS、
  GUI、activation、window loop lifetime。部分構築時も取得済み resource を逆順に閉じ、root error を保持
- `PygletImguiBackend`: ImGui context、IO sync、new frame、render、font texture、close
- `DrawRenderer`: ModernGL context、framebuffer/viewport、RGB24 readback、GPU cache
- `PresentedFrameState`: 表示 layers/t、revision/frame ID、fresh serial、preview/capture snapshot、
  provenance token の accept/prepare/publish
- `CaptureQueue`: capture admission/FIFO/drain
- `RecordingSession`: transport/window restore と video staging/publish
- `WorkspaceWindowController`: multi-screen placement/visibility/persistence
- `ParameterSession`: parameter load/recovery/autosave/finalization
- `WidgetSessionState`: GUI instance ごとの font/choice filter と snippet popup text/focus

renderer の `.ctx`、ParamStore private state、platform screen API に coordinator から到達しない。

`api.runner.run()` は public validation、effective `RuntimeConfig` / `RenderOptions`、private
`_InteractiveApplication.run()` への委譲だけになった。extension が `run()` の nested closure や cleanup
list を monkeypatch する経路はない。

`SceneRunner` の MP backend fake は private field へ代入せず、constructor の `mp_draw_factory` から
private `_MpDrawClient` Protocol 準拠 object を渡す。

```python
# 旧: private state injection
runner._mp_draw = fake

# 新: initial/reload の両方が同じ factory を使う
runner = SceneRunner(..., mp_draw_factory=lambda draw, **kwargs: fake)
```

既定 factory だけが concrete `MpDraw` を作る。`MpDraw` 内部では `_MpDrawState` が ACK/known revision、
latest-task、stale-result transition を持ち、process/Queue/restart/close は `MpDraw` に残る。private field
互換 setter はない。

GUI extension は widgets/table 呼び出しへ `ParameterGuiSessionState.widgets` を明示的に渡す。旧
module-global font/choice filter や snippet popup state、global reset API はないため、session close 後の
state を新 session へ引き継がない。

## 9. Capture / output path infrastructure

削除・移動した旧 core infrastructure:

- `grafix.core.output_paths` -> `grafix.export.output_paths`
- provenance collection -> `grafix.export.capture_provenance`
- staging / atomic publish -> `grafix.export.capture_staging` /
  `grafix.export.capture_publish`

core には immutable provenance/manifest value と codec だけを残す。format extension は
`CaptureService` の private staging -> encode -> publish lifecycle を使い、final path へ直接 encode
しない。artifact/manifest/split-G-code family の no-clobber判定、late-collision retry、rollback を
形式ごとに再実装しない。

## 10. Geometry kernel import

`grafix.core.effects.util` は削除した。互換 re-export はない。

| 旧用途 | 新しい正規 module |
|---|---|
| packed geometry / empty | `grafix.core.geometry_kernels.packed` |
| planar frame / PCA / ring | `grafix.core.geometry_kernels.planar` |
| grid planning | `grafix.core.geometry_kernels.grid` |
| raster / EDT / SDF | `grafix.core.geometry_kernels.raster` |
| marching contour | `grafix.core.geometry_kernels.marching` |
| resample/filter helper | `grafix.core.geometry_kernels.resample` |

effect module から sibling effect を import せず、数値 kernel は effect/diagnostics に依存させない。

grid planning は `geometry_kernels.grid.plan_grid_from_bbox()` が副作用のない `GridPlanResult` を返し、
`core.operation_diagnostics.grid_spec_from_bbox_with_diagnostic()` だけが `OperationDiagnostic` を emit して
`GridSpec | None` へ変換する。metaball、growth、reaction-diffusion、isocontour のローカル
`_grid_spec_from_bbox` clone は削除した。

budget 語彙は実際に確保する点数へ合わせ、`DEFAULT_MAX_GRID_CELLS` / `cell_count` / `max_cells` を
`DEFAULT_MAX_GRID_POINTS` / `point_count` / `max_points` へ破壊的に変更した。旧名の alias はない。

## 11. G-code stroke order

異なる input polyline 間を `optimize_travel=True` で並べ替えたり反転したりする旧挙動を削除した。
`bridge_draw_distance` も polyline 境界を越えない。

- producer 順、頂点数、closed-like shape から face/group を推測しない。
- optimization/bridge は同じ source polyline が clipping で分かれた fragment 間だけに適用する。
- cross-polyline optimization に依存する workflow は、呼び出し側で望む stroke 順に polyline を
  構築する。暗黙 heuristic を復活させない。

## 12. Benchmark harness import と source identity

旧 `grafix.devtools.benchmarks.runner` に混在していた定義、catalog、metric、計測、workload は正規
owner へ移した。`runner.py` は catalog と executor の composition/child entrypoint だけで、公開
symbol は `run_case_isolated` のみである。

| 旧 runner から使っていた責務 | 新しい正規 module |
|---|---|
| `CaseDefinition`, `define_case`, `make_case_spec`, scaled helper | `grafix.devtools.benchmarks.definition` |
| `case_definitions`, `definition_for_case`, `select_case_definitions` | `grafix.devtools.benchmarks.catalog` |
| checksum、typed metric、aggregation | `grafix.devtools.benchmarks.metrics` |
| in-process/isolated execution、calibration、child request | `grafix.devtools.benchmarks.executor` |
| subsystem setup/workload/postprocess | 対応する `grafix.devtools.benchmarks.*_benchmark` provider |
| isolated public composition | `grafix.devtools.benchmarks.runner:run_case_isolated` |

旧 `runner._setup*`、`runner._workload*`、`runner._measure*`、catalog/metric symbol の import や
monkeypatch は canonical owner へ移す。runner からの compatibility re-export、private alias、shim は
ない。executor は catalog/workload を importせず、provider は catalog/executor/runner を importしない。
provider間の再利用はarchitecture allowlistにある一方向のpublic helperだけに限定し、旧system/private
helperをsiblingからimportする形へ戻さない。

責務移動により implementation の `module.qualname` と source が変わったため、162 case 全件で
`CaseSpec.source_sha256` と `compatibility_key` が意図的に変わる。workload semantics の変更ではないが、
旧/new run の直接 compare は `case compatibility key differs`（exit 2）になる。これを
`--allow-incompatible` で隠さない。移行時の同値性は、case ID/order、status、checksum、hard contract、
metric identity、schema/CLI contract を Phase 9 snapshot と別に比較する。

fresh-process executor は `communicate()` の timeout だけでなく全 `BaseException` で process group へ
`SIGKILL` を試みる。`killpg` 自体が失敗した場合は child への `process.kill()` に fallbackし、bounded
な二回目の `communicate()`、さらに必要なら bounded `wait()` で reapを試す。cleanup失敗は元errorの
noteへ残し、cleanup errorで元の`BaseException`を置換しない。custom toolがexecutorを包む場合も、
この cleanupを迂回して独自`Popen` lifecycleを再実装しない。

## 13. Validation ownership

validation は次の信頼境界へ集中した。

| 境界 | owner |
|---|---|
| public API | public method/function |
| filesystem / IPC deserialize | decoder/receiver |
| custom evaluator output | evaluation boundary |
| trusted private DTO | producer の canonicalization + DTO 固有の意味的 invariant |

`MpDraw.submit()` が time/revision/snapshot/epoch/quality を検証して `_DrawTask` を作るため、private
`_DrawTask.__post_init__` で同じ field を再検証しない。export の format/snapshot/G-code/output-size
invariant は `_validate_export_request()` 一つを submit と `ExportJob` が共有する。IPC message、file
decode、payload 排他など untrusted boundary の検査は削除しない。

private DTO constructor の重複検査に依存する extension は、DTO を直接作らず public producer を使う。
旧 validation wrapper/shim は追加していない。

## 14. Test taxonomy

- marker なし: unit。process/subprocess/実 resource を使わない domain/coordinator test
- `integration`: multiprocessing、subprocess、または実 resource lifecycle
- `e2e`: 公開 CLI/application を entrypoint から往復
- performance: pytest marker ではなく決定的 benchmark CLI

```bash
pytest -q -m "not integration and not e2e"
pytest -q -m integration
pytest -q -m e2e
python -m grafix benchmark run --suite smoke --profile smoke
```

marker の正本は `pyproject.toml`、運用と CI lane は `docs/agent_docs/testing.md` と
`.github/workflows/ci.yml`。重い test を directory 名だけで分類しない。

## 15. Stub と検証

custom operation/preset を変更したら current environment で fresh subprocess の stub を生成する。

```bash
PYTHONPATH=src python -m grafix stub
```

削除した module を importする古い test helper や monkeypatch は、新しい immutable catalog /
explicit dependency injection を使うよう一括更新する。互換 module を test のためだけに追加しない。

最低限の確認:

```bash
PYTHONPATH=src pytest -q
ruff check src/grafix tests
mypy src/grafix
git diff --check
```

旧 schema/API の追加の破壊的変更は `docs/migration_2026-07-20.md` も参照する。
