# `src/grafix` 実装改善後コードレビュー（2026-07-23）

- 基準コミット: `4bc4cf6`
- 対象: 現在の working tree の `src/grafix/`、関連する `tests/`、`architecture.md`
- 関連文書:
  - `docs/review/src_grafix_code_review_2026-07-23.md`
  - `docs/plan/src_grafix_code_review_implementation_plan_2026-07-23.md`
- 観点: アーキテクチャ、責務の分離、美しさ、シンプルさ、可読性
- 規模: `src/grafix` 285 Python files / 96,580 行
- 性質: 実装変更を伴わない静的レビュー、重点テスト、限定的な動的再現

> レビュー時点の working tree には、前回レビューに基づく実装を含む多数の既存変更・未追跡ファイルが
> あった。本書はそれらを含む現在の状態を対象にしている。このレビューでは既存の source/test 差分を
> 変更せず、本書だけを新規作成した。

## 1. 結論

前回レビューで問題だった大きなレイヤ違反は、かなり良く整理された。filesystem-aware な authoring
loader は `core` の外へ移り、variation batch の publish transaction は `export` が所有し、parameter
load metadata と GUI cache も実際の session owner へ近づいた。不変な Geometry DAG、generation ごとの
catalog、型付き cache identity、明示的な resource owner という中心設計は引き続き強い。全面 rewrite や
新しい汎用 framework は不要である。

一方、改善後の配線には **state の寿命を取り違えた不具合** が残っている。特に次の三点は、設計上の
違和感だけでなく動的に再現できる。

1. lazy facade が Python の標準 import 意味論を壊す。
2. snapshot importer が function-local な relative import を load 時には受理し、実行時に失敗させる。
3. recovery の Keep action が古い parameter schema を保持し、新 generation の正当な引数を削除する。

したがって、現状を「前回計画の機械的完了」とみなすことはできても、「境界と寿命が十分に閉じた状態」
とはまだみなせない。次の改善では、抽象化を追加するよりも、**一つの名前に一つの意味、一つの state に
一つの owner、一つの transaction に一つの確定点**という単純な規則へ戻すべきである。

## 2. 確認方法と結果

### 2.1 実施した確認

- package import graph、公開 facade、runtime introspection の確認
- authoring/config source reload の source capture、module registry、finder lifetime の追跡
- parameter recovery、schema 交換、capture provenance の lifetime 追跡
- GUI command と外部 artifact publish の確定順序の確認
- mutable aggregate、process owner、session cache の変更理由と不変条件の確認
- AST による大規模 module・長大関数の抽出
- 問題候補を最小構成で動的再現

### 2.2 検証結果

```text
focused pytest: 104 passed in 8.36s
ruff check src/grafix tests/architecture: All checks passed
mypy src/grafix: Success: no issues found in 285 source files
```

focused pytest には `tests/architecture`、lazy facade、authoring loader、runner/interactive parameter
recovery、export variation batch の対象 test を含めた。full pytest、GUI manual test、長時間 process
soak は今回実行していない。

静的検証がすべて成功していても、後述の R3-001〜R3-006 は現行 test が固定していない意味論で再現した。
つまり、lint/type/import-shape の品質は高い一方、**複数 subsystem を跨ぐ時間軸の契約**に test gap がある。

## 3. 良い点

### 3.1 前回レビューの主要な依存違反は解消された

- authoring の filesystem capture と import transaction は、`grafix.authoring_loader` と
  `grafix._snapshot_import` に分離された。
- variation batch は API の render orchestration と export の staging/publish transaction に分かれた。
- `export` の output path helper は明示的な `RuntimeConfig` を受けるようになった。
- Parameter GUI leaf は concrete な export service ではなく thumbnail callback contract を受け取る。
- parameter load metadata は `ParamStore` の論理状態から外れ、immutable result と session owner が持つ。
- constructor/reload/standalone helper の cleanup は `CleanupErrors` を中心にかなり統一された。

設計文書の依存方向と production code の距離は、前回レビュー時より明確に短くなっている。

### 3.2 中心 model は引き続き簡潔で強い

- Geometry を不変 recipe/DAG として扱う。
- operation/preset/schema を generation ごとの immutable snapshot に固定する。
- evaluation fingerprint と parameter schema fingerprint を分ける。
- cache と外部 resource を lifetime owner に結び付ける。
- public inspection から evaluator capability を除く。

creative coding の使いやすさ、reload、parallel evaluation、headless render の再現性を、少数の強い概念で
支えている点は美しい。

