# `src/grafix` コードレビュー（2026-07-22）

- 対象コミット: `b3d04a0`
- 対象: `src/grafix/`、関連する `tests/`、`architecture.md`、`docs/agent_docs/`
- 観点: アーキテクチャ、責務の分離、美しさ、シンプルさ、可読性
- 性質: 実装変更を伴わない静的確認と限定的な動的 probe

## 1. 結論

Grafix の中心設計は良い。immutable な Geometry DAG、generation ごとの catalog、typed cache
identity、resource owner の寿命、render と export の分離は、`architecture.md` と実装の双方でかなり
明確になっている。依存境界を AST test で固定している点も優れている。全面的な再設計は不要である。

一方、現在は「設計上の owner は分けたが、実装上の制御と状態はまだ中央へ集まりやすい」段階にある。
特に次の 4 点を優先すべきである。

1. `core` が quarantine/recovery policy を持ち、fsync を伴う atomic write に依存している。
2. interactive coordinator が依然として多数の state machine と生成責務を抱えている。
3. `py.typed` package の主要 authoring decorator が利用者の型を `Any` に落としている。
4. source reload が `KeyboardInterrupt` / `SystemExit` まで通常の reload failure に変換する。

前回のアーキテクチャ改善で抽出された owner 自体は有効であり、巻き戻す必要はない。次の改善では
新しい大規模 framework や互換 wrapper を足すのではなく、**副作用境界、生成 seam、公開 DTO、例外契約を
狭くする**のが最もシンプルである。

## 2. 良い点

### 2.1 設計原則が明文化され、テスト可能な規則になっている

- `architecture.md:15-22` は DAG、catalog、cache identity、resource ownership、coordinator の役割を
  短い原則として定義している。
- `architecture.md:27-64` は package 間の依存方向を明示している。
- `tests/architecture/test_dependency_boundaries.py` は import graph、renderer context、coordinator への
  旧責務の逆流などを AST で検査している。
- 今回も architecture test 22 件は成功した。

設計文書が願望だけで終わらず、実装の退行防止へ接続されている点は、この規模の creative coding
framework として大きな強みである。

### 2.2 immutable value と lifetime owner の考え方が一貫している

- Geometry は座標配列ではなく遅延評価 recipe として扱われる。
- operation/preset は generation ごとの immutable catalog に固定される。
- evaluation context、external dependency、quality が typed fingerprint / cache key に入る。
- standalone session と borrowed dependency の close 契約が明示されている。

この骨格は並列評価、reload、headless render、interactive preview を同じ意味論で扱うための良い土台に
なっている。

### 2.3 render、export、interactive leaf の分離は概ね明快である

- headless `RenderSession` は frame を作り、保存は export 側へ委ねる。
- capture の encode、staging、publish が export package に集約されている。
- GUI table は edit DTO を返し、domain mutation を controller/command へ寄せる方向になっている。
- numerical kernel を `core/geometry_kernels/` へ分け、effect sibling import を禁止している。

### 2.4 基本的な静的品質は高い

- `ruff check src/grafix tests`: 成功
- `mypy src/grafix`: 275 source files、問題なし
- source: 275 Python files、約 94,000 行
- tests: 299 Python files、約 75,000 行、3,824 tests を収集

ただし、後述の通り通常の mypy 成功だけでは公開 decorator の型消失や untyped kernel を検出できない。

## 3. Findings summary

| ID | 優先度 | 主な観点 | 要約 |
|---|---|---|---|
| CR-001 | High | アーキテクチャ、責務 | `core` の永続化が依存図にない file I/O capability に依存している |
| CR-002 | Medium | 責務、シンプルさ | coordinator に複数の state transition と生成責務が集中している |
| CR-003 | High | 可読性、公開契約 | `@primitive` / `@effect` が decorated callable を `Any` にする |
| CR-004 | High | 例外契約、可読性 | reload が process-control 例外を通常 failure へ変換する |
| CR-005 | Medium | 責務、美しさ | ParamStore/Memento/Variation/Codec が private snapshot 表現を横断する |
| CR-006 | Medium | アーキテクチャ、単純性 | runtime config の値、探索、I/O、fallback、評価 identity が一体化している |
| CR-007 | Medium | 公開 API、ownership | 公開 facade が内部 evaluator と close 可能な owner を露出する |
| CR-008 | Medium | 例外契約、単純性 | `RenderSession` の cleanup policy が共通 contract と揃っていない |
| CR-009 | Medium | 美しさ、可読性 | exact-type validation と共通 adapter の重複が正常系を埋める |
| CR-010 | Medium | テスト容易性、責務 | `SceneRunner` が `MpDraw` を直生成し、テストが private state に依存する |
| CR-011 | Medium | 可読性、状態所有 | GUI session state の一部が module global に残る |
| CR-012 | Medium | 型、テスト運用 | runtime と型 alias、mypy/pytest 設定の契約が一致していない |

