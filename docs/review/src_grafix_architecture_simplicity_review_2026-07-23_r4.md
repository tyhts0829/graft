# `src/grafix` アーキテクチャ・単純性コードレビュー R4（2026-07-23）

## 1. 結論

### 1.1 率直な回答

**大きなアーキテクチャ上の問題は、ほぼ出尽くしている。**

前回レビューの R3-001〜R3-010 は現行の作業ツリーで解消されており、依存方向、公開 API、
resource ownership、`ParamStore` の更新境界、`mp_draw` の責務分割をもう一度広く作り直す必要はない。
今後も「何かないか」と全体を分割し続ける方が、見つかる問題より抽象化と移動のコストを増やしやすい。

ただし、今回の確認では次の **修正する価値がある Medium 2 件**を新たに確認した。

1. `MpDraw` worker が stale と判定した snapshot payload を実際には評価してしまう。
2. variation thumbnail の rollback ownership を publish 後に取り直しており、TOCTOU を残したまま
   runtime adapter と fault-injection test が過剰に複雑化している。

したがって、「もう直すところは一つもない」ではない。一方で、この 2 件を修正して文書を同期した後は、
Low 項目を watchlist に留め、**全面的なレビュー駆動リファクタリングを止める段階**に来ている。

### 1.2 今回の判定

- High: **0 件**
- Medium: **2 件**
- Low: **4 件**
- 全体再設計: **不要**
- 汎用 framework、DI container、repository、event bus、transaction manager の追加: **不要**

## 2. 対象と前提

- 対象: 現在の作業ツリーの `src/grafix/`、関連 test、architecture 文書
- 観点:
  - アーキテクチャ
  - 責務の分離
  - 美しさ
  - シンプルさ
  - 可読性
  - 過剰防御・オーバーエンジニアリング
- 規模:
  - production Python: 290 files / 98,874 lines
  - test Python: 323 files / 83,836 lines
  - architecture tests: 1,633 lines

レビュー開始時点で作業ツリーには 136 件の既存変更・未追跡項目があった。本レビューはその現在形を
対象とし、既存差分には触れていない。この文書だけを新規作成した。

前回のレビューと実装記録:

- `docs/review/src_grafix_post_implementation_code_review_2026-07-23.md`
- `docs/plan/src_grafix_post_review_implementation_plan_2026-07-23.md`

後者では R3-001〜R3-010 が完了済みであり、full pytest 4047 passed が記録されている。この R4 では
full suite を再実行せず、構造確認、静的検証、focused test、個別再現を行った。

## 3. 確認方法と結果

### 3.1 独立検証

| 確認 | 結果 |
|---|---|
| `tests/architecture` | 39 passed |
| lazy facade / public type graph / ParamStore mutation / `MpDraw` focused tests | 103 passed |
| `ruff check src/grafix tests/architecture` | All checks passed |
| `mypy src/grafix` | Success: no issues found in 290 source files |
| runtime top-level import graph | cycle なし |
| layer 逆依存 | `core -> api/export/interactive`、`export -> api/interactive`、`interactive -> api` なし |
| stale snapshot payload の process 再現 | 再現した |

静的検証と既存 test が成功していても、R4-001 は未テストの payload 付き stale task 分岐で再現した。

### 3.2 stale snapshot の再現結果

1 worker を revision 5 まで進めた後、revision 4 の snapshot payload を持つ task を投入した。

```text
first 1 5 ((79033, 5),)
second 10000 4 rejections 0 ack (4, 5, 'stale')
```

worker は revision 4 を `stale` と ACK し、current revision 5 を保持した。それにもかかわらず
revision 4 の `DrawResult` を返し、rejection count は 0 のままだった。

これは private task queue へ payload 付き stale task を注入し、問題の worker 分岐を決定的に
検証した protocol-level reproduction である。通常の `submit()` だけで競合頻度を測ったものではない。
独立した control queue と task queue の順序により production でも到達可能という点は、コードからの
推論として区別する。