### 3.3 局所的な分割には良い例が増えた

- `drop.py` と `partition.py` は処理 phase が helper 名から読める。
- `laplace_field_grid.py` の `_GridDomain` / `_MappingPlan` は中間概念の命名が明確である。
- `snippet.py` は共通 kwargs と形式別 emitter を分離している。
- MIDI learn は純粋な state transition と UI adapter の分離へ進んだ。
- `SceneRunner`、`RenderSession`、DWS は所有 resource と close 順を比較的追いやすい。

## 4. Findings summary

| ID | 優先度 | 主な観点 | 要約 |
|---|---|---|---|
| R3-001 | High | アーキテクチャ、単純性、美しさ | lazy facade が package/module と callable の名前を衝突させ、標準 import と introspection を壊す |
| R3-002 | High | ownership、正しさ | snapshot finder の寿命が callable の寿命より短く、deferred relative import が実行時に失敗する |
| R3-003 | High | ownership、正しさ | recovery action が古い schema snapshot を保持し、Keep 時に新 generation の引数を削除する |
| R3-004 | Medium | 状態所有、整合性 | capture provenance の source と load provenance が異なる時点から取得され、矛盾し得る |
| R3-005 | Medium | transaction、責務 | Variation の domain commit 前に thumbnail を公開し、失敗時に orphan artifact を残す |
| R3-006 | Medium | import contract、明示性 | authoring root の `__init__.py` を fingerprint へ含めながら黙って実行しない |
| R3-007 | Medium | 依存方向、明示性 | MIDI leaf が ambient runtime config discovery を再所有する |
| R3-008 | Medium-High | aggregate、責務分離 | `ParamStore` の不変条件が private mutable reference と手動 `_touch()` に分散する |
| R3-009 | Medium | 責務分離、可読性 | `mp_draw.py` が IPC、state machine、worker、process owner を一ファイルで所有する |
| R3-010 | Medium | 公開境界、encapsulation | 公開 facade が公開型だけで閉じず、mutable `ParamStore` と内部 concrete type を契約化する |

優先度の意味:

- **High**: 公開 import 契約、実行時 failure、または parameter data loss に直接つながる。
- **Medium-High**: 現時点で単独の不具合を再現していないが、中心 aggregate の不変条件を広範囲に弱める。
- **Medium**: 通常経路は動くが、ownership、transaction、変更単位の不一致を残す。

## 5. 詳細

### R3-001 [High] lazy facade が標準の import 意味論を壊している

#### 根拠

- `src/grafix/__init__.py:73-139` は sentinel と `ModuleType` subclass を使い、`export` / `cc` への
  submodule assignment を抑止する。
- `src/grafix/api/__init__.py:84-155` は同じ仕組みを `render` / `export` に重ねる。
- `tests/api/test_lazy_facade.py:108-135` は root callable の勝利を意図的に固定するが、通常の alias import
  や nested dotted import は確認しない。
- `src/grafix/api/__init__.py:145-152` の `run` wrapper は本来の signature を持たない。

動的確認では、同じ名前が import 方法によって異なる object になった。

```text
importlib.import_module("grafix.export") -> module
import grafix.export as x              -> function
import grafix.cc as x                  -> CcView
import grafix.api.render as x          -> function
import grafix.export.variation_batch as x
                                         -> ImportError
inspect.signature(grafix.run)          -> (*args: object, **kwargs: object) -> None
```

#### 問題

lazy import のために Python の package semantics を変更している。`sys.modules` と親 package 属性が一致せず、
IDE、debugger、documentation generator、plugin discovery、runtime introspection が通常の規則を使えない。
また、ほぼ同じ facade machinery が root と `grafix.api` に重複する。

これは「遅延 import が複雑」という問題ではなく、`grafix.export` を package と callable の両方にした命名の
問題である。architecture test が現状を固定していても、契約自体が単純ではない。

#### 推奨

1. 一つの dotted name に一つの意味だけを与える。
2. `grafix.export` package を維持するなら root callable は `save` / `export_frame` などへ改名する。
3. `grafix.api.render` / `grafix.api.export` の実装 module は、必要なら private な
   `_render.py` / `_export.py` へ移し、公開 callable と実 module を衝突させない。
4. `ModuleType` 差し替えと assignment guard を削除し、通常の module semantics へ戻す。
5. `run` は正規 signature/docstring を持つ軽量関数とし、関数本体だけで runner implementation を遅延
   import する。