優先度の意味:

- **High**: 公開契約、終了操作、依存境界、主要な変更容易性に直接影響する。
- **Medium**: 現時点で必ず誤結果を返すわけではないが、変更面積と理解コストを継続的に増やす。
- **Low**: 局所的な命名・整理。今回は独立 finding にせず関連項目へ含めた。

## 4. 詳細

### CR-001 [High] `core` の永続化が依存図にない file I/O capability に依存している

#### 根拠

- `architecture.md:56-57` は `core` が fsync や出力 path policy を持たないと定義している。
- `architecture.md:261-265` は load/recovery/終了時 persist の owner を `ParameterSession` としている。
- `src/grafix/core/parameters/persistence.py:13` は `grafix.file_io.atomic_write_text` を import する。
- 同 `:47-97` は decode issue を検出すると `os.replace()` で原本を quarantine し、recovery を保存する。
- 同 `:117-180` の `load_param_store()` は名前に反して原本を移動し得る。
- 同 `:276-322` は保存、prune、recovery unlink まで行う。
- `src/grafix/file_io.py:13-61` が tempfile、mkdir、fsync、replace を実装する。
- `tests/architecture/test_dependency_boundaries.py:179-194` は `core -> grafix.file_io` を禁止しない。
  同 `:197-229` は core 内の直接の `os.fsync` / `os.link` だけを見るため、間接依存を検出できない。

#### 問題

構文上は `core -> export/interactive` を避けているが、意味上は filesystem infrastructure が core に
入り込んでいる。さらに read/decode、修復方針、原本退避、journal 作成、診断生成が一つの call に
結合しており、`load_param_store()` を確認目的で呼ぶだけでも filesystem が変化し得る。

これは `architecture.md` の依存図に記載されていない capability 依存である。

#### 推奨

1. core には純粋な codec、memento、prune decision、`ParamStoreLoadResult` だけを残す。
2. quarantine、recovery journal、atomic save は `ParameterSession`、または application-side の狭い
   `ParamStoreRecoveryService` に置く。
3. 非変更の read adapter、pure な decode、filesystem 変更を伴う `recover/commit` を別 API にする。
4. architecture test に `core -> grafix.file_io` 禁止、または package 間の推移的 capability 検査を足す。

`grafix.file_io` を別名へ移すだけでは解決しない。重要なのは fsync の実装場所ではなく、回復方針と
filesystem mutation の owner である。

---

### CR-002 [Medium] coordinator に複数の state transition と生成責務が集中している

#### 根拠

- `src/grafix/api/runner.py` は 559 行で、`run()` は `:96-559` の 464 行を占める。引数 validation、
  pyglet option、path/session policy、application exit、activation、cleanup/persist を一つに記述する。
- `src/grafix/interactive/runtime/draw_window_system.py` は 1,168 行。
  - `:126-349`: window/renderer、output path、provenance、transport、performance、recording、capture、
    `SceneRunner` を構築する。
  - `:431-570`: provenance token の policy と state transition を持つ。
  - `:658-796`: reload、diagnostics、scene evaluation を扱う。
  - `:886-1075`: 約 190 行の `draw_frame()` が reload、export poll、MIDI、style、evaluate、GL、
    telemetry、recording、capture binding を横断する。
- `src/grafix/interactive/runtime/mp_draw.py` は 1,830 行。`MpDraw` は process lifecycle、queue、snapshot
  replication/ACK、latest-wins scheduling、timeout/restart、stale-result policy、counter を所有する。
- `tests/architecture/test_dependency_boundaries.py:462-546` の coordinator gate は、既知 symbol の再出現を
  禁止する方式であり、新しい state/policy の集中や capability の増加は検出しない。