## 4. 良くなっている点

### 4.1 layer と公開境界は十分に明確

- root と `grafix.api` は通常の module semantics を保っている。
- public annotation graph は正式な public path だけで閉じている。
- mutable `ParamStore` や concrete capture seam は公開 API から除かれている。
- `core`、`export`、`interactive`、`api` の逆依存は検出されなかった。
- composition root と domain owner の区別は文書と実装の両方で概ね一致している。

この部分は再設計対象ではない。

### 4.2 `mp_draw` の分割は過剰ではない

現在の protocol / state / worker / parent process owner の分割は、spawn/pickle 境界、pure state
transition、worker 実行、Queue/process lifecycle という実際の変更理由に一致している。

R4-001 はこの分割を戻す理由ではなく、worker 内の一つの条件分岐を修正する理由である。
`MpDraw` を scheduler、process manager、stats service などへさらに分けると、時間的状態の追跡が
複数 object に散り、かえって読みにくくなる。

### 4.3 `ParamStore` の中心集約は必要な複雑性

`ParamStore`、`_ParamStoreRead`、`_ParamStoreMutation` は大きいが、論理 state、revision、
history integration、snapshot/favorite cache invalidation、transient rollback の確定 owner を
一箇所に置く実益がある。以前の raw mutable reference と手動 `_touch()` へ戻すべきではない。

ただし mutation port は 23 個の `commit_*` を持ち、そのうち 17 個は production で 1 callsite
だけである。多くは `_replace()` への forwarding であり、複雑性の上限に近い。今後は新しい
一回限りの commit method を機械的に追加しない。次にこの領域を変更する時だけ、
`commit_full_replacement`、runtime identity を保つ install、sparse runtime patch の固有処理は
残し、純 forwarding method を少数の明示的 replacement primitive へ統合するのがよい。
行数削減だけを目的とする独立再編は不要である。

また `_replace()` は入力 container を copy せず store へ移す。これは性能上妥当だが、入力は
**terminal ownership transfer** であり、呼び出し側は commit 後に参照を再利用・変更しないという
規約を局所 architecture 文書へ明記すべきである。commit 時の defensive deep-copy は不要である。

### 4.4 大きい orchestration code は直ちに分割対象ではない

`_InteractiveApplication`、`DrawWindowSystem`、`SceneRunner` は長いが、一回の run/window/frame の
acquisition、時間順序、cleanup を所有する composition/resource owner として凝集している。
phase ごとの class 化や DI container 化は、美しさより間接参照を増やす。

Parameter GUI の長い逐次描画関数や effect の数値処理も、行数だけで小関数・class へ分割すると
データフローと性能上の局所性を失いやすい。

## 5. Findings summary

| ID | 優先度 | 観点 | 要約 |
|---|---|---|---|
| R4-001 | Medium | 正しさ、state ownership | stale と ACK した task 同梱 snapshot を worker が評価する |
| R4-002 | Medium | ownership、単純性、美しさ | thumbnail ownership の再取得に TOCTOU があり、誤った層で過剰防御している |
| R4-003 | Low | 単純性、可読性 | 2 個の set の visibility revision に hidden mutation hook 一式を持つ |
| R4-004 | Low | composition、明示性 | source reload だけが長寿命の ambient `ContextVar` 依存を残す |
| R4-005 | Low | 単純性、testability | variation batch の private test seam が公開 signature を複製する |
| R4-006 | Low | 保守性、文書 | parameter 局所文書が旧責務を説明し、一部 guard は source shape に強く結合する |

## 6. 詳細

### R4-001 [Medium] stale と ACK した task 同梱 snapshot を worker が評価する

#### 根拠

- `src/grafix/interactive/runtime/_mp_draw_worker.py:82-107`
  - `apply_snapshot()` は current より古い update を `stale` と ACK し、worker state には適用しない。
