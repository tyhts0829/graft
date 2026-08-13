# Agent-facing Semantic Header 導入計画（2026-08-13）

- 状態: **完了**
- 計画作成時 branch: `main`
- 計画作成時 HEAD: `ca5ae6d`
- 計画作成時 working tree: clean
- 実装承認: 2026-08-13
- 実装開始時 HEAD: `ca5ae6d`
- 実装開始時差分: 本計画書のみ（未追跡）
- 対象: routing 価値の高い Python source と、header から再生成する小さな semantic index

この計画は巨大な AI 向け説明書を増やすものではない。source of truth の冒頭へ短い routing metadata を
置き、agent が全文を読む前に候補ファイルを絞れる状態を作る。

本書の作成までは読み取り専用の reconnaissance と convention design であり、production code、test、
既存文書の変更は行っていない。以下のチェックボックスは、計画承認後に実施した項目だけを `[x]` にする。

## 1. 調査結果

### 1.1 Repository map と実行境界

```text
public authoring:  grafix root facade / grafix.api (G, E, L, P, run, render, save)
                         |
authoring:         declaration -> immutable operation/preset catalog
                         |
domain:            Geometry DAG -> scene normalization -> shared realize pipeline
                         |
application:       headless RenderSession | interactive runtime composition
                         |
output:            GL preview | capture staging/publish | SVG/PNG/G-code/video
```

- `src/grafix/`: package 本体。296 Python modules。
- `src/grafix/core/`: immutable domain value、operation/catalog、Geometry evaluation、parameter domain。
- `src/grafix/api/`: 公開 DSL と application entry point。`runner.py` は軽量 wrapper、
  `_runner_application.py` は heavy composition root。
- `src/grafix/interactive/`: GL/MIDI/GUI leaf と runtime coordinator/resource owner。
- `src/grafix/export/`: encode、staging、no-clobber publish、provenance、output path policy。
- `src/grafix/devtools/`: CLI、generator、diagnostics、benchmark harness。repository tool と package CLI が同居。
- `tests/`: 331 tracked Python files。通常 test は名前と test function から routing できるが、共有 fixture と
  architecture boundary test は局所 metadata の価値が高い。
- `sketch/`: 79 tracked Python files。作品・README例・work と、config から読む再利用 preset が混在。
- `.agents/skills/*/scripts/`: repository 固有の agent/tool implementation が4本。
- `tools/`: 調査開始時は空。今回、再生成可能な semantic index CLI を1本追加した。

既存の正本は `architecture.md`、`docs/architecture_visualization.md`、
`docs/developer_guide.md`、`docs/glossary.md`。ただし source 選択前にこれら全体を読む必要があり、
個々の source には routing 用の `Use when` と局所 invariant が不足している。

### 1.2 既存 header の状態

- `src/grafix` の296 modules中、module docstringあり217、なし79。
- `Purpose` / `Use when` / `Constraints` が揃う structured header は0。
- 旧規約の「どこで・何を・なぜ」または一行要約が多く、責務の近い sibling の使い分けを判断しにくい。
- `docs/agent_docs/documentation.md` は旧形式を恒久規約として要求しており、新形式と競合する。
- `src/grafix/core/effects/AGENTS.md` は effect 冒頭にアルゴリズム手順ではなく効果の意味を書くよう要求する。
  この意図は新形式の `Purpose` に維持する。

### 1.3 Agent が迷いやすい主な領域

- declaration / definitions / catalog / selector / public namespace の類似した責務。
- `Geometry` と `RealizedGeometry`、runtime config と evaluation config、render と save/capture。
- ParamStore の query、command、frame merge、reconcile、load-state、session ownership。
- synchronous draw、mp-draw protocol/state/worker/owner、source reload generation。
- GUI の table model/view/render/commit と controller/session state。
- capture staging/publish、interactive export queue、variation batch の transaction owner。
- planar/raster/marching/resample kernel と、それらを組み合わせる複数の effect。
- benchmark の definition/catalog/provider/executor/runner/schema/report。

### 1.4 Generated / vendor / non-source boundary

次は対象外とする。

- `dist/`、`*.egg-info/`、cache、`__pycache__/`、`.grafix/data/`、`data/output/`。
- font、NPZ、画像などの static/binary resource。
- generated stub の `src/grafix/api/__init__.pyi` と `typings/`。
- lock file、package metadata、単純な YAML/TOML、CI workflow。
- `docs/old/`、migration/history document、既存の巨大 architecture documentへの情報追加。