#### 問題

既存 owner の抽出は成功しているが、coordinator 自身にも別種の状態遷移が多く残る。capture の変更で
frame loop を読み、reload の変更で process/diagnostic/provenance を追い、起動 option の変更で終了時
persist まで確認する必要がある。行数そのものより、変更理由と lifetime owner が複数あることが問題である。

#### 推奨

- `DrawWindowSystem` から provenance/presented-frame/capture binding の状態を狭い owner へ移す。
  reload は新しい coordinator を増やさず、既存 `SourceReloadController` の command を配線するだけにする。
- `MpDraw` の process、snapshot、scheduler は同じ generation/restart lifetime を共有するため、まずは一つの
  aggregate のまま保つ。その内部で pure な scheduling decision と message replication を test 可能な
  collaborator にし、close owner を不用意に増やさない。
- `api.runner.run()` は入力と effective config を確定した後、1 個の application composition object へ
  委譲する。
- 分割基準は関数数や行数ではなく、**state の寿命と失敗時の rollback 単位**にする。

class-per-function や汎用 event bus は不要である。既存の concrete owner を constructor で明示注入する
だけでも、主制御フローはかなり短くなる。

---

### CR-003 [High] 公開 authoring decorator が型を `Any` に落とす

#### 根拠

- package は `src/grafix/py.typed` を配布する。
- `src/grafix/core/operation_authoring.py:224-233` の `primitive()` と `:328-337` の `effect()` には
  戻り値型と overload がない。
- 実装は `:321-325`、`:446-450` で元の callable をそのまま返すため、型としては `ParamSpec` で
  保持できる。
- checked-in stub の `src/grafix/api/__init__.pyi:1927-1928` も未型付け関数を re-export するだけである。
- `src/grafix/core/operation_catalog.py:83-92,143-158` の `schema`、`evaluator`、`cache_policy`、
  `defaults`、`meta` property にも戻り値注釈がない。
- 一時 mypy probe では、`@primitive` / `@effect` 適用後の callable と誤った引数での呼び出しが
  いずれも `Any` となり、エラーにならなかった。

#### 問題

custom operation 定義は Grafix の主要導線である。ここで型が失われると、通常の `mypy src/grafix`
が成功しても、ライブラリ利用者が最も必要とする引数検査が働かない。型注釈が存在するように見えて
実効性がないため、可読性と信頼性の両方を損なう。

#### 推奨

- `ParamSpec` と `TypeVar` を使い、直接適用 `@primitive` と引数付き `@primitive(...)` の 2 形式へ
  overload を定義する。`effect` も同様にする。
- `OperationCatalogEntry` の各 property に具体的な戻り値型を付ける。
- mypy smoke test で、decorated callable の引数型が保存され、不正な呼び出しが失敗することを固定する。

runtime wrapper や cast を追加する必要はなく、現在の「元関数を返す」実装を型へ正確に表現すればよい。

---

### CR-004 [High] source reload が process-control 例外を通常 failure へ変換する

#### 根拠

- `src/grafix/interactive/runtime/source_reload.py:978-1028` は candidate load 全体を
  `except BaseException` で捕捉し、`SourceReloadResult(status="failed")` へ変換する。
- 初期 load は同 `:875-877` で、その summary を cause なしの `RuntimeError` へ変換する。
- 一時 sketch が `KeyboardInterrupt("stop")` を送出する probe では、呼び出し側が受け取ったのは
  `RuntimeError("KeyboardInterrupt: stop")` で、`__cause__` もなかった。

#### 問題

candidate load 中の Ctrl-C、`SystemExit`、通常の sketch error が同じ reload failure になる。終了要求が
通常 failure に変換され、ログと traceback 上も「再読み込み可能な user error」と「process を止める要求」を
区別できない。
同リポジトリの resource cleanup が process-control exception を保持する設計とも不整合である。

#### 推奨

- candidate module cleanup は `finally` または cleanup helper で必ず実行する。
- failure result へ変換する対象は `Exception` に限定する。
- `KeyboardInterrupt` / `SystemExit` は cleanup 後にそのまま再送出する。
- 初期 load の通常例外も、可能なら元例外を cause として保持する。
- initial reload と subsequent reload の両方へ process-control regression test を追加する。