- `src/grafix/interactive/runtime/_mp_draw_worker.py:130-165`
  - `evaluation_snapshot = task.snapshot` を先に設定する。
  - payload があれば `apply_snapshot()` を呼ぶが、`stale` の場合も
    `evaluation_snapshot` は task の古い値のままである。
  - rejection は `evaluation_snapshot is None` の場合にしか行われない。
- `src/grafix/interactive/runtime/_mp_draw_worker.py:174-229`
  - 古い payload を評価し、`snapshot_revision=requested_revision` の結果を返す。
- `src/grafix/interactive/runtime/_mp_draw_state.py:198-217`
  - parent は epoch と frame id で結果を採用し、snapshot revision の一致は見ない。
- `tests/interactive/runtime/test_mp_draw_stress.py:198-271`
  - stale task の `snapshot=None` 経路は検証するが、payload 付き stale task は検証しない。

#### 問題

revision 5 を既に保持する worker が、Queue ordering により後から届いた revision 4 payload を
評価できる。結果として次が起こり得る。

- より大きい frame id の結果がまだ到着していなければ、古い revision の draft frame を一時表示する。
- current revision の draft preview への収束を遅らせる。
- stale task の計算時間と worker capacity を浪費する。

final capture、recording、variation thumbnail は同期 final 評価を使うため、この finding を
capture/provenance の失敗へは拡大しない。

#### 最小修正

task payload をそのまま評価用変数として保持しない。

1. payload があれば `apply_snapshot()` する。
2. その後に `snapshot_revision == requested_revision` を確認する。
3. 一致する場合だけ worker が所有する current `snapshot` と `effect_order_snapshot` を評価する。
4. current より古ければ `_TaskRejected(reason="stale")`、未到達なら `"unknown"` とする。
5. payload 付き stale task の process test を 1 件追加する。

parent 側へ別の validator class を追加したり、汎用 scheduler を作ったりする必要はない。

### R4-002 [Medium] thumbnail ownership の再取得に TOCTOU があり、誤った層で過剰防御している

#### 根拠

- `src/grafix/export/capture_publish.py:218-246`
  - publish 境界は staged artifact の device/inode identity を既に取得し、rollback に使う。
- `src/grafix/export/capture_publish.py:253-256`
  - 正常 return の `PublishedCaptureGeneration` は path だけを返し、確定済み identity を捨てる。
- `src/grafix/export/capture.py:493-505`
  - `CaptureService.export()` も path と manifest path だけの `ExportResult` に変換する。
- `src/grafix/interactive/runtime/variation_thumbnail_capture.py:24-170`
  - runtime adapter が公開済み path を改めて `stat()` し、rollback ownership を再構成する。
- `src/grafix/interactive/runtime/variation_thumbnail_capture.py:152-170`
  - 単純な frozen dataclass owner の生成まで `BaseException` で囲み、二ファイル rollback を行う。
- `src/grafix/interactive/parameter_gui/variation_controller.py:418-462`
  - typed な内部 callback の返り値を hostile input のように実行時検証し、不正値からも cleanup を
    試みる。

#### 問題

publish 完了から runtime 側の再 `stat()` までに外部 process が同名 path を置換すると、runtime は
外部 file の inode を「今回 publish した file」として所有できる。その後 variation commit が失敗し
`discard()` すると、外部 file を削除し得る。

既存 test は ownership 取得後の置換を検証するが、この ownership 取得前の窓は閉じない。

また、publish 層と runtime 層に identity/unlink helper が重複し、runtime adapter は 227 行、
専用 test は cleanup failure の細かな組合せまで持つ。これは「防御を増やせば安全になる」のではなく、
**ownership を確定できる層から情報を捨てたため、下流で防御コードを再構築している状態**である。
過剰設計でありながら、本来守るべき競合窓は残っている。

