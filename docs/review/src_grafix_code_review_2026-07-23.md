# `src/grafix` コード再レビュー（2026-07-23）

- 基準コミット: `b3d04a0`
- 対象: 現在の working tree の `src/grafix/`、関連する `tests/`、`architecture.md`
- 観点: アーキテクチャ、責務の分離、美しさ、シンプルさ、可読性
- 規模: `src/grafix` 280 Python files / 95,273 行、`tests` 311 Python files / 78,050 行
- 性質: 実装変更を伴わない静的レビューと限定的な動的確認

> 作業開始時点で working tree には多数の既存変更と未追跡ファイルがあった。本書はそれらを含む
> 現在の状態を対象にしており、`b3d04a0` そのものだけのレビューではない。このレビューでは既存の
> source/test 差分を変更していない。

## 1. 結論

Grafix の中心設計は良い。不変な Geometry DAG、generation ごとの immutable catalog、型付き cache
identity、明示的な resource owner、render と export の分離は、約 9.5 万行の本体を支える骨格として
十分に強い。前回レビュー後の変更により、parameter storage、runtime config loader、公開
`OperationInfo`、`RenderSession` の cleanup、GUI session state なども明確になった。全面的な再設計は
不要である。

一方、設計文書が明快になったことで、実装に残る境界違反が以前より見えやすくなっている。今回の
最優先事項は次の三点である。

1. `core.authoring_loader` から filesystem 探索と Python import 実行を外へ出す。
2. `api.variation_batch` から codec、staging、publish transaction を `export` へ移す。
3. constructor・standalone helper・reload 失敗時の cleanup を、既存の共通契約へ揃える。

これらは新しい framework を導入する問題ではない。既に存在するレイヤと `CleanupErrors` を使い、
**副作用を持つ場所、state の寿命、失敗時の rollback 単位を一致させる**のが最も単純で美しい。

静的確認の範囲では即時の重大な計算誤りは見つからなかった。ただし、初期化途中の resource leak は
現実に起こり得るため、cleanup の所見は正しさの問題として High にした。

## 2. 確認方法と結果

### 2.1 実施した確認

- package 間 import、I/O、process-global state、resource acquisition/cleanup の追跡
- 大きな coordinator、GUI bridge、effect/primitive の変更理由と制御フローの確認
- 前回レビューと現在の設計文書・実装の差分確認
- AST/source-shape test が保証している内容と、保証していない capability の確認
- `import grafix.core.geometry` 時の module load probe

### 2.2 検証結果

```text
tests/architecture: 37 passed
ruff check src/grafix tests/architecture: All checks passed!
```

限定 probe では `import grafix.core.geometry` だけで `grafix.*` module が 102 個 load され、
`grafix.export.*`、`grafix.parameter_storage`、`grafix.runtime_config_loader` も含まれた。

full pytest、mypy、GUI/manual test は今回実行していない。したがって、本書は全機能の回帰保証ではなく、
設計・責務・可読性を中心とするレビューである。

## 3. 良い点

### 3.1 中心概念が少なく、意味が強い

- `Geometry` を mutable な座標列ではなく immutable recipe/DAG として扱っている。
- operation、preset、evaluation config を generation ごとの snapshot に固定している。
- quality、config、operation version、外部 asset を cache identity へ反映している。
- `Layer` が Geometry と style を分け、geometry cache の再利用を可能にしている。

creative coding の書きやすさと、reload・parallel evaluation・headless render の再現性を同じモデルで
両立している点は美しい。

### 3.2 設計文書とコードの距離が短い

`architecture.md:14-22` は中心原則を五点に絞り、`:27-69` は package の責務と依存方向を明示する。
import graph、kernel 境界、公開 capability などは architecture test に接続されている。文書が単なる
理想像ではなく、変更判断の基準として機能している。

### 3.3 前回レビューの主要課題は大きく改善した

- ParamStore の filesystem mutation は top-level `parameter_storage` へ移った。
- runtime config の immutable value と探索/I/O が分離した。
- 公開 `OperationInfo` から evaluator capability が除かれた。
- `RenderSession` は共通 cleanup contract を利用するようになった。
- `runner.run()` は `_InteractiveApplication` へ lifetime を委譲するようになった。
- widget 固有の GUI state は `ParameterGuiSessionState` へ移った。
- `_MpDrawState`、`PresentedFrameState`、数値 kernel など、寿命または意味で切った owner が増えた。