---

### CR-005 [Medium] ParamStore 周辺で private snapshot 表現の境界が曖昧である

#### 根拠

- `architecture.md:221-236` は `ParamStore` を logical state、revision、history integration、rollback の owner
  と定義しており、この集約自体には一貫性がある。
- `src/grafix/core/parameters/store.py:139-186` は parameter state と、それらの atomic mutation に必要な
  revision、observer、rollback marker、snapshot cache を保持する。
- `src/grafix/core/parameters/memento.py:176-241` は `ParamStore` の `_states` / `_meta` を直接読む。
- `src/grafix/core/parameters/variations.py:44-59,197-207,401-404,680-760` は memento を値として保持し、
  その private container を variation 差分・morph・encoding から読む。
- `src/grafix/core/parameters/codec.py:128-130` は variations 側の private encoder を逆に利用する。

#### 問題

`ParamStore` が広い aggregate root であること自体より、snapshot の private representation が memento、
variation、codec の三方向から参照されることが問題である。内部表現の変更が複数の異なる責務へ波及し、
module 間の循環依存を TYPE_CHECKING と private helper で回避する状態になっている。

#### 推奨

- variation が保持できる独立 immutable value、たとえば `ParameterAdjustmentSnapshot` を定義する。
- codec は encoding を所有し、variations module は variation 操作だけを所有する。
- `ParamStore` は atomic revision/history/rollback invariant の owner のままとし、公開 snapshot の生成と
  適用だけを提供する。

field ごとに class を増やす必要はない。まず memento/variation/codec のうち private representation を
横断する辺を 1 本切ることが先である。

---

### CR-006 [Medium] runtime config の値、探索、I/O、fallback、評価 identity が一体化している

#### 根拠

- `src/grafix/core/runtime_config.py:72-177` は immutable config value と subsystem section を定義する。
- 同 `:180-239` は ContextVar binding と CWD/HOME の探索規則を持つ。
- 同 `:408-469` は YAML import、file/package resource の読み込みを行う。
- 同 `:672-964` は UI、G-code、export、MIDI の schema parsing を持つ。
- 同 `:967-1202` は layer merge、探索、fallback、traceback の表示用整形まで扱う。
- `src/grafix/core/realize.py:328-335,880-887` と `src/grafix/core/pipeline.py:104-119` は context/config
  未注入時に `current_runtime_config()` を呼ぶ。未束縛なら CWD/HOME の探索と file read が起こる。
- `RuntimeConfig` 自体は font 探索だけでなく output/sketch/preset path、window position、GUI shortcut、
  PNG、G-code、MIDI まで含む。
- `src/grafix/core/evaluation_context.py:60-97` は `config_path` 以外の dataclass field を再帰的に全て
  canonicalize する。
- 同 `:143-159` はその全値を geometry evaluation fingerprint に含める。
- built-in geometry から config を使う代表経路は font resolution であり、window position、shortcut、
  MIDI、output directory は通常 geometry semantics を変えない。

#### 問題

一つの約 1,230 行の module が domain value、binding、validation、filesystem discovery、fallback policy、
表示用診断という異なる変更理由を持つ。低水準評価 API も明示 config がなければ ambient CWD/HOME と
file content に依存する。

また window 配置や shortcut を変えるだけで CPU/GPU cache identity が変わる。これは誤キャッシュを防ぐ
安全側の設計ではあるが、domain evaluator が UI/export 設定の全体構造を知ることになり、不要な cache
miss と変更波及を生み得る。

custom operation が全 config を暗黙参照できる現契約を維持する限り、fingerprint だけを狭めるのは危険で
ある。したがってこれは hash 実装ではなく、config access contract の問題である。

#### 推奨

- immutable config value と pure な mapping validation を core に残し、YAML/package resource、CWD/HOME
  discovery、fallback/reporting を application-side loader へ移す。
- 低水準評価 API は explicit な `EvaluationContext` / config を要求し、暗黙 discovery は公開 convenience
  API に限定する。
- 統合 YAML 用 `RuntimeConfig` と、評価意味論だけを持つ `EvaluationConfig` を分ける。
- evaluator へ渡す config slice を明示し、外部 asset は既存の external-dependency fingerprint を使う。
- UI/export/MIDI の設定は各 subsystem の composition 時にだけ渡す。

