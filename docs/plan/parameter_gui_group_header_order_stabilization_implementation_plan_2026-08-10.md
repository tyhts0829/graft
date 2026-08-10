# Parameter GUI group header 順序安定化 実装計画（2026-08-10）

- 状態: **承認待ち・未実装**
- 計画作成時 branch: `main`
- 計画作成時 HEAD: `f6b52b7`
- 対象: Parameter GUI に表示する `G` / `E` / `P` group header の順序と table model cache
- 採用方針: runtime-only の `display_order_revision` を導入し、順序変更時だけ table model を再構築する

## 0. 要約

起動時に Parameter GUI が preview より先に table model を構築すると、まだコード観測順がないため、
header は fallback 順で cache される。その直後の成功 frame でコード観測順が確定しても、現行の model
cache key は順序変更を検知しない。このため、同じ sketch でも「起動直後に cache された fallback 順」と
「cache clear 後に再構築されたコード観測順」の二つの状態を取り得る。

本計画では、並び順を永続化したり sort 規則を作り直したりせず、`display_order_by_group` の意味的変更だけを
表す runtime revision を追加する。これを table model cache key に含め、初回成功 frame の次の GUI tick で
必ずコード観測順へ収束させる。安定後は既存どおり model を再利用し、毎 frame の再構築を発生させない。

## 1. 作業ツリー境界

計画作成時点の `git status --short` は空である。承認後の実装は、本計画に列挙した production code、test、
本計画書の進捗更新に限定する。

計画作成中に、別作業による次の差分が新たに現れた。今回の対象外として内容変更・復元・削除を行わない。

- `D docs/plan/fill_angle_hatch_count_stabilization_implementation_plan_2026-08-10.md`
- `?? docs/plan/fill_size_independent_density_spacing_implementation_plan_2026-08-10.md`

次は調査 fixture またはユーザーデータであり、変更・削除・再生成しない。

- `sketch/agent_loop/runs/run_20260810_001117_n3/final/sketch.py`
- `data/output/param_store/agent_loop/runs/run_20260810_001117_n3/final/sketch.json`
- `.grafix/config.yaml`
- 依頼範囲外の sketch、出力画像、ParamStore JSON

既存の未追跡・並行差分が実装開始時に見つかった場合も、restore、移動、削除、stageを行わない。

## 2. 現象と再現監査

### 2.1 確認できたこと

- `FrameParamsBuffer` と `merge_frame_params()` の record 処理は list 順であり、現行 sketch の観測順は
  別 process / `PYTHONHASHSEED` 間でも決定的だった。
- 現行 sketch は `n_worker=0` であり、worker completion 順や multiprocessing の結果混在は主因ではない。
- Geometry cache、`for` 文、`G` / `E` の命名 key は、今回の table header 順序 cache とは別責務である。
- 成功 frame を先に評価してから table を作る headless 起動では、fresh / persisted の各試行で最終順は一致した。
- 一方、保存済み row を持つ store で **成功 frame より先に table model を構築**すると、fallback 順の model が
  cache され、その後 `display_order_by_group` が確定しても同じ model が返る状態を再現できた。
- 同じ store に対し、初回成功 frame 後に新しい cache で model を作るとコード観測順になる。したがって
  sort 自体の非決定性ではなく、cache の無効化不足で二つの順序が生じる。

### 2.2 現行データにある旧 group

調査対象の保存済み ParamStore には、過去の自動 identity に由来する未観測 group が残っている。ただし通常の
可視 view では除外され、現行 header のコード観測順を直接変える主因ではない。本修正で自動削除は行わない。

## 3. 現行実装と原因境界

1. `src/grafix/core/parameters/codec.py` は load 時に `ParamStoreRuntime()` を新規作成するため、
   `display_order_by_group` は空から始まる。これは runtime-only という現行契約どおりである。
2. `src/grafix/core/parameters/merge_ops.py::_plan_structural_record()` は、group の初回観測時に
   `display_order_by_group` と `next_display_order` を更新する。
3. `src/grafix/interactive/parameter_gui/table_view.py::_order_rows_for_display()` は、基本的に
   `(display_order, kind_rank, stable_id)` で決定的に並べる。display order が未確定なら fallback 値を使う。
4. `src/grafix/interactive/parameter_gui/table_model.py::ParameterTableModelCache` の現行 key は
   `(table_revision, catalog)` だけで、runtime の display order を含まない。
5. 初回観測による runtime-only な採番では `table_revision` が増えないことがあるため、fallback 順 model が
   cache hit し続ける。
6. `src/grafix/api/_runner_application.py` は Inspector task を preview より先に登録するため、保存済み row が
   ある起動では「GUI model 構築が初回成功 frame より先」という順序が実際に成立し得る。

