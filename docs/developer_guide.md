<!--
どこで: `docs/developer_guide.md`。
何を: Grafix 開発者（人間/コーディングエージェント）向けの “読む順/入口” ガイド。
なぜ: `src/grafix/` の変更（実装改善・新機能追加）に入るまでの探索コストを下げるため。
-->

# Developer Guide（読む順と入口）

このドキュメントは「Grafix を改修したいとき、どこから読めばよいか」を最短で示す。

## まず読む（コンセプト）

1. `README.md`（使い方・API の雰囲気）
2. `architecture.md`（責務境界・依存方向・実行フロー）
3. `docs/glossary.md`（用語の対応表）

## 入口（コード）

### 公開 API（スケッチ作者が触る層）

- `src/grafix/__init__.py`（標準 PEP 562 root facade:
  `G/E/L/P/run/render/save/render_variation_batch/cc` と共通公開型）
- `src/grafix/api/__init__.py`（authoring DSL と公開 value type。application callable は re-export しない）
- `src/grafix/api/primitives.py`（`G.*`）
- `src/grafix/api/effects.py`（`E.*`）
- `src/grafix/api/operation_info.py`（evaluator-free な公開 catalog inspection value）
- `src/grafix/api/layers.py`（`L.*`）
- `src/grafix/api/presets.py` / `src/grafix/api/preset.py`（`P.*` / `@preset`）
- `src/grafix/api/cc.py`（root `cc` object の定義）
- `src/grafix/api/runner.py`（正規 signature/docstring を持つ軽量 `run(draw)` wrapper）
- `src/grafix/api/_runner_application.py`（config/GUI/MIDI/window の private heavy composition）
- `src/grafix/api/render.py`（`RenderSession` / `render(draw, t) -> Frame`）
- `src/grafix/api/export.py`（`save(frame, path) -> ExportResult` の headless 導線）

### コア（変更の中心になる層）

- `src/grafix/core/geometry.py`（Geometry: レシピ DAG / 署名）
- `src/grafix/core/operation_authoring.py` / `src/grafix/core/operation_declaration.py`（decorator / immutable declaration）
- `src/grafix/core/authoring_definitions.py` / `authoring_recipe.py`（registration target / immutable recipe・snapshot）
- `src/grafix/authoring_loader.py`（config authoring source の filesystem capture / candidate catalog）
- `src/grafix/_source_import_policy.py`（authoring/reload 共通の relative-import lexical preflight）
- `src/grafix/_snapshot_import.py`（config/reload が共有する temporary import transaction。private infrastructure）
- `src/grafix/core/operation_catalog.py` / `src/grafix/core/preset_catalog.py`（immutable catalog）
- `src/grafix/core/evaluation_config.py` / `evaluation_context.py`（評価専用 config、quality、external dependency contract）
- `src/grafix/core/realize.py`（`RealizeSession` / omitted-owned・explicit-borrowed dependency / inflight）
- `src/grafix/core/realized_geometry.py`（配列表現と不変条件）
- `src/grafix/core/scene.py`（Scene 正規化）
- `src/grafix/core/pipeline.py`（interactive/export 共通の realize パイプライン）
- `src/grafix/core/builtins.py`（組み込み op manifest / bootstrap の単一入口）
- `src/grafix/core/font_resources.py`（font asset fingerprint / bounded resource owner）
- `src/grafix/core/geometry_kernels/`（effect 共通の pure numeric kernel）
- `src/grafix/core/parameters/`（GUI/CC の param domain、codec、immutable snapshot）
- `src/grafix/parameter_storage.py`（`ParamStoreLoadResult`、parameter file read/recovery/atomic commit）
- `src/grafix/runtime_config_loader.py`（YAML/package resource、CWD/HOME discovery、fallback）

### Import boundary を確認したい

- sketch/public extension は `from grafix import ...` を正規入口にする。
- root と `grafix.api` は通常の `ModuleType` である。custom module class、代入 guard、
  callable/module の dual behavior は追加しない。