## 2. 採用する header 規約

Python では AST が取得できる module docstring を、shebang/encoding commentの後かつ
`from __future__` より前の最初の statement として置く。key は機械抽出しやすい英語、本文は日本語とする。

```python
"""
Purpose:
    この module が担う意味的責務を1〜2文で示す。
Use when:
    どの変更・調査で、この module を次に読むべきかを示す。
Constraints:
    - 破ると意味または ownership が壊れる invariant だけを書く。
See:
    強く関連する正本がある場合だけ示す。
"""
```

規約は次で固定する。

- 原則5〜15行。`Purpose`、`Use when`、`Constraints` は必須。
- `Constraints` は1〜3項目を目安にし、一般論や実装上の細部を書かない。
- `Side effects` は file I/O、network、external process、GUI/GPU、process-global import transaction など、
  routing 判断に必要な場合だけ追加する。
- `See` は強い sibling/source-of-truth 関係だけに使い、参照集を作らない。
- 関数/class/import/引数/戻り値/手順の一覧を置かない。
- 既存の公開関数・class docstring と型ヒントは変更しない。
- 既存 module docstring が持つ有用な設計意図は短い fields へ統合し、重複 prose は残さない。
- Purpose または invariant を十分な確度で判断できない file は変更せず、最終報告へ記録する。
- 同じ package-wide constraint を全 sibling へ反復しない。たとえば effect 間の依存規則は
  `src/grafix/core/effects/AGENTS.md` を正本とし、個別 header には局所 invariant だけを書く。

恒久規約として `docs/agent_docs/documentation.md` の旧 header 節をこの形式へ置換する。
新規の総覧ドキュメントは作らない。

## 3. 選択的な適用範囲

最初から全 module を機械変換しない。以下を候補集合とし、各 file を再読して三項目を確信できたものだけ
変更する。想定は約150〜180 filesだが、件数達成を目的にせず最終件数は実査結果で確定する。
実査の結果、header は194 filesへ選択的に適用した。

### 3.1 最優先: public / infrastructure / core source of truth

- root infrastructure: `grafix/__init__.py`、`__main__.py`、`_snapshot_import.py`、
  `_source_import_policy.py`、`authoring_loader.py`、`runtime_config_loader.py`、
  `parameter_storage.py`、`file_io.py`。
- `grafix/api/`: trivial sentinel の `_unset.py` を除く public facade、DSL、selector adapter、
  render/export/runner/variation composition。
- `grafix/core/`: Geometry/RealizedGeometry、Layer/Scene/Pipeline、operation declaration/authoring/catalog、
  definitions/recipe/builtins/fingerprint、evaluation context/config/realize/resource budget、font resources、
  runtime config/limits、diagnostics/schema/selector、capture provenance/manifest、G-code parameters。
- 単純定数、単純 enum/result value、trivial `__init__.py` は除外する。

### 3.2 高優先: parameter domain と geometry processing

- `core/parameters/`: store/runtime、context/frame observation/resolution/merge、snapshot、codec/parser、
  edit/effect-order、history/autosave、reconcile、variation、layer style、validationなど、ownershipまたは
  persistent meaningを持つ中心 modules。単純 forwarding ops は除外する。
- `core/geometry_kernels/`: `grid.py`、`marching.py`、`packed.py`、`planar.py`、`raster.py`、`resample.py`。
- 類似・誤用リスクの高い effects: boolean、buffer、clip、deduplicate、fill、growth、highpass、
  isocontour、lowpass、metaball、mirror3d、offset_curve、partition、reaction_diffusion、relax、
  resample、simplify、warp、weave。
- specialized primitives: text と共有 helper、asemic、delaunay、laplace-field grid、L-system、
  polyhedron、spline、topographic contours。
- 名前だけで役割が十分明確な基本図形・単純変換 effect は除外する。

### 3.3 高優先: export と interactive ownership

- `grafix/export/` の non-trivial modules 全て。
- interactive の diagnostics/telemetry/transport、GL renderer、MIDI factory/controller/session。
- runtime の mp-draw protocol/state/worker/owner、scene runner、source reload、draw window composition、
  parameter session/recovery、presented frame、capture/export/recording、variation thumbnail、workspace owner。
