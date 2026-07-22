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

- `src/grafix/__init__.py`（再エクスポート: `G/E/L/P/run/cc`）
- `src/grafix/api/__init__.py`（公開 API パッケージ）
- `src/grafix/api/primitives.py`（`G.*`）
- `src/grafix/api/effects.py`（`E.*`）
- `src/grafix/api/operation_info.py`（evaluator-free な公開 catalog inspection value）
- `src/grafix/api/layers.py`（`L.*`）
- `src/grafix/api/presets.py` / `src/grafix/api/preset.py`（`P.*` / `@preset`）
- `src/grafix/api/runner.py`（`run(draw)` の interactive 実装）
- `src/grafix/api/render.py`（`RenderSession` / `render(draw, t) -> Frame`）
- `src/grafix/api/export.py`（`export(frame, path) -> ExportResult` の headless 導線）

### コア（変更の中心になる層）

- `src/grafix/core/geometry.py`（Geometry: レシピ DAG / 署名）
- `src/grafix/core/operation_authoring.py` / `src/grafix/core/operation_declaration.py`（decorator / immutable declaration）
- `src/grafix/core/authoring_definitions.py` / `src/grafix/core/authoring_loader.py`（registration target / session snapshot）
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
- `src/grafix/parameter_storage.py`（parameter file read/recovery/atomic commit）
- `src/grafix/runtime_config_loader.py`（YAML/package resource、CWD/HOME discovery、fallback）

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
- storage（read/recovery/commit）: `src/grafix/parameter_storage.py`
- GUI 実装: `src/grafix/interactive/parameter_gui/`
- GUI 起動と連携: `src/grafix/interactive/runtime/parameter_gui_system.py` / `src/grafix/api/runner.py`
- schema snapshot: `src/grafix/interactive/parameter_gui/catalog.py`
- renderer は `TableRenderInput -> TableEdits` に限定し、変更は store bridge/controller から core command へ渡す

### Export（headless 出力）を触りたい

- render/store/config/cache: `src/grafix/api/render.py`
- encode/no-clobber/manifest: `src/grafix/export/capture.py`
- staging/publish: `src/grafix/export/capture_staging.py` / `src/grafix/export/capture_publish.py`
- output path policy: `src/grafix/export/output_paths.py`
- 入口 API: `src/grafix/api/export.py`
- フォーマット別: `src/grafix/export/svg.py` / `src/grafix/export/image.py` / `src/grafix/export/gcode.py`
- 共通パイプライン: `src/grafix/core/pipeline.py`

### Interactive runtime / reload / diagnostics を触りたい

- frame評価とworker世代: `src/grafix/interactive/runtime/scene_runner.py` / `mp_draw.py`
- presented frame/capture binding: `src/grafix/interactive/runtime/presented_frame.py`
- transactional source watch: `src/grafix/interactive/runtime/source_reload.py`
- frame順序と配線: `src/grafix/interactive/runtime/draw_window_system.py`
- capture admission: `src/grafix/interactive/runtime/capture_queue.py`
- recording lifecycle: `src/grafix/interactive/runtime/recording_session.py`
- window policy: `src/grafix/interactive/runtime/workspace_window_controller.py`
- parameter session: `src/grafix/interactive/runtime/parameter_session.py`
- 共通診断stream: `src/grafix/interactive/diagnostics.py`
- transport contract: `src/grafix/interactive/transport.py`
- resource/profiler表示: `src/grafix/interactive/runtime/perf.py` / `parameter_gui/profiler_panel.py`
- window状態復元: `src/grafix/interactive/runtime/workspace_state.py`

reload candidate は source bytes と local relative-import helper を隔離し、scoped
`RegistrationTarget` から immutable authoring snapshot を構築する。draw signature、catalog、worker
startup を検証してから同じ frame 境界で generation を交換する。失敗時に default authoring
definitions を変更したり、last-good worker/catalog を閉じたりしない。

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