---

### CR-007 [Medium] 公開 facade が内部 evaluator と close 可能な owner を露出する

#### 根拠

- `src/grafix/api/primitives.py:38-71` と `src/grafix/api/effects.py:546-581` の `catalog()` / `describe()` は
  core の `OperationCatalogEntry` をそのまま返す。
- `src/grafix/core/operation_catalog.py:40-92` の entry は declaration、evaluation、evaluator を公開する。
- `src/grafix/api/render.py:328-366` の `RenderSession` は `ParamStore`、`StyleResolver`、`RealizeSession`、
  `EvaluationResources`、`RealizeCacheStore` を返す。
- `evaluation_resources` と `cache_store` は親 session が所有する close 可能な resource owner である。

#### 問題

利用者は catalog inspection のつもりで evaluator implementation まで取得でき、`RenderSession` の子 owner
を親より先に閉じることもできる。内部の lifecycle が公開 API の互換性面へ広がり、architecture が
文書化する親子 ownership を API 利用者が破れる余地がある。

#### 推奨

- catalog inspection には evaluator-free の immutable `OperationInfo` DTO を返す。
- `realize_session`、`evaluation_resources`、`cache_store` は非公開にし、必要なら stats/query/command の
  狭い view だけを返す。
- advanced API が本当に必要なら `grafix.core` の内部型露出ではなく、明示した低水準 public contract にする。
- すでに安定した immutable domain value まで、API 専用 wrapper で二重化する必要はない。

破壊的に整理し、旧型を返す互換 shim は作らない方がこのリポジトリの方針に合う。

---

### CR-008 [Medium] `RenderSession` の cleanup policy が共通 contract と揃っていない

#### 根拠

- `src/grafix/api/render.py:280-290` は `RealizeSession` 構築失敗時に resource と cache を順番に閉じる。
  最初の cleanup が失敗すると次の cleanup は実行されず、元の構築例外も置き換わる。
- 同 `:321-322` の `__exit__` は body の例外情報を使わず `close()` するため、close error が body error を
  置き換え得る。
- 同 `:447-459` の nested `finally` は全 close を試すが、複数失敗時は最後の例外が勝つ。
- fault-injection probe では 3 close は全て呼ばれたものの、最初の `RuntimeError` ではなく最後の
  `KeyError` が送出された。
- 一方、repository には `core.lifecycle.CleanupErrors` があり、別の owner では root error を保持して
  全 cleanup step を試す契約が実装されている。

#### 問題

同じ resource ownership model の中で、どの session を使ったかにより例外優先順位が変わる。
描画・構築 failure の処理中に cleanup も失敗すると、cleanup failure だけが見えて root failure が失われ、
診断が難しい。

#### 推奨

- cleanup step の実行と root error 保持を共通 helper へ統一する。
- `__exit__` は body exception を root として渡し、全 child cleanup を試した後も root を送出する。
  secondary cleanup failure は exception note、診断 callback、または log のいずれかへ残す。
- constructor failure、body failure、複数 close failure、`KeyboardInterrupt` の fault-injection test を
  public owner ごとに追加する。

---

### CR-009 [Medium] exact-type validation と共通 adapter の重複が正常系を埋める

#### 根拠

- source 全体には `raise TypeError` が約 577、`raise ValueError` が約 648、`raise RuntimeError` が
  約 187 あり、exact-type check も多数ある。
- `src/grafix/interactive/runtime/mp_draw.py:169-545` の 7 private IPC DTO は、合計約 263 行の
  `__post_init__` validation を持ち、scalar/container subclass まで逐一拒否する。
- 同 `:1403-1450` の `submit()` は、DTO constructor と重なる validation を持つ。
- `src/grafix/interactive/runtime/export_job_system.py` でも job DTO と `submit()` の検査が重なる。
- 同一 AST の `_grid_spec_from_bbox` が次の 4 箇所に存在する。
  - `src/grafix/core/effects/metaball.py:49-75`
  - `src/grafix/core/effects/growth.py:52-78`
  - `src/grafix/core/effects/reaction_diffusion.py:44-70`
  - `src/grafix/core/effects/isocontour.py:57-83`

#### 問題