したがって修正対象は sort 規則や runner の task 順ではなく、**display order の変更を table model cache key が
観測できないこと**である。

## 4. ゴール

- [ ] 保存済み store で GUI が先に model を作っても、初回成功 frame の次 tick でコード観測順へ更新される。
- [ ] fresh store と persisted store が、順序確定後に同じ group header 順を返す。
- [ ] `G` / `E` / `P` のコード観測順、effect chain の最初の step による配置、Style の固定位置を維持する。
- [ ] 同じ group の再観測、effective 値更新、UI/MIDI 値更新では model を再構築しない。
- [ ] cache key の判定は O(1) とし、毎 frame の map copy / sort / fingerprint を追加しない。
- [ ] ParamStore JSON schema、既存保存値、CODE/UI/MIDI の値選択契約を変更しない。

## 5. 非ゴール

- group header を種類別、名前順、alphabetical 順へ並べ替えること。
- ユーザーが GUI 上で group 全体を drag & drop する機能を追加すること。
- `display_order_by_group` を JSON へ永続化すること。
- Value欄に保存済みUI候補値とCODE有効値のどちらを表示するかという表示仕様を変更すること。
- 同一 session の hot reload で、既存 key のソース上の位置変更まで自動再採番すること。
- 起動直後の最初の 1 GUI frame に出得る fallback 順の flicker を、runner の初期化順変更で消すこと。
- 保存済み ParamStore の未観測 / stale group を自動削除すること。
- sketch、geometry、render cache、Layer、font、fill、line thickness を変更すること。
- 互換 wrapper、shim、旧挙動切替 flag を追加すること。

## 6. 採用設計

### 6.1 Display order の owner に revision を持たせる

`ParamStoreRuntime` に、永続化しない `display_order_revision: int = 0` を追加する。

production での採番処理は `ParamStoreRuntime.ensure_display_order(group)` のような小さな owner method に集約し、
次を一つの操作として扱う。

1. 既に group が採番済みなら既存 order を返し、何も変更しない。
2. 未採番なら `next_display_order` を割り当てる。
3. `next_display_order` と `display_order_revision` をそれぞれ 1 増やす。

revision は **order map の意味的変更時だけ**進める。同一 group の再観測、値変更、失敗 frame では進めない。

### 6.2 Cache key は O(1) の scalar だけで作る

`ParamStore` に read-only の `display_order_revision` property を追加する。
`ParameterTableCacheKey` は次の3要素にする。

```python
(store.table_revision, store.display_order_revision, catalog)
```

cache key 取得のために `runtime_view()` を呼ばない。`runtime_view()` は display order map の read-only copy を作るため、
hot path の key 判定には不要な O(n) allocation になる。map 自体の hash / sorted fingerprint も作らない。

### 6.3 Runtime lifecycle を保つ

- codec load は新しい runtime と revision `0` から開始し、JSON へ revision を保存しない。
- detached merge plan は `deepcopy` された revision を引き継ぐ。
- `_install_runtime_contents()` は map、`next_display_order`、revision を一緒に commit する。
- full replacement と transient rollback でも map / next / revision の組を欠落なく置換・復元する。
- `ParamRuntimeView` には、model cache key のためだけの field を増やさない。GUI は O(1) の store property を使う。

### 6.4 Model / view cache の伝播

`ParameterTableModel.cache_key` が変わると、既存の table view / search / visibility cache key も
`model_cache_key` 経由で無効化される。したがって `table_view.py` の sort 実装や visibility token へ
display order を重複して追加しない。

期待する起動時遷移は次のとおり。

```text
保存済みrowをload
  -> GUIがfallback順modelをbuild (display_order_revision=0)
  -> 初回成功frameがgroupを採番 (display_order_revision=N)
  -> 次GUI tickでmodelを1回rebuild
  -> 以降は同じmodelを再利用
```

### 6.5 採用しない代替案

- `visibility_revision` の流用: 最小変更だが、order と無関係な loaded/observed 変更でも model を再構築し、責務が混ざる。
- `table_revision` の更新: runtime-only の採番で永続 table revision を汚し、snapshot / style 等の既存境界を広げる。
- `next_display_order` だけを token 化: 現在の append-only 動作には効くが、map置換・復元との契約が不明瞭になる。
- map fingerprint: 正確だが毎 GUI frame O(n) 以上の処理が必要になる。
- display order の永続化: ソースのコード観測順へ起動ごとに再構築する現行契約を変え、stale order を増やす。
- GUI cache の手動 clear / runner task の並べ替え: 起動時だけに依存し、後発 conditional group や hot reload を一般に扱えない。

