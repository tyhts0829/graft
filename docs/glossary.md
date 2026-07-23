<!--
どこで: `docs/glossary.md`。
何を: Grafix の主要用語（コア/parameters/interactive/export）を短く定義する用語集。
なぜ: 仕様変更やコードリーディング時の “言葉の対応” を最短で取れるようにするため。
-->

# Glossary（用語集）

## Core（データモデル/評価）

- `Geometry`: 配列そのものではなく「幾何のレシピ」を表す DAG ノード。`src/grafix/core/geometry.py:Geometry`
- `GeometryId`: canonical `(op, EvaluationOpRef, input GeometryId, args)` から計算され、使用 operation version を推移的に含む内容署名 ID。`src/grafix/core/geometry.py:compute_geometry_id`
- `RealizedGeometry`: `Geometry` を評価して得られる実体配列（`coords` + `offsets`）。`src/grafix/core/realized_geometry.py:RealizedGeometry`
- `GeomTuple`: `(coords, offsets)` タプルで表す最小実体表現。`@primitive` / `@effect` のユーザー定義 I/O に使う。`src/grafix/core/realized_geometry.py:GeomTuple`
- `EvaluationContext`: 一 catalog generation の operation catalog、quality、effective config を固定する immutable evaluation contract。`src/grafix/core/evaluation_context.py:EvaluationContext`
- `RealizeCacheStore`: byte/entry 上限付き CPU LRU。通常は catalog generation 外の親 session/runtime が所有し、低水準 `RealizeSession` で省略した場合だけその session が所有する。`src/grafix/core/realize.py:RealizeCacheStore`
- `EvaluationResources`: external-dependency provider memo と bounded font/glyph resource を所有する closeable generation resource。`src/grafix/core/evaluation_context.py:EvaluationResources`
- `RealizeSession`: `Geometry -> RealizedGeometry` と inflight 集約を行う session。明示注入した `EvaluationResources` / `RealizeCacheStore` は借用し、省略した各 dependency だけを所有して close する。`EvaluationContext` は immutable value。`src/grafix/core/realize.py:RealizeSession`
- `GeometryCacheKey`: `GeometryId`、evaluation fingerprint、external-dependency fingerprint、任意の uncached generation を持ち、CPU/inflight/GPU cache で共有する typed key。`src/grafix/core/realize.py:GeometryCacheKey`
- `RealizedLayer`: 描画/出力用の「Layer + realize 済みジオメトリ + cache key + style」。`src/grafix/core/pipeline.py:RealizedLayer`

## Scene / Layer（描画単位）

- `Layer`: `Geometry` とスタイル（色/線幅）を束ねる描画単位。`src/grafix/core/layer.py:Layer`
- `SceneItem`: `draw(t)` が返せる型（Geometry/Layer/それらの列など）を正規化するための入力表現。`src/grafix/core/scene.py`
- `normalize_scene`: `SceneItem` を `Layer` 列へ正規化する関数。`src/grafix/core/scene.py:normalize_scene`

## Authoring / catalog / builtins（拡張ポイント）

- `ParameterOpSchema`: meta、defaults、引数順、UI 表示規則を evaluator から独立して保持する frozen schema。`src/grafix/core/operation_schema.py:ParameterOpSchema`
- `OpDeclaration`: evaluator、schema、arity、cache/external-dependency contract と evaluation/schema fingerprint を持つ immutable authoring declaration。`src/grafix/core/operation_declaration.py:OpDeclaration`
- `EvaluationOpRef`: DAG node が固定する operation kind/name/evaluation fingerprint。`src/grafix/core/operation_declaration.py:EvaluationOpRef`
- `EffectStepRef`: 遅延 effect step が作成時に固定する evaluation ref と schema fingerprint。`src/grafix/core/operation_declaration.py:EffectStepRef`
- `OperationCatalog`: 一 session/generation 内で変化しない operation entry snapshot。`src/grafix/core/operation_catalog.py:OperationCatalog`
- `PresetCatalog`: 一 session/generation 内で変化しない preset declaration snapshot。`src/grafix/core/preset_catalog.py:PresetCatalog`
- `RegistrationTarget`: decorator declaration を operation/preset builder へ渡す単一登録境界。`src/grafix/core/authoring_definitions.py:RegistrationTarget`
- `DefaultAuthoringDefinitions`: 通常 module-scope decorator 用の process-level declaration store。evaluation はその snapshot だけを使う。`src/grafix/core/authoring_definitions.py:DefaultAuthoringDefinitions`
- `@primitive` / `@effect`: canonical `(coords, offsets)` tuple I/O の callable から declaration を作る公開 decorator。`src/grafix/core/operation_authoring.py`
- built-in manifest: builtin module/callable/evaluator ABI の唯一の locator。import 済み callable に付与された declaration を bootstrap が回収する。`src/grafix/core/builtins.py`
- synthetic authoring root: `preset_module_dirs` 一件を表す namespace source root。root `__init__.py` は拒否し、nested package initializer は通常実行する。`src/grafix/authoring_loader.py`
- source import lexical policy: relative import を module scope に限定し、function/async function/class 内を全 candidate 実行前に拒否する共通規則。`src/grafix/_source_import_policy.py`