- root は公開名から定義 module/attribute への PEP 562 mapping だけを持つ。`grafix.export` は package、
  `grafix.api.export` は module、保存 callable は `grafix.save` / `grafix.api.export.save` である。
- `grafix.api.render` / `grafix.api.export` / `grafix.api.runner` / `grafix.api.cc` は通常 module である。
  application callable は root または各定義 module から取得し、`grafix.api` 直下に re-export しない。
- `run` の参照や signature inspection は GUI/runtime を load せず、call 時に
  `api._runner_application` を初期化する。
- `import grafix.core.<module>` は `grafix.api`、`grafix.export`、parameter storage、config loader を
  初期化しない。core module から outer capability を得るために root facade を importしない。
- 標準 import contract は `tests/api/test_lazy_facade.py`、公開型 graph は
  `tests/api/test_public_type_graph.py`、dependency direction は
  `tests/architecture/` を先に読む。

## 変更パターン別 “触る場所”

### primitive を追加/修正したい

- 実装: `src/grafix/core/primitives/*.py`
- 宣言: `@primitive(...)`（`src/grafix/core/operation_authoring.py`）
- 組み込み化: `src/grafix/core/builtins.py` の manifest に locator と evaluator ABI を追加
- custom module: session 作成前に通常 import、または config/source candidate から load

### effect を追加/修正したい

- 実装: `src/grafix/core/effects/*.py`
- 宣言: `@effect(...)`（`src/grafix/core/operation_authoring.py`）
- 組み込み化: `src/grafix/core/builtins.py` の manifest に locator と evaluator ABI を追加
- 共通数値処理: sibling effect ではなく `src/grafix/core/geometry_kernels/` に置く

### preset を追加/修正したい

- 実装と登録: `@preset(...)`（`src/grafix/api/preset.py`）
- 呼び出し: `P.<name>(...)`。label/identity 付きは
  `P(name=..., key=...).<name>(...)`（`src/grafix/api/presets.py`）
- IDE 補完（スタブ）更新: `python -m grafix stub`

### Parameter GUI（param 解決/表示/永続）を触りたい

- コア（値解決・履歴・snapshot）: `src/grafix/core/parameters/`
- storage（unified load result / read/recovery/commit）: `src/grafix/parameter_storage.py`
- current load-state owner: `src/grafix/interactive/runtime/parameter_session.py`
- GUI 実装: `src/grafix/interactive/parameter_gui/`
- GUI 起動と連携: `src/grafix/interactive/runtime/parameter_gui_system.py` / `src/grafix/api/runner.py`
- schema snapshot: `src/grafix/interactive/parameter_gui/catalog.py`
- table query/cache: `src/grafix/interactive/parameter_gui/table_view.py`
- effective source badge: `src/grafix/interactive/parameter_gui/source_badge.py`
- table edit commit: `src/grafix/interactive/parameter_gui/table_commit.py`
- session cache/widget lifetime: `src/grafix/interactive/parameter_gui/session_state.py`
- renderer は `TableRenderInput -> TableEdits` に限定し、`table_commit` / controller から core command へ渡す

`ParameterTableViewCache` は `ParameterGuiSessionState` が instance ごとに所有する。
`parameter_table_view_for_store(..., cache=...)` へ明示注入し、module-global cache/default catalog/build
counter を追加しない。catalog 交換・GUI close はその session の cache だけを clear する。

parameter file を読む extension は store だけを戻り値とみなさず、統一 result を扱う。

```python
from grafix.parameter_storage import (
    read_param_store,
    recover_param_store_session,
)

read_result = read_param_store(path)  # 原本を変更しない
store = read_result.store
status = read_result.status
provenance = read_result.load_state.provenance
diagnostics = read_result.load_state.diagnostics

recovery_result = recover_param_store_session(path)  # quarantine し得る明示経路
recovered_store = recovery_result.store
```