確認した IPC/job 経路では、公開入力、decode、内部 trusted value の境界に同種の検査が重なる。
意味のある invariant より subclass 拒否や canonical scalar の再検査が目立ち、正常系の意図が読みにくい。
重複 adapter は診断 code/severity の drift も招き得る。

#### 推奨

- validation は public API、filesystem/IPC deserialize、custom evaluator の戻り値に集中させる。
- private producer が生成した値は型注釈を信頼し、意味的 invariant だけを constructor 一箇所で検証する。
- grid planner は pure kernel のまま維持し、plan result を diagnostic/spec へ変換する薄い effects-side
  adapter だけを共通化する。
- 全件を機械的に削るのではなく、「各 invariant の owner は一つ」を基準にする。

---

### CR-010 [Medium] `SceneRunner` の concrete 生成がテストを private state へ押し出す

#### 根拠

- `src/grafix/interactive/runtime/scene_runner.py:193-208` と reload 時の `:282-296` は `MpDraw` を
  直接構築し、差し替え seam がない。
- そのため `tests/interactive/runtime/test_mp_draw.py` では `runner._mp_draw = cast(Any, fake)` が複数箇所に
  ある。`test_operation_diagnostic_flow.py`、`test_profiler.py` にも同種の private injection がある。
- `tests/interactive/runtime/test_mp_draw.py` 自体も 3,071 行あり、IPC DTO、process lifecycle、timeout、
  stress、SceneRunner sync/mp/reload を一つの file で扱う。
- 確認した window/runtime 系 tests では、production private member の差し替えが複数 file にまたがる。

#### 問題

production の private layout を変えるだけで多数の test が壊れ、mypy を `Any` で迂回する。constructor と
reload が同じ factory contract を使うことも直接検査しにくい。これは test の問題というより、production
composition seam が不足している兆候である。

#### 推奨

- `submit`、`poll_latest`、`latest_successful_result`、`last_submitted_frame_id`、`begin_epoch`、`close` の
  狭い Protocol と、internal `MpDrawFactory` を `SceneRunner` へ注入する。
- default factory は production concrete を返し、公開 API に DI framework を露出しない。
- test は責務別に message、lifecycle、scheduler、SceneRunner、stress へ分割する。
- fake は Protocol 準拠の共有 fixture とし、private assignment と `cast(Any, ...)` を削除する。

---

### CR-011 [Medium] GUI session state の一部が module global に残る

#### 根拠

- `src/grafix/interactive/parameter_gui/session_state.py:29-45` は frame 間 UI state の lifetime owner を
  `ParameterGuiSessionState` と説明する。
- `src/grafix/interactive/parameter_gui/widgets.py:23-24,136-153,182-200,380-388` は font/choice filter を
  process-global dict に保持する。
- `src/grafix/interactive/parameter_gui/table.py:1543,1786-1788` は snippet popup text/focus を
  module global に保持する。
- `src/grafix/interactive/parameter_gui/gui.py:1617-1634` の close ではこれらを解放しない。

#### 問題

別 GUI/session/project で同じ key が現れると以前の filter が再利用され得る。choice filter は選択時に
個別削除されるが、session close では一括解放されないため、操作次第では process lifetime まで残り得る。
documented session owner と実際の寿命が一致しない。module global は引数を減らすが、状態の所在を隠すため
長期的には可読性を下げる。

#### 推奨

filter と popup state を `ParameterGuiSessionState` または専用 `WidgetSessionState` へ移し、render input
として明示的に渡す。新しい global reset hook を追加するのではなく、owner の close と同じ寿命にする。

---

### CR-012 [Medium] 型・テスト運用の文書と実契約が一致していない

#### 根拠

1. **SceneItem**
   - `src/grafix/core/scene.py:15` は `Sequence["SceneItem"]` を再帰 alias に使う。
   - `str` は `Sequence[str]` なので型上は再帰的に適合するが、runtime は同 `:56-60` で `str/bytes` を
     明示拒否する。
   - 一時 mypy probe では `draw() -> str` を `run(draw)` へ渡しても成功した。
2. **mypy gate**
   - `pyproject.toml:70-72` は `python_version` と `mypy_path` だけを設定する。
   - `mypy --disallow-untyped-defs src/grafix` では 23 files、68 errors となる。
   - 特に `core/effects/weave.py:203-535`、`displace.py:375-440` の複雑な Numba kernel に未注釈が残る。