## parameters（GUI/CC と値解決・永続）

- `ParamStore`: パラメータ状態の永続ストア（state/meta/label/ordinal/effect chain）。`src/grafix/core/parameters/store.py:ParamStore`
- `ParamMeta`: 引数の UI 種別・範囲などの最小メタ。`src/grafix/core/parameters/meta.py:ParamMeta`
- `ParamState`: UI 値・override・CC 割当などの状態。`src/grafix/core/parameters/state.py:ParamState`
- `ParameterKey`: `(op, site_id, arg)` で GUI 行を一意化するキー。`src/grafix/core/parameters/key.py:ParameterKey`
- `site_id`: project-relative code location、または G/E/L/P の明示 `key=` から作る呼び出し箇所 ID。`src/grafix/core/parameters/key.py:make_site_id`
- `ParamSnapshot`: `ParamStore` を revision 単位で読み取り用に固定した mapping。`src/grafix/core/parameters/snapshot_ops.py:ParamSnapshot`
- `ParamStore.revision`: snapshot/GUI model/worker 同期を無効化する、snapshot に影響する永続状態の変更時だけ進む単調 revision。
- `ParamRuntimeView`: GUI/application が private live container へ触れずに読む時点固定 runtime snapshot。生成時に mapping を浅く copy し、後続 mutation を観測しない。要素は canonical immutable value。`src/grafix/core/parameters/runtime.py:ParamRuntimeView`
- `_ParamStoreRead`: core parameter command が検証・計画に使う、copy/frozen value だけを返す private read port。`src/grafix/core/parameters/store.py:_ParamStoreRead`
- `_ParamStoreMutation`: 完成済み replacement の expected-revision 確認、参照 swap、revision/history/cache 確定だけを行う private mutation port。`src/grafix/core/parameters/store.py:_ParamStoreMutation`
- `commit_runtime_value_patch`: effective value/source だけの frame merge を full store plan なしで確定し、persistent revision と runtime identity を保つ private sparse commit。`src/grafix/core/parameters/store.py:_ParamStoreMutation.commit_runtime_value_patch`
- `ParamStoreRollback`: variation batch などの一時評価で論理 state と counter を exact restore する owner-bound one-shot rollback scope。`src/grafix/core/parameters/store.py:ParamStoreRollback`
- `ParameterSession`: interactive session の current schema/load state、store/history/autosave、capture-state provider、終了時 persist の owner。`src/grafix/interactive/runtime/parameter_session.py:ParameterSession`
- `ParameterCaptureState`: 同じ load-state sample から導出した parameter source と load provenance の frozen pair。`src/grafix/core/parameters/runtime.py:ParameterCaptureState`
- `VariationDraft`: thumbnail I/O 前に validation、parameter snapshot、base store revision を固定する immutable variation draft。`src/grafix/core/parameters/variations.py:VariationDraft`
- `ParameterEdit`: GUI の変更意図を表し、複数件を一つの core command として適用できる immutable edit。`src/grafix/core/parameters/edit_commands.py:ParameterEdit`
- `parameter_context`: フレーム境界で `ParamSnapshot` と `FrameParamsBuffer`（観測バッファ）を固定し、終了時に store へマージする。`src/grafix/core/parameters/context.py:parameter_context`
- `FrameParamsBuffer`: そのフレームで観測・解決した引数を貯めるバッファ。`src/grafix/core/parameters/frame_params.py:FrameParamsBuffer`
- `resolve_params`: base/GUI/CC から effective 値を決め、観測を記録する関数。`src/grafix/core/parameters/resolver.py:resolve_params`
- `explicit_args`: 「ユーザーが明示指定した kwargs の集合」。初期 override ポリシー用に観測へ残す。`src/grafix/core/parameters/resolver.py:resolve_params`
- `chain_id` / `step_index`: effect チェーンのグルーピングと順序情報。`src/grafix/api/effects.py:EffectBuilder` / `src/grafix/core/parameters/effects.py:EffectChainIndex`