repository 方針どおり互換 shim は作らず、破壊的でも一度で整理する方が美しい。

---

### R3-002 [High] snapshot finder の寿命が deferred relative import より短い

#### 根拠

- `src/grafix/_snapshot_import.py:226-264` は context 終了時に finder を必ず `sys.meta_path` から外す。
- config authoring は `src/grafix/authoring_loader.py:176-220` で `retain_modules=False` を使い、candidate
  modules も除去する。
- source reload は `src/grafix/interactive/runtime/source_reload.py:209-319` の `ast.walk()` で関数内の
  relative import まで依存 source として capture する。
- 同 `:510-549` は `retain_modules=True` だが、context 中に import されなかった helper を後から解決する
  finder は残らない。

次の source は generation の load に成功するが、採用済み `draw` の初回呼び出しで失敗した。

```python
def draw(t):
    from .helper import make
    return make(t)
```

```text
loaded generation: 0
draw(0.0) -> ModuleNotFoundError: No module named '_grafix_watch_....helper'
```

config authoring の `@preset` 内に同じ import を置いた場合も、catalog 登録には成功し、preset 呼び出し時に
`ModuleNotFoundError` となった。

#### 問題

loader は source を「有効な generation」として採用した後で失敗させる。source reload では last-good の
意味が崩れ、config authoring では `authoring_loader.py:183-185` の「callable globals は module registry を
参照しない」という説明が import statement には成立しない。

#### 推奨

最も単純な契約は、**relative import は module scope だけに置く**と定め、function/class body 内の relative
import を load 時に明確な `ImportError` で拒否することである。現在すでに AST を解析しているため、汎用
import framework を増やす必要はない。

deferred import を正式に支えるなら、finderまたは全 captured module の registry を generation/snapshot の
寿命まで保持し、close/rollback と同じ owner が除去しなければならない。config snapshot には現在 close
lifetime がないため、こちらは設計コストが高い。

少なくとも config preset と source reload draw の function-local import を end-to-end test に追加する。

---

### R3-003 [High] recovery action が古い parameter schema を保持する

#### 根拠

- `src/grafix/interactive/runtime/parameter_session.py:132-222` の action installer は、構築時の
  `known_operations` を closure と `ParamStoreRecoverySession` に固定する。
- `ParameterSession.replace_known_operations()` `:260-275` は source reload 採用後に current schema を交換
  できる。
- しかし `install_diagnostic_actions()` `:303-316` で一度作った recovery object は交換されない。
- `src/grafix/interactive/runtime/parameter_recovery.py:101-110` の `keep()` は、その古い snapshot で
  `finalize_parameter_session()` を実行する。
- `src/grafix/api/runner.py:466-482` は採用済み authoring generation を session へ正しく反映しているが、
  既設 action までは更新しない。

旧 schema が `probe.old_arg`、採用後の current schema が `probe.new_arg` の最小構成で、古い recovery object
から `keep()` を実行した結果は次のとおりだった。

```text
current schema: ['new_arg']
old preserved: True
new preserved: False
```

#### 問題

recovery dialog を表示したまま authoring reload が成功し、その後 Keep を選ぶと、新 generation で正当な
parameter が「未登録引数」として primary save から削除される。これは user data loss である。

#### 推奨

- Keep/Discard action の owner を `ParameterSession` に置き、action 実行時の
  `self.known_operations` を使う。
- 代替として schema provider を渡せるが、単純な snapshot を provider 化して回るより、すでに current
  state を所有する session が command を実行する方が明快である。
- test は「recovery action install → authoring generation 採用 → Keep → 新 schema の引数が残る」という
  実際の順序で書く。

---

### R3-004 [Medium] capture provenance の二項目が異なる lifetime から取得される

#### 根拠

- `src/grafix/api/runner.py:278-282` は `parameter_load_provenance` だけを lambda で渡し、
  `parameter_source` は構築時の値を渡す。
- `src/grafix/interactive/runtime/draw_window_system.py:160-190,437-450` は static source と dynamic provider
  をそのまま次 generation に再利用する。
- `src/grafix/export/capture_provenance.py:202-250` は source を `_session` に固定する。
- 同 `:284-332` は frame ごとに load provenance だけを `replace()` する。
- `ParameterSession.source` は `parameter_session.py:293-301` で current load state から導出されるため、
  本来は Keep/Discard 後に変わる。

動的確認では、recovery から primary へ遷移した後の frame が次の組み合わせになった。

```text
parameter_source: recovery
parameter_load_provenance: primary
```