- Parameter GUI の catalog、session state、table model/view/render/commit、main GUI composition、
  range/reconcile/variation controller、backend/widget boundary。
- 単純な表示 helper、theme、定数中心 module は除外する。

### 3.4 必要に応じて対象: tooling / reusable test support / authoring support

- `devtools/`: side effect または生成物契約が強い CLI/generator、および benchmark の
  definition/catalog/metrics/executor/runner/schema/compare/report 境界。
- `.agents/skills/*/scripts/*.py`: 4本の repository 固有 agent tool候補。このうちmodule docstringをCLI helpに
  使う1本は実行時表示を守るため変更しない。
- reusable test support: `tests/*_test_support.py`、interactive shared fixture、manual GUI runner。
- architecture boundary testsは、既存docstringで invariant routing が不足するものだけ補強する。
- `sketch/presets/` は reusable authoring module として、名前だけでは責務や registration contract が
 不明な共通 moduleを優先する。G-code alignment sketch と showcase は物理座標・manifest同期などの
 強い制約を確信できる場合だけ対象にする。

## 4. 原則対象外

- 通常の unit/integration test。test path と test function が十分な routing metadata になる。
- `sketch/readme/`、`sketch/agent_art/`、`sketch/work/`、`sketch/agent_loop/` の作品・生成run。
- 同梱 examples。`onboarding` が module docstring先頭を一覧説明として利用するため、機械形式へ変えない。
- trivial `__init__.py`、単純定数、明白な一関数 wrapper、小さな表示専用 helper。
- 既存docstringがすでに Purpose/利用場面/制約を十分に伝え、形式変更の便益が小さい file。

`sketch/main.py` は名前上の default entry point と現在の特定作品という二つの意味が混在しており、
durableなPurposeを確定できないため、現時点では変更せず「判断できなかった重要ファイル」として報告する。

## 5. Semantic index の最小設計

過剰設計を避け、runtime packageや公開CLIへ新しいAPIを増やさない。

- `tools/semantic_index.py` を dependency-free (`ast`, `argparse`, `json`, `pathlib`) で追加する。
- sourceをimport/execせず、module docstringだけをASTから読む。
- exact heading の `Purpose`、`Use when`、`Constraints` が全てある headerだけを収録する。
- 一部だけ存在する malformed semantic header は path付きerrorにし、通常の既存docstringは無視する。
- manifestは path順の JSON listとし、各 itemは `path`、`purpose`、`use_when`、`constraints` だけを持つ。
- 既定はrepository内のPython sourceを走査し、cache/generated/output/vendor領域を除外する。
- positional pathで走査範囲を狭められ、既定はstdout、`--output FILE` 指定時だけ書き込む。
- manifest自体はcommitしない。source headerから常に再生成できる一時成果物とする。
- `tests/devtools/test_semantic_index.py` で multiline、bullet、skip、malformed、syntax error、決定的順序、
  CLI outputを検証する。

想定使用方法:

```bash
python3 tools/semantic_index.py
python3 tools/semantic_index.py src/grafix/core --output /tmp/grafix-semantic-index.json
```

## 6. 実装アクション

### Phase 0: 承認後の baseline

- [x] HEAD、branch、`git status --short` を再確認し、並行差分を記録する。
- [x] 対象候補をAST inventory化し、既存 module docstring と先頭 comment の consumer を確認する。
- [x] 対象限定の import-free syntax check と既存 focused testsをbaselineとして実行する。

Baseline:

```text
src/grafix: 296 modules AST parse成功
architecture + lazy facade + public type graph: 51 passed in 6.43s
```

system `python3` にはpytestがないため、repositoryで既存利用している
`/opt/anaconda3/envs/gl5/bin/python` をtest runnerに用いる。

### Phase 1: Convention を正本化

- [x] `docs/agent_docs/documentation.md` の旧「どこで・何を・なぜ」規約を新形式へ置換する。
- [x] 必要なら `core/effects/AGENTS.md` を新形式と矛盾しない短い表現へ同期する。
- [x] header line数、必須field、optional field、既存docstring統合規則をindex testでも固定する。

### Phase 2: Source-of-truth headers

- [x] public facade / API / entry pointへ routing headerを追加する。
- [x] authoring/catalog/identity/evaluation/Geometry/pipelineの正本へ追加する。
- [x] parameter domainのquery/command/session/persistence境界へ追加する。
- [x] geometry kernelと、類似するeffect/primitiveへ局所invariant付きで追加する。