ただし、publish 失敗時の unlink は元の publish error を優先する best-effort rollback、明示的な
thumbnail `discard()` は二 member を両方試して cleanup error を診断する strict rollback であり、
エラー方針まで一つの helper へ統合する必要はない。

#### 最小修正

- publish 境界で確定した path と identity を持つ package-private な owned generation/discard token を
  返し、必要な private capture 経路だけへ伝播する。
- thumbnail adapter はその token を `VariationThumbnailArtifact` の `path` / `discard()` へ写すだけに
  する。
- `discard()` は identity が一致する member だけを削除し、一方が失敗しても他方を試す現在の
  strict/diagnostic cleanup contract を維持する。
- `prepare -> capture -> commit -> commit failure 時 discard` の transaction 順序は維持する。
- target path の一意性は publish 層の test として残す。
- 両 member の削除、一方が外部置換済みの場合の保持、一方の cleanup failure 後も他方を試す test は
  残す。
- pure owner constructor failure、typed private callback の不正な形など、production では発生経路の
  ない fault matrix と、runtime が人工的な same-path `ExportResult` を受ける test は削る。

汎用 artifact transaction framework や filesystem repository は作らない。

### R4-003 [Low] visibility revision のための tracked set が技巧的すぎる

#### 根拠

- `src/grafix/core/parameters/runtime.py:20-110`
  - `_GroupVisibilityTracker` と `_TrackedGroupSet` を定義し、set の mutation API をほぼ全て上書きする。
- `src/grafix/core/parameters/runtime.py:194-265`
  - `ParamStoreRuntime.__setattr__()` まで上書きし、tracker を暗黙に bind する。
- `src/grafix/core/parameters/store.py:746-781`
  - detached runtime の tracked set を既存 tracker へ rebind してから内容を移す。
- production の明示的な `loaded_groups` / `observed_groups` mutation は、現状 5 flow 内の
  6 mutation 文に限られる。

#### 問題

二つの集合が変わったかを知るために、hidden hook、`deepcopy` 後の tracker identity、rebind 順序を
理解する必要がある。通常の `set` に見える object が revision を変更するため、読み手が副作用を
局所的に判断しにくい。

#### 最小修正

- `loaded_groups` と `observed_groups` を plain `set` に戻す。
- `__post_init__()` で constructor 入力 set を copy する alias 防止は維持し、custom
  `__setattr__()` だけを外す。
- 通常の runtime install は swap 前に old/new 集合を比較し、差分がある場合だけ target の現在
  revision を基準に `+1` する。
- `commit_full_replacement()` は opaque cache token の強制無効化として、現在どおり無条件 `+1`
  を維持する。
- decoded runtime の初回 install も明示的に扱い、effective-only sparse patch には集合比較を
  追加しない。

これは「集合 mutation ごとの counter」から「commit generation」への意図的な semantic
simplification になる。最終集合が元に戻った場合に revision を進めないことを cache token の契約として
固定し、exact increment test を更新する。structural merge へ O(group 数) の比較が加わるため、
parameter benchmark の 10% 未満基準も再確認する。

新しい observable collection abstraction は作らない。現在の実装に correctness defect はないため、
独立作業として急がず次回この領域を変更する時に行う。

### R4-004 [Low] source reload だけが長寿命の ambient composition dependency を残す

#### 根拠

- `src/grafix/interactive/runtime/source_reload.py:1044-1074`
  - `_CURRENT_SOURCE_RELOAD`、`source_reload_context()`、`current_source_reload()` を定義する。
- `src/grafix/devtools/run_sketch.py:82-100`
  - interactive run 全体を context で囲む。
- `src/grafix/api/_runner_application.py:163-166,257-270`
  - composition root が引数ではなく `current_source_reload()` を探索し、
    `DrawWindowSystem` へ渡す。

#### 問題