#### 問題

同じ capture manifest の二項目が別の時点を表す。再現性 metadata としては、個別の値が正しくても組が
矛盾している。

#### 推奨

`ParameterSessionCaptureState(source, load_provenance)` のような小さな frozen value を session から一度に
取得し、frame 固定時に一回だけ sample する。二つの独立 provider にするより、同時点の pair を返す方が
単純である。Keep と Discard の双方について manifest の二項目を確認する test を追加する。

---

### R3-005 [Medium] Variation の確定前に thumbnail を公開する

#### 根拠

- `src/grafix/interactive/parameter_gui/variation_controller.py:121-156` は thumbnail callback を先に実行し、
  その後で `create_variation()` を呼ぶ。
- variation 名の最大長検証は `src/grafix/core/parameters/variations.py:619-634` で初めて行われる。

81 文字の名前と、実際にファイルを作る capture callback を使った結果は次のとおりだった。

```text
save result: False
variation count: 0
thumbnail remains: True
```

#### 問題

domain commit が失敗しているのに外部 artifact だけが残る。GUI controller が「parameter snapshot の保存」と
「filesystem publish」の跨ぎ transaction を、rollback なしで所有している。

#### 推奨

1. 名前、note、seed、時刻、snapshot を含む domain 入力を capture 前にすべて検証する。
2. variation を thumbnail なしで先に確定し、capture 成功後に検証済み path を設定するか、thumbnail を
   staging して variation commit 後だけ publish する。
3. capture failure でも variation 自体を保存する現行 UX は維持する。
4. validation failure、capture failure、metadata update failure の artifact/state 組を test する。

汎用 transaction manager は不要であり、確定順を一方向にするだけでよい。

---

### R3-006 [Medium] authoring root の `__init__.py` を黙って無視する

#### 根拠

- `src/grafix/authoring_loader.py:92-103` は root 配下の全 `.py` を recipe と fingerprint に含める。
- 同 `:139-173` は root `__init__.py` を synthetic namespace package のため import plan から除外する。
- 同 `:206-219` は package source を eager import 対象から外す。

root `__init__.py` だけに `@preset` を定義した最小構成では、load は成功したが preset は登録されなかった。

```text
root init preset registered: False
```

#### 問題

directory は見た目上 Python package だが、通常の package initializer の declaration、定数、re-export を
使えない。一方で、実行しない `__init__.py` の bytes は fingerprint を変えるため、意味と identity も一致
しない。

#### 推奨

通常 package semantics を支えるか、authoring root は namespace source root と明示して root
`__init__.py` を検出時に拒否する。現在の構造では後者が単純である。黙って capture して無視する中間形を
やめ、nested package initializer の実行規則も同じ文書と test で固定する。

---

### R3-007 [Medium] MIDI leaf が ambient runtime config discovery を再所有する

#### 根拠

- `src/grafix/interactive/midi/midi_controller.py:17-20` が
  `grafix.runtime_config_loader.output_root_dir` を import する。
- `default_cc_snapshot_path()` `:171-181` は `save_dir=None` の場合に config discovery へ戻る。
- `load_frozen_cc_snapshot()` 等 `:375-407` もこの暗黙経路を公開する。
- `architecture.md:72-77` は leaf と composition root、および config resolution の方向を明示している。

#### 問題

lower-level MIDI component の挙動が CWD、HOME、YAML、process-global runtime config に依存する。複数
session の config isolation と leaf 単体 test を弱め、前回 export 側で除いた ambient discovery を別の
leaf に残している。

#### 推奨

- controller/storage helper には確定済み `save_dir` または snapshot path を必須注入する。
- default path は `api.runner` / `interactive.runtime` の composition で一度だけ解決する。
- architecture test に `interactive/{gl,midi,parameter_gui} -> runtime_config_loader` の禁止を追加する。
- MIDI device state と JSON storage の分割は、実際に独立変更が増えた場合だけ行う。

---

### R3-008 [Medium-High] `ParamStore` の不変条件が friend module に分散している

#### 根拠

- `src/grafix/core/parameters/store.py:175-221` は state、metadata、revision、history observer、rollback、
  snapshot cache を一つの aggregate として持つ。
- 同 `:845-943` は `_labels_ref()`、`_ordinals_ref()`、`_variations_ref()`、`_runtime_ref()` など mutable
  backdoor と手動 `_touch()` を sibling module へ提供する。