## 7. 変更予定ファイル

### Production

- [ ] `src/grafix/core/parameters/runtime.py`
  - `display_order_revision` と初回採番 method を追加する。
- [ ] `src/grafix/core/parameters/merge_ops.py`
  - 直接採番を runtime owner method へ置き換える。
- [ ] `src/grafix/core/parameters/store.py`
  - commit / replacement / rollback 用の runtime revision 伝播と O(1) property を追加する。
- [ ] `src/grafix/interactive/parameter_gui/table_model.py`
  - `ParameterTableCacheKey` と `get_or_build()` の key を更新する。

### Tests

- [ ] `tests/interactive/parameter_gui/test_parameter_table_model.py`
  - 今回の cache lifecycle を直接再現する RED test と安定後の再利用 test を追加する。
- [ ] `tests/core/parameters/test_revision.py`
  - display order revision の初回採番 / no-op 契約を追加する。
- [ ] `tests/core/parameters/test_transient_rollback.py`
  - map / next / revision の完全復元を既存 logical state 検証へ含める。
- [ ] 必要な場合のみ `tests/core/parameters/test_runtime.py`
  - runtime owner method の単体契約を追加する。
- [ ] 既存の `tests/interactive/parameter_gui/test_parameter_gui_display_order_code_order.py`
  - sort 規則そのものが変わらないことを回帰確認する。原則としてproduction都合の書換えはしない。

### Documentation

- [ ] 本計画書の状態と完了 checklist を、実際の実装・検証結果に合わせて更新する。
- [ ] JSON / 公開API / migration契約は変わらないため、migration document は追加しない。

## 8. 実装フェーズ

### Phase 0: 承認と baseline 固定

- [ ] ユーザーから本計画の承認を得る。
- [ ] 実装開始時の branch、HEAD、`git status --short` を本計画へ追記する。
- [ ] 依頼範囲外差分と保存済み ParamStore を変更しない境界を再確認する。
- [ ] 「観測前model build -> 初回観測 -> 同じcache」の最小再現を test 形へ固定する。

### Phase 1: RED regression test

- [ ] 既存 group を持つ store を encode / decode し、loaded・unobserved・order未確定状態を作る。
- [ ] merge 前に model を build し、fallback 順を同じ cache に保持する。
- [ ] fallback 順と異なる record 順で既存 group を merge する。
- [ ] 現行実装では `table_revision` が不変のまま stale model が再利用されることを RED test で確認する。
- [ ] 失敗 frame では revision / order / model が変わらない test を追加する。

### Phase 2: Runtime revision 実装

- [ ] `ParamStoreRuntime` へ revision と owner method を追加する。
- [ ] 新規 group 1件につき revision が1進むよう merge 経路を更新する。
- [ ] 同じ group の再観測、同一frame内の重複record、値だけの更新では revision を進めない。
- [ ] commit、deepcopy、full replacement、transient rollback の伝播を揃える。
- [ ] codec へ revision を追加していないことを確認する。

### Phase 3: Table model cache 統合

- [ ] `ParamStore.display_order_revision` を O(1) property として追加する。
- [ ] `ParameterTableCacheKey` へ revision を追加する。
- [ ] 初回 order 確定後だけ model / view が1回再構築されることを GREEN にする。
- [ ] 安定後60 frameで model build count が増えないことを確認する。
- [ ] effective / UI / MIDI の値更新だけでは既存 model が再利用されることを確認する。

### Phase 4: Focused validation

- [ ] runtime / revision / rollback の対象 test を実行する。
- [ ] table model / display order の対象 test を実行する。
- [ ] parameter GUI の labeling、effect order、group block、filter/search testを回帰確認する。
- [ ] 対象ファイルへ `ruff check` と `ruff format --check` を実行する。
- [ ] full pytest は所要時間を確認し、ユーザー許可を得た場合のみ実行する。

### Phase 5: 実GUI確認と記録

- [ ] 同じ保存済み store を使って複数回起動し、初回成功 frame 後の header 順が一致することを確認する。
- [ ] hot reload / catalog更新後も、追加された group の採番で stale model が残らないことを確認する。
- [ ] 起動直後1 frameの fallback flicker が許容不能な場合は、本修正へ混ぜず別計画として記録する。
- [ ] 実測した model build count、順序 hash、test結果を本計画へ追記する。

## 9. テスト・受入マトリクス