`ParamStore` / `ParamStoreRuntime` は load provenance/diagnostics を保持しない。interactive は
`ParameterSession` が current `KnownOperationSchemaSnapshot`、`load_state`、store/history/autosave と
終了時 persist を一意に所有する。source reload 成功時だけ schema を交換し、Keep は action dispatch
時の current schema を使う。Keep/Discard の detached load result は session が store contents と
load state を一箇所で採用する。

`ParameterSession.capture_state()` は一つの current load-state sample から frozen
`ParameterCaptureState(source, load_provenance)` を返す。interactive の provenance builder は provider
を frame ごとに一度だけ読み、headless の `RenderSession.metadata.parameter_load_state` と capture
state は構築時 result に固定する。

core parameter command は `ParamStore._read()` の copy/frozen view 上で
`validate -> plan` を完了し、完成した replacement だけを `ParamStore._mutation()` へ渡す。
sibling command は `_ParamStoreMutation` だけを write port として使い、expected revision、
history-before observation、参照 swap、revision/cache 更新を一つの commit として確定する。
`ParamStore` 自身が所有する construction/contents replacement/transient rollback はこの境界内に残る。
mutation batch API は持たず、store 外へ mutable `_..._ref()` や手動
`_touch()` を戻さない。frame merge が effective value/source だけを変える場合は
`commit_runtime_value_patch()` が full plan を作らず sparse patch を適用し、persistent revision と
runtime identity を保って effective revision だけを進める。

### Export（headless 出力）を触りたい

- render/store/config/cache: `src/grafix/api/render.py`
- variation request/render/partial failure: `src/grafix/api/variation_batch.py`
- variation directory transaction: `src/grafix/export/variation_batch.py`
- encode/no-clobber/manifest: `src/grafix/export/capture.py`
- staging/publish: `src/grafix/export/capture_staging.py` / `src/grafix/export/capture_publish.py`
- output path policy: `src/grafix/export/output_paths.py`
- 入口 API: `src/grafix/api/export.py:save`
- フォーマット別: `src/grafix/export/svg.py` / `src/grafix/export/image.py` / `src/grafix/export/gcode.py`
- 共通パイプライン: `src/grafix/core/pipeline.py`

直接保存する正規形は `from grafix import save` と `save(frame, path)` である。`grafix.export` は
subsystem package のため、callable として扱わない。

GUI の named variation 保存は `prepare_variation -> thumbnail capture -> commit_variation` の順である。
prepare が metadata/duplicate/snapshot/revision を I/O 前に検証し、capture は exact path と
`discard()` を持つ publish-owned token を返す。file identity は publish 前に一度だけ取得し、
runtime adapter は token を再構築せず GUI へ渡す。capture failure は thumbnail なし commit、
commit failure は今回の artifact family の discard に進む。capture callback は同期中に store を
変更しない contract で、revision が変われば commit は state を変更せず失敗する。batch API は raw
store を借りず、session 内に閉じた一時適用/render/rollback capability を使う。

publish rollback と token の `discard()` は、cleanup 中に同名 path が安定していることを前提にした
best-effort compare-then-delete である。identity 検査時点で missing、非通常 file、identity mismatch
と観測した entry は削除しない。検査と `unlink()` は atomic ではなく、その間の並行交換は保証しない。

API variation batch は variation 順、item ごとの transient rollback、render/capture callback、partial
failure だけを持つ。private workspace、manifest relocation、contact sheet/summary encode、no-clobber
retry、overwrite failure 時の旧 generation 復元は export transaction が一括所有する。API に
fsync/link/replace/staging codec を追加せず、export から API/interactive を importしない。

output path helper は ambient config を探索しない。すべて composition root で解決済みの
`RuntimeConfig` を渡す。

```python
from grafix.export.image import default_png_output_path
from grafix.export.output_paths import default_param_store_path, output_path_for_draw
from grafix.runtime_config_loader import load_runtime_config

config = load_runtime_config(".grafix/config.yaml")
svg_path = output_path_for_draw(
    kind="svg",
    ext="svg",
    draw=draw,
    config=config,
)
parameter_path = default_param_store_path(draw, config=config)
png_path = default_png_output_path(
    draw,
    scale=3.0,
    canvas_size=(300, 300),
    config=config,
)
```