正式 callsite では正しく対になっているため通常の不具合ではない。ただし draw と reload controller の
関係が signature に現れず、他の短命な evaluation context と違って window loop の全寿命に及ぶ。
composition dependency としては明示引数の方が単純である。

#### 最小修正

private `_run_interactive_application(..., source_reload=None)` へ直接渡す。
公開 `grafix.run()` の signature は変えず、新しい abstraction も作らない。

現行 `ContextVar` は CLI の watch 実行だけに限定され、正しく reset されている。これは不具合でも
明確な過剰設計でもないため、独立作業にせず次回この経路を変更する時の候補に留める。

### R4-005 [Low] variation batch の private test seam が公開 signature を複製する

#### 根拠

- `src/grafix/api/variation_batch.py:55-114`
  - public `render_variation_batch()` が全 option を private helper へ転送する。
- `src/grafix/api/variation_batch.py:117-129`
  - `_render_variation_batch()` が同じ default と signature を再定義し、差は
    `capture_frame` seam だけである。
- `src/grafix/api/variation_batch.py:226-232`
  - `_batch_name()` の path-component policy は
    `src/grafix/export/variation_batch.py:251-256` にも存在する。

#### 問題

public option の追加・改名時に二つの signature、default、forwarding を同期する必要がある。
test injection のために composition 全体を複製している。

#### 最小修正

公開関数で一度だけ validation/composition を行い、injectable seam は item render loop の狭い helper
へ移す。concrete `CaptureService` を再び public 引数へ出さない。

### R4-006 [Low] 局所文書が旧責務を説明し、一部 guard は source shape に強く結合する

#### 根拠

- `tests/architecture/test_parameter_store_mutation_boundary.py:18-60,89-101`
  - 削除済み private attribute/class/method の名前集合を固定する。
- `tests/architecture/test_dependency_boundaries.py:300-320,388-392,504-540`
  - 過去の module/path が存在しないことを個別に固定する。
- `tests/architecture/test_implementation_quality.py:154-166`
  - 削除済み `store_bridge.py` の不在を固定する。
- `src/grafix/core/parameters/architecture.md:11,37-63,75-77,153-156`
  - `ParamStore` を薄い「データの入れ物」、ops を唯一の write owner と説明する。
- `src/grafix/core/parameters/store.py:924-933`
  - class docstring 自体も「永続データの入れ物」に寄せるという旧説明を残す。
- 現在の `src/grafix/core/parameters/store.py:62-851,878-922,924-1180,1614-1860`
  - read/mutation port、history、rollback、revision/cache の commit ownership を持つ aggregate である。

#### 問題

局所文書は移行後の aggregate ownership に追従していない。

architecture test の列挙には、単なる移行履歴だけでなく、raw mutation surface、`core` への
filesystem/process owner の逆流、process-global registry の復活を防ぐ意味がある。そのため一括削除は
誤りである。一方、同等の semantic boundary test が既にあるのに exact file/class 名の不在だけを
重ねると、test が source shape と過去の名前に不要に結合する。

#### 最小修正

- layer 逆依存、filesystem capability、global mutable state 禁止などの semantic test は維持する。
- exact-path/name guard は、それが守る意味的制約と同等の test が別に存在するか個別に確認する。
- 同等の semantic test がある場合だけ、重複する tombstone assertion を削除候補にする。
- 意味的制約を持つ guard は、過去の名前を消すだけでなく可能な範囲で capability/import rule として
  表現する。
- parameter 局所文書を
  「ops は validate/plan、`ParamStore` は論理 state、commit/revision、history integration、
  snapshot/favorite cache invalidation、transient rollback の owner」
  へ更新する。
- mutation port の入力は terminal ownership transfer であり、commit 後に caller が再利用・変更
  しないことも明記する。

## 7. オーバーエンジニアリング判定

### 7.1 実際に過剰になっている箇所