分割が「class 数を増やすこと」ではなく、state/lifetime の境界を明示する方向で行われている点が良い。

### 3.4 基本的な局所品質が高い

型注釈、NumPy style docstring、immutable DTO、入力検証、transactional cache、no-clobber publish などが
広く一貫している。今回の Ruff と architecture test も成功しており、雑な未整理コードが全体を
支配している状態ではない。

## 4. Findings summary

| ID | 優先度 | 主な観点 | 要約 |
|---|---|---|---|
| R2-001 | High | アーキテクチャ、責務 | `core.authoring_loader` が filesystem 探索・compile/exec・process-global import state を所有する |
| R2-002 | High | アーキテクチャ、単純性 | `api.variation_batch` が export codec・staging・publish transaction を実装する |
| R2-003 | High | ownership、正しさ | 初期化・生成失敗時の cleanup が不完全で、resource leak と元例外の隠蔽が起こり得る |
| R2-004 | Medium | 依存方向、明示性 | `export.output_paths` が ambient な runtime config 探索へ逆依存する |
| R2-005 | Medium | encapsulation、責務 | `parameter_storage` が `ParamStore` の private runtime を直接変更する |
| R2-006 | Medium | レイヤ分離 | Parameter GUI leaf が concrete な `CaptureService` に依存する |
| R2-007 | Medium | 状態所有、可読性 | `store_bridge.py` に query、cache、command、render commit が集中し、cache owner が module-global に隠れる |
| R2-008 | Medium | 美しさ、可読性 | 複数の長大関数が独立した処理 phase と重複分岐を一つの制御フローへ埋め込む |
| R2-009 | Low | 単純性、import 境界 | root facade の eager import により core submodule import でも外層を大量初期化する |
| R2-010 | Low | テスト設計、変更容易性 | 一部 architecture test が意味より helper 名・call 数・source shape を固定する |

優先度の意味:

- **High**: resource safety、主要な依存規則、変更単位に直接影響する。
- **Medium**: 直ちに誤結果になるとは限らないが、ownership と理解コストを継続的に悪化させる。
- **Low**: 現状でも動作するが、import、navigation、refactor の摩擦を増やす。

## 5. 詳細

### R2-001 [High] `core.authoring_loader` が application/infrastructure capability を所有する

#### 根拠

- `architecture.md:58-66` は、`core` が file 探索を持たず、top-level loader が I/O と core value を
  接続すると定義する。
- `src/grafix/core/authoring_loader.py:93-104` は `rglob()` と `read_bytes()` で source tree を読む。
- 同 `:107-119` は `RuntimeConfig` から authoring root を選ぶ。
- 同 `:140-170` は独自 loader で source を `compile()` / `exec()` する。
- 同 `:240-257,297-318` は `sys.modules` と `sys.meta_path` を一時変更する。
- `src/grafix/interactive/runtime/source_reload.py:376-509,586-630` に、snapshot loader/finder、
  package install/remove、compile/exec というほぼ同じ import engine がもう一つある。

#### 問題

immutable declaration/catalog は domain contract だが、source discovery、Python module 実行、
`sys.meta_path` の排他制御は application/process boundary である。現在は `core` を import するだけで
filesystem と process-global import state を操作できる capability が同じレイヤに入る。

また、初期 authoring load と reload が別々の snapshot importer を持つため、cleanup、lock、relative
import の仕様が将来ずれやすい。これは generic plugin framework が足りないのではなく、同じ副作用を
二箇所が所有していることが問題である。

#### 推奨

1. `AuthoringDefinitionsRecipe`、declaration、catalog snapshot などの不変値だけを `core` に残す。
2. source tree 探索・candidate import・config からの root 選択を top-level
   `grafix.authoring_loader` へ移す。旧 import path の shim は作らない。
3. initial load と reload が共有するのは、snapshot importer と process-global mutation の狭い実装だけに
   する。reload policy や diagnostics まで汎用化しない。
4. `core` での `sys.meta_path` mutation、`compile/exec`、source tree walk を禁止する capability test を
   追加する。

---

### R2-002 [High] `api.variation_batch` が export の内部実装を再所有する

#### 根拠