`default_video_output_path()` と `default_workspace_state_path()` も同じく `config=` が必須である。
`config=None` fallback や export layer から config loader を呼ぶ経路を戻さない。

### Interactive runtime / reload / diagnostics を触りたい

- public runner / heavy composition: `src/grafix/api/runner.py` /
  `src/grafix/api/_runner_application.py`
- frame評価とworker世代: `src/grafix/interactive/runtime/scene_runner.py` /
  `src/grafix/interactive/runtime/mp_draw.py`
- mp-draw DTO/wire validation: `src/grafix/interactive/runtime/_mp_draw_protocol.py`
- mp-draw pure parent transitions: `src/grafix/interactive/runtime/_mp_draw_state.py`
- mp-draw spawn entrypoint/evaluation: `src/grafix/interactive/runtime/_mp_draw_worker.py`
- presented frame/capture binding: `src/grafix/interactive/runtime/presented_frame.py`
- transactional source watch: `src/grafix/interactive/runtime/source_reload.py`
- frame順序と配線: `src/grafix/interactive/runtime/draw_window_system.py`
- capture admission: `src/grafix/interactive/runtime/capture_queue.py`
- recording lifecycle: `src/grafix/interactive/runtime/recording_session.py`
- window policy: `src/grafix/interactive/runtime/workspace_window_controller.py`
- parameter session: `src/grafix/interactive/runtime/parameter_session.py`
- variation thumbnail export adapter: `src/grafix/interactive/runtime/variation_thumbnail_capture.py`
- 共通診断stream: `src/grafix/interactive/diagnostics.py`
- transport contract: `src/grafix/interactive/transport.py`
- resource/profiler表示: `src/grafix/interactive/runtime/perf.py` / `parameter_gui/profiler_panel.py`
- window状態復元: `src/grafix/interactive/runtime/workspace_state.py`

reload candidate は source bytes と local relative-import helper を隔離し、scoped
`RegistrationTarget` から immutable authoring snapshot を構築する。draw signature、catalog、worker
startup を検証してから同じ frame 境界で generation を交換する。失敗時に default authoring
definitions を変更したり、last-good worker/catalog を閉じたりしない。

config authoring と source reload は `grafix._snapshot_import` の一つの reentrant lock と cleanup
transaction を共有する。source discovery/import policy/catalog accept は caller に残し、別の
`sys.meta_path` / `sys.modules` 操作を実装しない。config directory の capture/load は
`grafix.authoring_loader` を使い、削除済みの core 内 loader path を importしない。

`preset_module_dirs` は synthetic namespace source root で、root `__init__.py` は全件 preflight で
拒否する。nested package の `__init__.py` は通常どおり一度だけ実行する。relative import は module
lexical scope に限定し、function/async function/class 内の deferred import は path/line/scope 付きで
実行前に拒否する。filesystem capture と pickle 復元 recipe の両方が同じ全件 preflight を通る。

Parameter GUI leaf は export type/service を importせず、variation thumbnail の capture/preview callable
だけを受け取る。composition root が `CaptureService` の private owned-export callable を runtime
adapter へ渡す。adapter は要求ごとに live frame provider を呼び、実際の no-clobber path と publish
時 identity を持つ exact token を GUI へ返す。runtime で `stat()` し直したり rollback owner を
作り直したりしない。

MIDI の低水準 composition は exact path を必ず渡す。

```python
from pathlib import Path

from grafix.interactive.midi.factory import create_midi_session

midi = create_midi_session(
    port_name="auto",
    mode="7bit",
    snapshot_path=Path("data/output/sketch-midi.json"),
)
```

production では `api._runner_application` が解決済み `RuntimeConfig` と draw/run ID からこの path を
一度だけ作る。controller/factory/helper は live load/save、frozen fallback、reconnect、discard で
同じ `snapshot_path` を使い、profile/save directory や ambient config から再構築しない。