1. **thumbnail rollback**
   - ownership の確定位置が下流すぎるため、runtime に inode tracking、複合 cleanup、
     constructor failure handling、private Protocol validation が流入している。
2. **visibility tracked set**
   - 2 集合の差分通知に collection subclass と hidden `__setattr__` hook を使っている。
3. **重複する source-shape guard**
   - 同等の semantic test がある場合まで、削除済み名前の不在を追加で固定する。
4. **private test seam の広さ**
   - 一つの callback injection のために public composition signature を複製している。

### 7.2 複雑だが妥当な箇所

1. `ParamStore` の revision/history/cache/rollback commit ownership
2. `mp_draw` の protocol/state/worker/parent owner 分割
3. `_InteractiveApplication` の acquisition と逆順 cleanup
4. `DrawWindowSystem` と `SceneRunner` の時間的 state ownership
5. Parameter GUI の controller/model/view/session 分離
6. Variation の `prepare -> capture -> commit -> discard`
7. capture generation の staging/publish/rollback

これらを単純化する目的で generic framework に置き換えると、Grafix の規模では間接性の方が大きくなる。

### 7.3 今は分割しない方がよい箇所

- `MpDraw` を scheduler/process manager/stats class へ追加分割しない。
- `_InteractiveApplication` を subsystem ごとの service graph にしない。
- `DrawWindowSystem.draw_frame()` を phase class 群にしない。
- `SceneRunner` の generation/last-good/restyle state を別 owner へ散らさない。
- Parameter GUI widget を class hierarchy 化しない。
- effect の数値処理を行数だけで細切れにしない。
- `ParamStore` を repository/event sourcing/transaction framework へ置換しない。

`export_job_system.py` は protocol、worker、parent owner の変更理由が混在しているが、現在の行数だけで
今すぐ分割する必要はない。次に export job を大きく変更する時だけ、`mp_draw` と同様の
**機械的な module 分割**を検討し、generic process executor は作らない。

## 8. 推奨順序

### 今直す

1. R4-001: payload 付き stale snapshot task を拒否し、process regression test を追加する。
2. R4-002: publish 境界から owned generation を伝播し、thumbnail の TOCTOU と過剰防御を同時に除く。

### 低リスクの文書同期

3. R4-006 の parameter 局所文書と `ParamStore` docstring を現行 ownership に合わせる。

### 次回接触時だけ直す

4. R4-003: tracked set を plain set + commit-time comparison にする。
5. R4-004: source reload を private explicit argument にする。
6. R4-005: variation batch の test seam を item loop へ狭める。
7. R4-006: 同等の semantic test がある source-shape guard だけを個別に整理する。

### その後

新たな全面リファクタリング計画は作らない。次のいずれかが起きた時だけ対象箇所を見直す。

- 同じ機能変更で同じ境界を 3 回以上横断する。
- owner 境界をまたぐ実不具合が再現する。
- profile/benchmark で具体的な性能問題が出る。
- public API で実際の利用例を表現できない。
- 一つの変更のため常に複数の重複 signature/validation を更新する必要がある。

「ファイルが長い」「さらに綺麗にできそう」「レビューすれば何か出そう」だけでは、次の
リファクタリング理由にしない。

## 9. 最終評価

- **アーキテクチャ**: 良好。layer、公開境界、resource owner は十分に明示されている。
- **責務の分離**: 良好。残る実害は stale worker state と thumbnail ownership の局所的な二件。
- **美しさ**: 概ね良好。局所的に defensive ceremony が本来の一方向フローを隠している。
- **シンプルさ**: 成熟段階。新しい抽象化を足すより、既存の誤った防御位置と重複を減らすべきである。
- **可読性**: 良好。大きい owner はあるが、追加 class 化より現在の時間順序を保つ方が読みやすい。

最終結論は、**「主要な構造改善は終盤。実害のある 2 件を直して文書を同期し、Low 項目は
watchlist に置いたまま全面レビューを止める」**である。