- `architecture.md:31,36,65-67` は `api` を facade/composition root、`export` を encoding、output path、
  staging、publish の owner と定義する。
- `src/grafix/api/variation_batch.py` は 678 行あり、`json`、`os`、`shutil`、`tempfile` を直接使う。
- 同 `:278-385` は render loop に加えて working/final generation と summary 公開を制御する。
- 同 `:413-448` は staging directory と no-clobber generation path を作る。
- 同 `:478-545` は capture manifest の path 書換え、backup、`os.replace()`、rollback を行う。
- 同 `:578-647` は contact sheet SVG codec、`:650-670` は fsync/link/replace による text publish を行う。
- variation 名の slug 規則も `api/variation_batch.py:548-551`、
  `interactive/parameter_gui/variation_thumbnail.py:19-26` などへ分散している。

#### 問題

公開 API の一関数が、次の二つの変更理由を同時に持つ。

1. variation snapshot を適用し、`RenderSession` をどの順序で呼ぶか。
2. artifact をどう encode、stage、relocate、publish、rollback するか。

前者は API composition、後者は export transaction である。現在は manifest schema や atomic publish の
変更でも API module を編集する必要があり、既存 `export.capture_*` の責務と並行した実装が増える。

#### 推奨

- API には入力検証、variation 選択、store の transient rollback、render 順序だけを残す。
- batch artifact DTO、contact-sheet encoder、manifest relocation、generation publisher を
  `grafix.export.variation_batch` へ移す。
- class hierarchy は作らず、immutable plan/result と数個の pure/transaction 関数で十分である。
- filename component の正規化規則を export 側の一つの関数へ揃える。
- architecture test に「`api` が fsync/link/replace/staging codec を所有しない」ことを capability として
  追加する。

---

### R2-003 [High] cleanup/ownership 契約が一部の失敗経路で途切れる

#### 根拠

- `src/grafix/interactive/runtime/draw_window_system.py:241-286` は `PerfCollector`、
  `RecordingSession`、`CaptureQueue`、`SceneRunner` を順に取得する。
- 同 constructor の失敗 cleanup `:293-327` は scene runner、capture queue、renderer、window だけを
  登録し、取得済みの `RecordingSession` と `PerfCollector` を閉じない。
- `src/grafix/interactive/runtime/perf.py:86-102` の trace writer は constructor で thread を開始し、
  `PerfCollector.close()` `:554-570` が flush/終了を担う。
- `src/grafix/core/pipeline.py:109-124,188-194` は session/resource/store を手動所有し、先頭の
  `close()` が失敗すると後続を実行できない。
- `src/grafix/core/realize.py:896-907` も resource と cache store を逐次 close する。
- `src/grafix/interactive/runtime/scene_runner.py:150-166` は generation の部分構築 cleanup を逐次実行し、
  同 `:377-387` は replacement startup 失敗時の cleanup error を無条件に捨てる。

#### 問題

通常終了では `CleanupErrors` により全 cleanup を試す設計になっているが、constructor や convenience
helper では同じ契約が貫徹していない。trace 有効時に後続 constructor が失敗すると writer thread が
残り得る。cleanup 自体が失敗した場合は、未解放 resource が増えるか、逆に本来の startup error が
隠れる。

#### 推奨

1. resource は取得直後に cleanup stack へ登録し、所有権を移した時だけ登録を外す。
2. 新しい abstraction を作らず、既存 `CleanupErrors(initial_error=...)` を constructor、generation、
   standalone helper でも使う。
3. `RealizeSession` が dependency 省略時に所有できる経路では、外側で同じ resource を手動所有しない。
4. acquisition point ごとの injected failure test を追加し、全 closeable が一度閉じること、primary error が
   保存されること、secondary cleanup error も観測可能であることを確認する。

---

### R2-004 [Medium] `export.output_paths` が ambient runtime config 探索へ依存する

#### 根拠

- `src/grafix/export/output_paths.py:14-15` は core の `RuntimeConfig` だけでなく、top-level
  `grafix.runtime_config_loader.runtime_config` も import する。
- `output_path_for_draw()` `:309-371` は `config=None` のとき `runtime_config()` を呼ぶ。
- `architecture.md:61` の export dependency と `:65-66` の I/O composition 方針では、config discovery は
  `api` / `interactive.runtime` が接続する責務である。