3. **pytest markers**
   - `docs/agent_docs/testing.md:21-24` は `integration` / `e2e` / `perf` marker を案内する。
   - `pyproject.toml:63-64` に marker 登録がなく、実際の test にも該当 mark がない。
   - `pytest --collect-only -m integration` は 3,824 件を全て deselect した。

#### 問題

静的検査・test command が成功しても、利用者が実行時に必ず失敗する型や、実際には選択できない suite を
許している。「pass」の意味が設定の弱さに依存するため、品質 gate の読み取りを難しくする。

#### 推奨

- `SceneItem` は実際に受け入れる `list` / `tuple` などの container へ狭め、runtime と揃える。
- 注釈欠落自体を gate にするため、対象 module ごとに `disallow_untyped_defs = true` を段階適用する。
- Numba kernel は typed Python wrapper と compiled kernel を分け、shape/dtype 契約を wrapper に置く。
- marker を登録して process/GL/integration/perf test へ実際に付与し、unit suite と分離する。

## 5. 推奨する実施順

### Phase 1: 例外と公開契約を先に固定する

1. CR-004: `BaseException` を通常 reload failure へ変換しない。
2. CR-003: decorator の ParamSpec overload と catalog property の型を追加する。
3. CR-008: public session の cleanup/error precedence を統一する。

これらは比較的小さな変更で、user-facing failure と診断品質を直接改善できる。

### Phase 2: 意味上の依存境界を合わせる

1. CR-001: persistence codec と filesystem recovery policy を分離する。
2. CR-007: evaluator-free catalog DTO と resource owner 非公開化を行う。
3. CR-006: config loader/discovery を分離し、`EvaluationConfig` を UI/export config から分ける。

architecture test は module 名の blacklist だけでなく、許可する capability/依存方向を固定する。

### Phase 3: state owner と composition seam を細くする

1. CR-010: `SceneRunner` に狭い `MpDrawFactory` を注入する。
2. CR-002: その seam を使い、provenance/presented-frame state と pure scheduling decision を分離する。
3. CR-005: ParamStore/Memento/Variation/Codec の概念的な循環を一つ切る。
4. CR-011: GUI module global を session owner へ移す。

### Phase 4: 重複と gate を整理する

1. CR-009: grid diagnostic adapter と validation owner を一箇所にする。
2. CR-012: SceneItem、mypy strictness、pytest marker を実態へ合わせる。
3. 巨大 test file は production seam の改善後に責務別へ分割する。

## 6. 完了条件の提案

- `core` から filesystem mutation capability への依存を解消し、再導入を architecture test が検出する。
- read/decode は原本を変更せず、recover/commit だけが filesystem を変更する。
- `DrawWindowSystem.draw_frame()` が poll/evaluate/present/publish の順序と owner command の呼び出しだけを
  持ち、provenance/capture/reload の state mutation を直接実装しない。
- decorated custom operation の正しい signature が `reveal_type` で保持され、不正引数が mypy error になる。
- reload 中の `KeyboardInterrupt` / `SystemExit` が同じ型のまま caller へ届く。
- `RenderSession` は body/root error を優先して送出し、secondary cleanup error を note/診断/log の
  いずれかで観測可能にした上で、全 cleanup step を実行する。
- tests から `runner._mp_draw` への直接代入と、そのための `cast(Any, ...)` がなくなる。
- GUI filter/popup state は module global から参照されず、session owner の close で解放される。
- `pytest -m integration` が実際の integration test を選択する。

## 7. 今回の確認内容と制約

実行した確認:

```text
ruff check src/grafix tests
  -> All checks passed

mypy src/grafix
  -> Success: no issues found in 275 source files

pytest -q -p no:cacheprovider tests/architecture
  -> 22 passed in 3.91s

pytest --collect-only -q
  -> 3,824 tests collected
```

追加で、decorator/`SceneItem` の mypy probe、reload の `KeyboardInterrupt` probe、cleanup の
fault-injection probe、AST による完全重複・import graph・規模の確認を行った。

full pytest、実 GUI、performance benchmark は今回のレビューでは再実行していない。現コミット近傍の
既存証跡は `docs/review/src_grafix_architecture_final_audit_2026-07-22.md` にあるが、本レビューの実行結果とは
区別した。