| ケース | 期待結果 |
|---|---|
| fresh store、成功frame後に初回model build | コード観測順で1回build |
| persisted store、model buildが成功frameより先 | 次GUI tickで1回だけrebuildしコード観測順へ収束 |
| 同じ成功frameを再観測 | revision不変、model object再利用 |
| 新規groupを1件追加 | revision +1、modelを1回rebuild |
| 同一frame内に同groupの複数arg / 重複record | group単位でrevision +1以下 |
| effective/sourceだけ変更 | display order revision不変、model構造再利用 |
| UI/MIDI値だけ変更 | display order revision不変、既存value refresh契約を維持 |
| encode/decode roundtrip | JSON差分なし、load後revision=0、初回観測で再構築 |
| transient rollback | map / next / revisionを開始時へ完全復元 |
| failure中のmerge | live storeのmap / revision不変 |
| Style + G/E/P interleave | 現行の表示規則を維持 |
| effect chain | chain内最小display orderで配置する現行規則を維持 |
| 未観測stale group | 保存データを削除せず、可視viewへ不意に混入させない |
| 1000 rows × 60 stable frames | order確定後のmodel追加build 0 |

## 10. 検証コマンド

承認後、まず対象 test だけを実行する。

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/core/parameters/test_runtime.py \
  tests/core/parameters/test_revision.py \
  tests/core/parameters/test_transient_rollback.py \
  tests/interactive/parameter_gui/test_parameter_table_model.py \
  tests/interactive/parameter_gui/test_parameter_gui_display_order_code_order.py
```

関連GUI回帰test:

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=src \
/opt/anaconda3/envs/gl5/bin/python -m pytest -q -p no:cacheprovider \
  tests/interactive/parameter_gui/test_parameter_gui_effect_order.py \
  tests/interactive/parameter_gui/test_parameter_gui_group_blocks.py \
  tests/interactive/parameter_gui/test_parameter_gui_labeling_phase1.py \
  tests/interactive/parameter_gui/test_parameter_gui_labeling_phase2.py \
  tests/interactive/parameter_gui/test_parameter_filter.py
```

静的確認:

```bash
ruff check \
  src/grafix/core/parameters/runtime.py \
  src/grafix/core/parameters/merge_ops.py \
  src/grafix/core/parameters/store.py \
  src/grafix/interactive/parameter_gui/table_model.py \
  tests/core/parameters/test_runtime.py \
  tests/core/parameters/test_revision.py \
  tests/core/parameters/test_transient_rollback.py \
  tests/interactive/parameter_gui/test_parameter_table_model.py

ruff format --check \
  src/grafix/core/parameters/runtime.py \
  src/grafix/core/parameters/merge_ops.py \
  src/grafix/core/parameters/store.py \
  src/grafix/interactive/parameter_gui/table_model.py \
  tests/core/parameters/test_runtime.py \
  tests/core/parameters/test_revision.py \
  tests/core/parameters/test_transient_rollback.py \
  tests/interactive/parameter_gui/test_parameter_table_model.py
```

## 11. リスクと停止条件

### リスク

- revision 更新漏れがあると、別経路で同じ stale cache 問題が残る。
- revision を広すぎる条件で進めると、大規模 table model が毎 frame 再構築されてCPU負荷が悪化する。
- runtime commit / rollback のコピー漏れがあると、map と revision が不整合になる。
- `ParameterTableCacheKey` の型変更を受ける view/search cache の回帰を見落とす可能性がある。
- 本案は起動直後の最大1 GUI frameのfallback表示を許容する。最終順は次tickで安定するが、flicker自体は別課題である。

### 停止条件

- 最小再現 test が現行実装で失敗せず、cache無効化不足を再現できない場合はproduction変更へ進まない。
- revision が通常の安定frameで増える場合は、model cacheへ統合する前に更新境界を修正する。
- JSON出力、CODE/UI/MIDI値、effect order、Style配置に意図しない差が出た場合は範囲を広げず原因を再調査する。
- full pytest、保存済みParamStoreの破壊的cleanup、runner初期化順変更は追加承認なしに実行しない。

## 12. 完了条件

- [ ] 最小再現 test が修正前RED / 修正後GREENになる。
- [ ] 初回観測でだけ `display_order_revision` が進み、安定frameでは不変になる。
- [ ] persisted store のpre-frame cached modelが、初回成功frame後に同じcacheで更新される。
- [ ] fresh / persisted / 複数起動シミュレーションの最終header順が一致する。
- [ ] 安定後の1000-row model再利用性能を維持する。
- [ ] rollback、failure、decodeのruntime lifecycle testが通る。
- [ ] 対象testとlint/format checkが通る。
- [ ] 作品、保存済みParamStore、JSON schema、公開APIを変更していない。
- [ ] 本計画の各項目を実結果に合わせて更新し、未完了項目を明記する。

## 13. 承認・実装状態

現時点は **未承認・未実装**。ユーザー承認後に Phase 0 から開始し、完了した項目だけを `[x]` へ更新する。