- `reconcile_ops.py:170-243` は private container と `ParamState` を直接変更し、最後に `_touch()` する。
- `variations.py:93-185` も dict mutation と `_touch()` を手動で対にする。
- `merge_ops.py:50-105,119-175` は private runtime、private state、module-global WeakKey cache の同期を担う。

#### 問題

`ParamStore` の docstring は mutable reference を外へ渡さず、変更は operation 経由と説明するが、package
内部の実体は private container を複数の friend module へ渡す形である。新しい mutation が revision、
history、cache invalidation のどれかを一つ忘れるだけで不整合になる。内部表現の変更範囲も package 全体へ
広がる。

巨大な `ParamStore` を避けるために処理を module 分割した結果、aggregate が anemic になり、不変条件だけが
分散している。

#### 推奨

- raw mutable reference と任意の `_touch()` を廃止し、mutation/revision/history/cache invalidation を
  `ParamStore` の狭い internal mutation port または context-managed transaction に集約する。
- `reconcile_ops` / `variations` は domain command の検証・計画を担当し、実 mutation はその port を一度だけ
  commit する。
- 代替として明示的な `ParamStoreState` と pure command に完全分離する方法もあるが、二方式を混ぜない。
- まず mutation ごとの revision/history/rollback invariant test を作り、段階的に private ref を減らす。

新しい event bus や repository pattern は不要である。

---

### R3-009 [Medium] `mp_draw.py` が四つの独立責務を抱える

#### 根拠

`src/grafix/interactive/runtime/mp_draw.py` は 1,925 行あり、次を同居させる。

1. IPC DTO、検証、error/result model (`:145-505`)
2. 親側の純粋 state machine (`:508-720`)
3. spawned worker entrypoint と worker evaluation (`:723-920`)
4. process/queue/restart/timeout/close owner (`:922-1923`)

さらに `:1655-1801` は多数の scalar telemetry property を転送する。

#### 問題

wire protocol の変更、worker evaluation の変更、親 resource lifecycle の変更が常に同じ module のレビューに
なる。単体 test の境界と navigation が弱く、owner class の主制御フローが DTO と telemetry accessor に
埋もれる。

#### 推奨

- `_mp_draw_protocol.py`: pickle される immutable DTO と wire validation
- `_mp_draw_worker.py`: spawn 可能な top-level worker entrypoint
- `mp_draw.py`: 親側 state/process/queue resource owner
- telemetry: frozen `MpDrawStats` snapshot

まず DTO と worker を機械的に移すだけでよく、abstract base class、generic process framework、event bus は
追加しない。前回計画で明示的に後続へ送った項目であり、今回の correctness 修正とは別 change set にする。

---

### R3-010 [Medium] 公開 facade が公開型だけで閉じていない

#### 根拠

- root `__all__` `src/grafix/__init__.py:43-70` は `RuntimeConfig`、`RuntimeConfigFallback`、
  `AuthoringDefinitionsSnapshot`、`ParameterLoadState`、`CaptureService`、`ParamStore` を公開しない。
- 公開 `run()` は `RuntimeConfig` / `RuntimeConfigFallback` を受ける
  (`src/grafix/api/runner.py:566-587`)。
- 公開 `RenderSession` / `render()` は `AuthoringDefinitionsSnapshot` を受ける
  (`src/grafix/api/render.py:240-252,478-490`)。
- `render_variation_batch()` は concrete `CaptureService` を受ける
  (`src/grafix/api/variation_batch.py:39-51`)。
- `RenderSession.param_store` は mutable `ParamStore` owner をそのまま返す
  (`src/grafix/api/render.py:369-375`)。

#### 問題

利用者は public API の型を表現するために deep import を必要とし、内部 module の再編が public contract に
なる。特に `capture_service` / `definitions` は composition/test seam に見える一方、正式な public
injection point として露出している。

また、session が parameter state を所有すると説明しながら mutable aggregate を返すため、ownership が
外へ漏れる。`api.variation_batch.py:92-140` 自身もこの property を mutation port として利用している。

#### 推奨

- 利用者が注入・参照する型は `grafix.api` または root から正式公開する。
- 内部配線だけの引数は public signature から外し、private factory/helper に移す。
- concrete service が不要なら、必要最小限の public Protocol/callback にする。
- `param_store` は read-only view と明示 command に分けるか、mutable な正式 public API として型・競合条件・
  lifetime を文書化する。

「非公開だが public signature に出る」「read-only のように見えるが mutable」という中間形をなくすことが
重要である。

## 6. 低優先度の可読性・単純性 follow-up