#### 問題

同じ path 関数が、明示的な `RuntimeConfig` からの pure mapping と、CWD/HOME/YAML discovery の両方を
行う。呼び出し結果が ambient environment に依存し、`export` 単体のテストと再利用性を弱める。

#### 推奨

- lower-level `output_path_for_draw()` では `RuntimeConfig` または必要な path value を必須にする。
- config 省略の convenience は `api` / `devtools` の入口だけに置く。
- `export -> runtime_config_loader` の import を architecture test で禁止する。

---

### R2-005 [Medium] storage adapter が `ParamStore` の private runtime を直接変更する

#### 根拠

- `src/grafix/parameter_storage.py:41-50` の `_set_load_result()` は
  `store._runtime_ref()` を呼び、`load_provenance` と `load_diagnostics` を変更する。
- この helper は missing、partial、recovery、quarantine を含む多数の read/recovery path から使われる。
- `ParamStore._runtime_ref()` は `src/grafix/core/parameters/store.py:962-963` の private representation
  escape hatch である。

#### 問題

filesystem mutation を `core` から出した方向は正しいが、外側 adapter が aggregate の private backing
state を知るようになった。runtime 表現の変更が storage へ波及し、load metadata の owner が
`ParamStore` なのか `ParameterSession` なのかも曖昧になる。

#### 推奨

- 第一候補は、read/recovery result を `store + provenance + diagnostics` の immutable result として返し、
  `ParameterSession` が session-level load state を所有する形である。
- load metadata を本当に `ParamStore` の意味論へ含めるなら、storage が private ref を触るのではなく、
  core 側の一つの明示的な domain operation だけを通す。
- private field ごとの setter を増やす方法は避ける。

---

### R2-006 [Medium] Parameter GUI leaf が concrete export service に依存する

#### 根拠

- `src/grafix/interactive/parameter_gui/variation_thumbnail.py:10,47-65` は
  `CaptureFrame` / `CaptureService` を runtime import し、export callback を構築する。
- `src/grafix/interactive/parameter_gui/variation_panel.py:172-198` も concrete service を GUI callback へ
  適合する処理を持つ。
- `src/grafix/api/runner.py:365-399` は既に output path、draw window の capture service、frame provider、
  canvas size をすべて持つ composition owner である。
- `architecture.md:63-67` は GUI leaf を composition layer から独立させ、外側で protocol/callback へ変換する
  方針を示す。

#### 問題

GUI 自体は thumbnail の「名前を受け取り Path を返す callback」だけを必要とする。それにもかかわらず、
encode/publish を行う concrete export service の型と呼び方を leaf が知っている。export API の変更が GUI
leaf へ伝播し、headless な GUI model test も外層の概念を抱える。

#### 推奨

- `CaptureService -> VariationThumbnailCapture` の adapter と output path policy を `api.runner` または
  `interactive.runtime` へ移す。
- GUI package には `VariationThumbnailCapture` / `VariationThumbnailPreview` の callable contract と表示
  state だけを残す。
- `interactive/{gl,midi,parameter_gui} -> grafix.export` の禁止 test を追加する。

---

### R2-007 [Medium] `store_bridge.py` に異なる変更理由と隠れた cache owner が集中する

#### 根拠

- `src/grafix/interactive/parameter_gui/store_bridge.py` は 1,435 行ある。
- 同 `:77-91` は table model、default catalog、table view、visibility、search corpus の五つの
  process-global cache/counter を持つ。
- 同 `:163-627` は row ordering、catalog metadata、table model 構築を行う。
- 同 `:629-1113` は model/view cache、visibility、search/filter を扱う。
- 同 `:1116-1435` は GUI edits を core commands/history/render commit へ変換する。
- `ParameterGuiSessionState` は `session_state.py:50-83` で GUI-instance lifetime と `table_view` を所有するが、
  その派生 cache は session の外に残る。
- `architecture.md:442` は semantic state を process-global cache/service locator に置かない方針である。

#### 問題

`WeakKeyDictionary` により基本的なメモリ保持は抑えられている。しかし、cache の寿命と reset 契機は
module-global のままで、複数 GUI instance、catalog generation、test isolation の意味が読み取りにくい。
さらに query/model、cache policy、edit command、render commit の変更が一ファイルへ集まり、レビュー時に
無関係な 1,400 行を横断する必要がある。