## API（スケッチ作者が触る）

- `G`: primitive の公開名前空間（`G.circle(...) -> Geometry`）。`src/grafix/api/primitives.py:G`
- `E`: effect チェーンの公開名前空間（`E.scale(...).rotate(...)(g)`）。`src/grafix/api/effects.py:E`
- `L`: Layer 化（スタイル付与/concat 等）の公開名前空間。`src/grafix/api/layers.py:L`
- `P` / `@preset`: preset 登録と呼び出しの公開導線。`src/grafix/api/presets.py:P` / `src/grafix/api/preset.py:preset`
- `run(draw)`: interactive ランナー。`src/grafix/api/runner.py:run`
- `render()` / `save()`: headless 評価と保存の入口。`src/grafix/api/render.py:render` /
  `src/grafix/api/export.py:save`
- `grafix.export`: encode/staging/publish subsystem package。保存 callable ではない。`src/grafix/export/`
- `grafix.api`: authoring DSL と公開 value type の package。application callable は root または各定義 module から取得する。`src/grafix/api/__init__.py`

## Effect math / runtime

- `PlanarFrame`: 3D の平面形状を決定的な2D座標へ写し、rank/residualも返す共通平面基底。`src/grafix/core/geometry_kernels/planar.py:PlanarFrame`
- `GridSpec`: bbox、pitch、cell上限から確保前に格子解像度を決める値。`src/grafix/core/geometry_kernels/grid.py:GridSpec`
- `ExportJobSystem`: PNG/G-code を bounded FIFO で処理し、満杯時は明示拒否する長寿命
  spawn worker。`src/grafix/interactive/runtime/export_job_system.py:ExportJobSystem`
- `CaptureQueue`: immutable capture intent、件数/aggregate geometry byte admission、worker poll/drain を所有する runtime owner。`src/grafix/interactive/runtime/capture_queue.py:CaptureQueue`
- `MpDraw`: parent process、Queue、restart、timeout、close を所有する multiprocessing owner。`src/grafix/interactive/runtime/mp_draw.py:MpDraw`
- `MpDrawStats`: `MpDraw.stats` が同一時点の main-thread owner state から返す frozen telemetry snapshot。`src/grafix/interactive/runtime/_mp_draw_protocol.py:MpDrawStats`
- mp-draw protocol/state/worker: pickle DTO・wire validation、I/O-free 親 transition、spawn worker evaluation をそれぞれ分けた private module 群。`src/grafix/interactive/runtime/_mp_draw_protocol.py` / `_mp_draw_state.py` / `_mp_draw_worker.py`
- `ParameterGuiCatalog`: operation/preset catalog から evaluator を除いた session-local schema projection。`src/grafix/interactive/parameter_gui/catalog.py:ParameterGuiCatalog`
- `ParameterTableModel`: store structure revision と immutable GUI catalog 内で不変な行・順序・header を保持する cache 単位。`src/grafix/interactive/parameter_gui/table_model.py:ParameterTableModel`

## Benchmark harness

- `CaseDefinition`: case metadata、setup/workload/postprocess、source identity 材料をまとめた immutable benchmark 定義。`src/grafix/devtools/benchmarks/definition.py:CaseDefinition`
- benchmark catalog: provider の定義を収集し、重複拒否、stable ordering、suite/case selection を行う正規入口。`src/grafix/devtools/benchmarks/catalog.py`
- benchmark metrics: exact checksum と typed metric、warm/cold aggregation の owner。`src/grafix/devtools/benchmarks/metrics.py`
- benchmark executor: in-process/fresh-process 計測、calibration、timeout、child process group の kill/reap を所有し、catalog/workload を知らない。`src/grafix/devtools/benchmarks/executor.py`
- benchmark runner: catalog と executor の composition および child entrypoint。公開 symbol は `run_case_isolated` のみ。`src/grafix/devtools/benchmarks/runner.py`