以下は correctness 修正より後で、独立した小さな計画として扱うべきである。

| 対象 | 現状 | 小さな改善 |
|---|---|---|
| `interactive/parameter_gui/table_view.py:118-165,858-1051` | 一 session 用 cache が複数 Store 用 `WeakKeyDictionary` を持ち、query 関数が 194 行 | cache を構築時の Store に束縛し、static query / dynamic query / facet mask を pure helper に分ける |
| `interactive/parameter_gui/table.py:1153-1336` | MIDI cell の scalar/vec3 経路が transition、tooltip、button、command 適用を重複 | 一成分用 `_render_midi_component` を抽出する |
| `core/effects/warp.py:498-770`、`mirror.py:61-338`、`displace.py:555-778` | 独立 mode/phase が長い一関数に残る | class 階層ではなく、既存 ndarray を渡す小さな pure/`@njit` helper に分ける |
| 複数 primitive の `radius_f = radius` 等 | 変換を伴わない型 suffix 別名が二つの語彙を作る | 実際に normalize/cast する場合だけ別名を付ける |
| `export/variation_batch.py:232-532,639-709` | model、filename policy、OS publish/rollback、SVG codec が同居し、private cleanup error を黙殺する | まず filename policy を小 module へ出し、cleanup failure は secondary diagnostic として観測可能にする |

行数を減らすこと自体を目標にせず、上位関数が `validate -> plan -> execute -> commit/pack` と読めることを
完了基準にする。

## 7. 推奨する改善順

### Phase A: data loss と採用後 failure を止める

1. R3-003 の stale recovery schema を end-to-end test で固定して修正する。
2. R3-002 の relative import contract を決め、deferred import を load 時に拒否するか lifetime owner を作る。
3. R3-004 の provenance pair を同時 sample にする。
4. R3-005 の Variation validation/commit/publish 順を直す。
5. R3-006 の `__init__.py` 規則を明示する。

### Phase B: 公開 namespace を通常の Python に戻す

1. R3-001 の衝突名を決める。
2. 通常の import matrix と `inspect.signature()` を behavior test にする。
3. `ModuleType` facade を削除する。
4. R3-010 の public type/mutable capability を同じ public API audit で決める。

### Phase C: owner 境界を閉じる

1. R3-007 の config discovery を composition root へ移す。
2. R3-008 の ParamStore mutation invariant test を追加し、private mutable ref を段階的に減らす。

### Phase D: 独立した可読性改善

1. R3-009 の protocol/worker 分離。
2. GUI table、MIDI cell、数値 effect を一対象ずつ整理する。

Phase A と B に compatibility shim や dual behavior を入れない。とくに facade と import policy は、旧規則を
残すほど理解コストが増える。

## 8. 追加すべき回帰 test

最低限、次の behavior test が必要である。

1. `import grafix.export as x`、`import grafix.export.variation_batch as x`、
   `import grafix.api.render as x` が標準 import 規則どおりになる。
2. `inspect.signature(grafix.run)` が正規 public signature と一致する。
3. config preset と source reload draw の function-local relative import が、採用前に拒否されるか採用後も
   実行できる。
4. root/nested `__init__.py` の実行または拒否、および fingerprint の意味が一致する。
5. recovery action install 後に authoring schema を交換し、Keep しても current schema の parameter が残る。
6. Keep/Discard 後の capture manifest で `parameter_source` と `parameter_load_provenance` が整合する。
7. Variation の validation/commit failure で thumbnail artifact が残らない。
8. MIDI leaf import が `runtime_config_loader` を読み込まず、snapshot path が明示注入される。
9. ParamStore の各 mutation で revision、history、rollback、derived cache が同じ transaction として更新される。

## 9. 最終評価

Grafix は、大規模な creative-coding toolkit として中心アーキテクチャが明確であり、前回改善の方向も正しい。
今回の問題は「設計が存在しない」ことではなく、設計原則を facade の名前、snapshot importer の lifetime、
recovery action の closure、GUI/file transaction の最後まで適用し切れていないことにある。

最も美しい改善は、新しい層を増やすことではない。

- package 名と callable 名を衝突させない。
- current state は current owner から実行時に取得する。
- load 時に受理した契約を callable の寿命まで守る。
- domain commit 前に外部 artifact を公開しない。
- mutable aggregate の不変条件を一箇所で確定する。

この順で直せば、既存の強い Geometry/catalog/resource 設計を維持したまま、アーキテクチャ、責務分離、
シンプルさ、可読性を同時に改善できる。