#### 推奨

1. table model/view/search の派生 cache を小さな session-owned cache object とし、
   `ParameterGuiSessionState` と同じ寿命にする。
2. immutable table query/view 構築と、edit DTO を domain command へ commit する処理を別 module に分ける。
3. effect-chain 固有の並びだけを明示的な特殊ケースとして残し、primitive/preset の block 組立てと
   arg-index lookup はデータ駆動で共通化する。
4. service locator や class-per-function は導入しない。二つか三つの明確な変更理由に切るだけでよい。

---

### R2-008 [Medium] 長大関数が独立した処理 phase を隠している

#### 根拠

AST で現在の関数範囲を確認すると、代表例は次のとおりである。

| 関数 | 範囲 | 行数 | 主な混在責務 |
|---|---:|---:|---|
| `effects.drop.drop()` | `drop.py:131-454` | 324 | probability、scalar/vector sampling、line/face output |
| `effects.partition.partition()` | `partition.py:213-447` | 235 | region、density、sampling、Voronoi clipping、packing |
| `effects.displace._apply_noise_to_coords()` | `displace.py:478-789` | 312 | noise strategy、coordinate transform、mask、output |
| `laplace_field_grid()` | `laplace_field_grid.py:348-594` | 247 | 31 引数、三 mapping strategy、共通 u/v 走査、境界 |
| `snippet_for_block()` | `snippet.py:310-638` | 329 | block 判別、row 変換、複数 emitter |
| `_render_cc_cell()` | `table.py:1147-1372` | 226 | MIDI state transition、label/tooltip、ImGui rendering |
| `render_parameter_table()` | `table.py:1528-1798` | 271 | collapse、header、reorder、row table、modal |

行数だけを問題にしているわけではない。例えば `snippet_for_block()` では rows から kwargs を作る同型処理が
三系統にあり、`_render_cc_cell()` では scalar/vec3 の MIDI learn 遷移が重複する。数値関数でも strategy
選択、検証、走査、packing が一つの局所変数空間を共有し、ある phase の修正時に全分岐を読み直す必要がある。

#### 推奨

- 任意の短い helper へ機械的に分割せず、入力と出力が説明できる phase 単位の pure helper を抽出する。
- `drop`: probability field、selection mask、geometry packing。
- `partition`: region construction、site sampling、Voronoi clipping、output packing。
- `laplace_field_grid`: strategy から `(map_fn, validity)` を選び、共通 u/v emitter を一つにする。
- GUI: MIDI learn の state transition を pure にし、ImGui rendering と分ける。snippet は block 種別ごとの
  emitter にする。
- 分割前後の fast-path parity、empty/degenerate case、seed determinism を test で固定する。

`Strategy` class 階層や汎用 visitor は不要である。関数の上から「検証 → plan → execute → pack」が読める
程度の分割が最も単純である。

---

### R2-009 [Low] root facade の eager import が core-only import を重くする

#### 根拠

- `src/grafix/__init__.py:7-34` は `grafix.api` の公開 surface を eager import する。
- `src/grafix/api/__init__.py:9-30` は DSL だけでなく export、render、variation batch を eager import する。
- `run` だけは同 `:67` 以降で遅延 import されており、既に lazy facade の先例がある。
- clean process の限定 probe では、`import grafix.core.geometry` で 102 個の `grafix.*` module が load され、
  `grafix.export.capture`、`grafix.parameter_storage`、`grafix.runtime_config_loader` も含まれた。

#### 問題

Python は submodule より先に package `__init__` を実行するため、domain-only の test/tool でも外層の import
graph を初期化する。現在直ちに cycle があるという意味ではないが、core の独立性を実行時に弱め、import
時間と将来の循環依存リスクを増やす。

#### 推奨

- root の公開名を PEP 562 `__getattr__` などで必要時に解決し、`.pyi` は現状の静的 surface を保つ。
- まず export/render/variation batch の重い名前だけを遅延化し、複雑な lazy registry は作らない。
- smoke test で core submodule import 時に export/storage/config loader が未 load であることを固定する。

---

### R2-010 [Low] 一部の architecture test が意味より source shape を固定する

#### 根拠

`tests/architecture/test_implementation_quality.py` には、次のような検査がある。