`MpDraw` の private module は protocol/state/worker/parent resource owner に分離されている。
repository consumer は scalar telemetry property を連続して読まず、一度取得した frozen
`stats = mp_draw.stats` から同一時点の値を読む。`generation` と `evaluation_timeout` など制御 contract
に必要な property だけは parent owner に残る。worker は task payload を current snapshot 更新にだけ
使い、requested revision と worker current revision が一致するときだけ worker-owned
snapshot/effect-order pair を評価する。

### Architecture / cache identity を触りたい

- declaration fingerprint: `src/grafix/core/definition_fingerprint.py`
- typed cache key: `src/grafix/core/realize.py:GeometryCacheKey`
- parent/child ownership: `src/grafix/api/render.py` / `interactive/runtime/scene_runner.py`
- font external dependency: `src/grafix/core/font_resources.py` / `src/grafix/core/primitives/text.py`

全 catalog revision や object identity を新しい cache key に入れない。Geometry が実際に参照した
operation ref、quality/`EvaluationConfig`、lookup 時点の external dependency だけを使う。

`RenderSession` / `SceneRunner` は `EvaluationResources` と `RealizeCacheStore` を所有して明示注入し、
子 `RealizeSession` は借用する。低水準で `RealizeSession` の `resources` / `cache_store` を省略した場合は、
省略した dependency だけを session が所有して `close()` する。二つの引数は独立に判定されるため、
明示注入した dependency を session 側から閉じない。
公開 `RenderSession` は close 可能な子 owner を property として返さない。

### Benchmark harness を追加/修正したい

通常利用は `python -m grafix benchmark ...` を入口とする。内部 harness を拡張する場合の canonical
module は次のとおり。

```python
from grafix.devtools.benchmarks.catalog import (
    case_definitions,
    definition_for_case,
    select_case_definitions,
)
from grafix.devtools.benchmarks.definition import (
    CaseDefinition,
    define_case,
    make_case_spec,
    scaled_case_definitions,
)
from grafix.devtools.benchmarks.runner import run_case_isolated
```

- case の immutable 定義と source identity: `definition.py`
- provider 収集、重複検査、stable selection: `catalog.py`
- checksum と typed metric/aggregation: `metrics.py`
- in-process/fresh-process 計測、calibration、timeout、child lifecycle: `executor.py`
- subsystem ごとの setup/workload/postprocess: `*_benchmark.py` provider
- catalog と executor の composition/child entrypoint: `runner.py`

metric helper は `grafix.devtools.benchmarks.metrics` から import する。executor の公開 helper
（`execute_case_isolated`、`execute_child_request`、`measure_in_process`、`read_child_request`）が必要なのは
harness test/tool の低水準実装だけである。`runner` の公開 symbol は `run_case_isolated` のみで、旧
`runner._workload*`、集計 helper、case selection を import する経路や re-export shim はない。

workload は対象 subsystem の provider に置き、`case_definitions()` で返した定義を `catalog.py` の
provider 列へ明示追加する。provider から catalog/executor/runner へ逆依存させない。provider間の
再利用が必要なら、architecture testの明示allowlistにある一方向のpublic helperだけを使い、siblingの
private symbolへ到達しない。依存規則は`tests/architecture/test_benchmark_dependency_boundaries.py`が
検査する。

## 関連ツール（CLI）

- `python -m grafix list`（組み込み effect/primitive の一覧）
- `python -m grafix describe primitive|effect NAME`（catalog詳細）
- `python -m grafix run sketch.py --watch`（transactional live reload。MIDI無効化は
  exact `--midi-port none`）
- `python -m grafix config validate|show [PATH]`（strict config検証。pathはpositionalのみ）
- `python -m grafix init` / `doctor` / `examples`（onboarding）
- `python -m grafix stub`（`grafix.api` のスタブ再生成）
- `python -m grafix export --callable module:attr --t ...`（headless export。詳細は `python -m grafix export -- --help`）
- `python -m grafix benchmark -- --help`（ベンチ/レポート生成）

破壊的な import/signature/internal owner の変更一覧は
`docs/migration_2026-07-23_r3.md` を参照する。