### Phase 3: Application / export / tooling headers

- [x] exportのencode/staging/publish/path/provenance境界へ追加する。
- [x] interactive runtimeのcomposition/resource owner/IPC/reload境界へ追加する。
- [x] Parameter GUIのmodel/view/render/commit/controller境界へ追加する。
- [x] CLI、benchmark harness、agent tool、共有test helperへ選択的に追加する。
- [x] 各段階で判断できないfileは推測せず候補一覧から外し、理由を記録する。

### Phase 4: Semantic index

- [x] AST parserとrepository walkerを実装する。
- [x] compact manifestのstdout/`--output` CLIを実装する。
- [x] parser/CLIのunit testとreal-repository smoke testを追加する。
- [x] index生成結果を読み、header対象数と必須fieldの完全性を確認する。

### Phase 5: Consistency review

- [x] headerだけをpath順に抽出して横断レビューする。
- [x] Purpose重複、曖昧なUse when、矛盾するConstraints、15行超過、実装手順の再述を修正する。
- [x] `Side effects` と `See` が本当にroutingに必要な箇所だけにあることを確認する。
- [x] 変更前後のASTから、module docstring以外の構文木が同一であることを検査する。

### Phase 6: Validation

- [x] `python3 -m compileall` または全変更Python fileの`ast.parse`を実行する。
- [x] `ruff check src/grafix tests tools/semantic_index.py` を実行する。
- [x] 変更Python fileへ `ruff format --check` を実行する。
- [x] `mypy src/grafix` を実行する。
- [x] `PYTHONPATH=src pytest -q -p no:cacheprovider` を実行する。
- [x] semantic indexを再生成し、path順、field限定、source本文非収録を確認する。
- [x] 完了項目を本書へ反映し、未完・既存failure・未変更重要fileを明示する。

Validation summary:

```text
semantic headers: 194 files; 5〜15 lines; Purpose exact duplicates 0
semantic index tests: 16 passed
changed/new Python AST parse: 194 passed
existing changed Python module-docstring-only AST diff: 192 passed
ruff check: passed
ruff format --check: 74 baseline-clean files and 2 new files passed;
  118 files were already formatter-dirty at HEAD and remain untouched outside module docstrings
mypy: 4 pre-existing errors in font_resources.py and effects/boolean.py
pytest: 4394 passed, 1 pre-existing inventory-count failure
  (tests/sketch/test_active_sketch_entrypoints.py expects 57, current checkout discovers 64)
```

`compileall` はread-onlyな `.agents/skills` 配下で `__pycache__` を作れず失敗したため、全変更・新規
Python 194 filesへの `ast.parse` を代替のsyntax validationとした。`mypy`とpytestのfailureはいずれも
module docstring以外のASTがHEADと同一である対象またはcheckout inventory由来で、今回の実装変更ではない。

## 7. 実装中に維持する境界

- production codeの実行文、signature、type hint、decorator、public symbol、import順を変更しない。
- `from __future__` の前にはmodule docstring以外の実行文を置かない。
- module `__doc__` をCLI/example説明に利用するconsumerを壊さない。
- generated fileや依頼外差分を編集・再生成・整理しない。
- semantic indexはsource headerの派生物であり、別の意味情報を持つDBや手書きmanifestを作らない。
- 新しいcompatibility wrapper、registry、runtime hook、import-time filesystem scanを追加しない。

## 8. 現時点で確認した architecture 上の曖昧さ

1. planar frameには入力ring由来の向きとworld基準canonical frameの二系統があり、使い分けがoverviewだけでは
   十分に明文化されていない。
2. planar inputが不適格な場合、effectごとに例外・入力維持・空結果が混在する。共通constraintを推測しない。
3. canvas値は通常は論理座標で、G-code境界だけmmとして解釈される一方、一部operation docstringにmm表記がある。
4. coreはapplication filesystem policyを持たないが、font/package asset/fingerprint用の限定的I/Oは存在する。
5. `devtools/` に公開CLI、repository保守script、resource generator、benchmark providerが同居する。
6. `sketch/main.py` がstable entry pointか現在作品かをsource構造だけでは確定できない。

これらは今回のheaderで勝手に統一せず、確実な局所情報だけを記述する。