- `:36-51`: 特定 helper 名の定義数を数える。
- `:54-69`: source string の存在/非存在を検査する。
- `:72-82`: `_DrawTask.__post_init__` という実装名を禁止する。
- `:123-147`: `_validate_export_request` の call 数を正確に 2 とする。
- `:169-224`: DTO field/property の完全一致 allowlist を持つ。
- `:251-276`: 過去の private attribute 名を禁止し、現在の attribute 名を要求する。

`tests/architecture/test_runner_composition_boundary.py:15-57` も class 名、owner 数、public `run()` 内の
AST shape を強く固定する。

#### 問題

import 禁止、capability 非露出、cycle 非存在の test は価値が高い。一方、helper 名や call 数の固定は、同じ
意味を保つ読みやすい refactor でも失敗し、逆に別名で同じ責務が逆流する変更を見逃す。test が設計を守る
のではなく、現在の文章構造を凍結する箇所がある。

#### 推奨

- import graph、公開 capability、resource ownership の negative test は維持する。
- call 数や helper 名ではなく、公開 API の behavior、失敗時の cleanup、禁止 package/capability を検査する。
- exact field allowlist は「evaluator/owner を公開しない」など本当に security/capability 境界である場合だけ
  残し、無関係な derived property まで禁止しない。
- coordinator の大きさは一時的な regression gate として使う場合も、移行完了後は semantic test へ
  置き換える。

## 6. 小さな局所改善候補

次は上記の boundary 問題より優先度が低いが、関連箇所を編集する際に同時に直すとよい。

- `api/effects.py:327-330` は `isinstance()` の真偽どちらでも同じ `n_inputs = step.n_inputs` を実行する。
- 16 files、59 箇所で `radius_f = radius` のような変換を伴わない型接尾辞付き別名があり、正規化済みの
  ように見えるノイズになっている。実変換がない場合は元の型付き引数を使う。
- `api/primitives.py:107-134`、`api/effects.py:148-160`、`api/_operation_selector.py:55-69` では、既に作った
  selector spec を freeze/resolve 時に再度正規化し、schema fingerprint を再計算する。公開境界で作った
  immutable selector context を内部が信頼すれば、制御フローと hot path の両方を単純化できる。
- `MpDraw` は process/restart/queue の owner としては凝集しているが、`mp_draw.py` は 1,925 行、class は
  約 1,000 行ある。次に触る際は ownership を分散せず、IPC message/worker entrypoint を module 分離し、
  telemetry の多数の scalar proxy を immutable stats snapshot にまとめると navigation が改善する。

## 7. 推奨する改善順序

### Phase 1: resource safety

1. `DrawWindowSystem` の部分初期化 cleanup に `RecordingSession` と `PerfCollector` を含める。
2. `pipeline.py`、`realize.py`、`SceneRunner` の cleanup を `CleanupErrors` 契約へ揃える。
3. acquisition failure/cleanup failure の regression test を追加する。

### Phase 2: package boundary

1. authoring source loader/import engine を `core` から top-level へ移す。
2. variation batch の codec/staging/publish を `export` へ移す。
3. `export.output_paths` の runtime config discovery を composition root へ移す。
4. GUI leaf から concrete export dependency を外す。

### Phase 3: ownership と可読性

1. Parameter GUI の派生 cache を session owner へ移す。
2. `store_bridge` を query/cache と command commit の変更理由で分ける。
3. 数値/UI の長大関数を phase 単位で分ける。
4. source-shape test を semantic/capability test へ置き換える。

この順序なら、先に失敗時の正しさを確保し、その後に依存方向を直し、最後に局所的な読みやすさを改善
できる。大規模な同時 rewrite は不要である。

## 8. 最終評価

Grafix は「設計が不在で巨大化した codebase」ではない。むしろ、中心モデルと文書は既に強く、前回の
改善で owner もかなり明示された。今回見つかった問題の多くは、**新しい境界を作る問題ではなく、既に
宣言した境界を最後まで貫徹する問題**である。

したがって、最も美しい次の一手は abstraction の追加ではない。I/O と process-global mutation を外層へ
戻し、cleanup を一契約へ揃え、GUI cache を instance lifetime へ戻し、長い関数を意味のある phase にだけ
分けることである。これにより、アーキテクチャ、責務分離、シンプルさ、可読性を同時に改善できる。
