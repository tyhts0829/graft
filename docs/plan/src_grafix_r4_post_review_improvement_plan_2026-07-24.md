# `src/grafix` R4 実装後レビュー改善計画（2026-07-24）

- 根拠レビュー:
  `docs/review/src_grafix_r4_post_implementation_code_review_2026-07-24.md`
- 計画作成時 HEAD: `1be99e3`
- 計画作成時 branch: `main`
- 計画作成時 working tree: clean
- 対象: R4P-001〜R4P-005
- 状態: **完了**

本計画は、R4 の ownership 集約と worker revision barrier を維持したまま、実装後レビューの
Medium 2 件、Low 3 件を局所的に解消する。

production code、test、既存文書は、本計画への明示承認後に変更する。

## 1. 結論と実装方針

次の方針を採用する。

1. **R4P-001 は atomic filesystem transaction を追加せず、保証範囲を正確化する。**
   - portable な `stat -> unlink` は atomic compare-and-delete ではない。
   - cleanup は stable namespace を前提にした best-effort identity cleanup と定義する。
   - identity 検査時点で観測した missing、非通常 file、identity mismatch は保持する。
   - identity 一致確認後から `unlink()` までの同名 entry の並行交換は保証しない。
2. **R4P-002 は未使用の `write_capture_manifest()` を削除する。**
   - repository 内 production callsite は 0 件。
   - root/API facade、stub、`grafix.export` package から公開されていない。
   - cleanup policy を修正して unused primitive を残すより、削除する方が単純である。
3. **R4P-003 は owned token を public API に戻さず、private 境界を一貫させる。**
   - `publish_capture_generation()` を `_publish_capture_generation()` に改名し、`__all__` から除く。
   - `_OwnedCaptureGeneration` と `CaptureService._export_owned()` は private のまま維持する。
   - thumbnail adapter は concrete `CaptureService` ではなく、bound owned-export callable の
     private Protocol に依存する。
   - composition root だけが `CaptureService._export_owned` を配線する。
4. **R4P-004 は test の配置を production owner と一致させる。**
   - filesystem publish test を `tests/export/test_capture_publish.py` へ移す。
   - manifest value fixture は `tests/capture_manifest_test_support.py` に一度だけ置く。
   - `tests/core/test_capture_manifest.py` は value、schema、validation、serialization に限定する。
5. **R4P-005 は R3 migration を R3 時点の履歴へ戻す。**
   - R4 で追記した publish-time identity/token と worker revision barrier の詳細だけを戻す。
   - 現行内部仕様は `architecture.md`、developer guide、architecture visualization を正本とする。
   - 新しい migration 文書は作らない。

## 2. なぜ R4P-001 をコードで atomic 化しないか

標準 Python/POSIX API には、path の identity 比較と unlink を一操作で行う portable primitive がない。

- file descriptor で inode を固定しても、最終的な path-based unlink との間の交換を防げない。
- cooperative lock は、その lock に参加しない外部 process を保護できない。
- quarantine rename は、外部 replacement を移動した場合の no-clobber restore まで必要になる。
- portable でない `renameat2` / `renameatx_np`、writer lease、journal を今回へ追加すると、
  narrow cleanup helper の修正を超える。

したがって、今回の正確な contract は次とする。

> discard / rollback は、member の identity 検査中に同名 path が安定していることを前提とする。
> 検査時点ですでに観測できる missing、非通常 file、identity mismatch は削除しない。
> identity 一致確認後から unlink までの並行 path 交換は保証対象外である。

並行する未知の external writer に対する厳密な無損失保証が必要な場合、本計画を止め、
platform-specific no-clobber restore または writer lease を別設計する。

## 3. 現状 inventory

### 3.1 `write_capture_manifest()`

- production callsite: 0
- test callsite: `tests/core/test_capture_manifest.py` の 2 箇所
- root/API facade export: なし
- stub: なし
- `grafix.export.__all__`: 空

削除時に compatibility alias、wrapper、deprecation shim は残さない。

### 3.2 owned export

- `_OwnedCaptureGeneration`
  - artifact paths、manifest path、publish 前 identity、`discard()` を所有する。
- `CaptureService._export_owned()`
  - public `CaptureService.export()` と variation thumbnail adapter が利用する。
- variation thumbnail
  - runtime adapter が concrete service の private method を直接呼んでいる。
  - test fake は concrete service を偽装するため `Any` cast を使っている。

改善後は adapter が private callable Protocol だけを知り、concrete private method の取得は
`api._runner_application` の composition root に限定する。

### 3.3 capture publish tests

現在の `tests/core/test_capture_manifest.py` は次の二責務を持つ。

- core:
  - manifest value、schema、validation、serialization
- export:
  - path policy、hard-link publish、rollback、overwrite、staging cleanup、owned discard

後者を新規 export test file へ移す。

## 4. 承認境界と停止条件

- 本計画への明示承認前に production code、test、既存文書を変更しない。
- 承認後、Phase 0 から順に進め、各 Phase の focused test 成功後に次へ進む。
- dependency 追加、network access、snapshot 更新、commit、push、release は行わない。
- full pytest、GUI smoke、process soak、長時間 benchmark は本計画に含めない。
- compatibility alias、wrapper、dual return、第二の rollback path は追加しない。
- generic repository、transaction manager、filesystem abstraction は追加しない。
- historical review/plan 文書内の旧 symbol 名や当時の判断は書き換えない。

次の場合は実装を停止して確認する。

- external writer に対する atomic compare-and-delete が必須で、best-effort contract への限定が不可。
- `write_capture_manifest()` の repository 外正式利用が判明し、削除ではなく public contract 維持が必要。
- private callable Protocol だけでは型が閉じず、owned token の root/API 公開が必要になる。
- test 移動により repository 内 CI selector の更新が必要になる。
- R3 migration の R4 追記以外まで変更する必要が生じる。
- 対象 file に本計画と意味が衝突する並行差分が生じる。

## 5. Phase 0 — baseline と対象確定

- [x] 実装開始時の HEAD、branch、`git status --porcelain` を本書へ記録する。
- [x] 対象 file の並行差分を読み直す。
- [x] HEAD が `1be99e3` から変わっていなければ、根拠レビューの baseline を採用する。
- [x] HEAD と対象 file は変わっていなかったため、focused baseline の再実行は不要と判断する。
- [x] `rg` で `write_capture_manifest()` の production callsite が 0 件であることを再確認する。
- [x] `rg` で `_export_owned()` と `publish_capture_generation()` の全 callsite を確定する。

実装開始時:

```text
HEAD: 1be99e3
branch: main
git status --porcelain:
?? docs/plan/src_grafix_r4_post_review_improvement_plan_2026-07-24.md
```

HEAD と対象 production/test file は根拠レビュー時から不変だったため、記録済み baseline を採用した。
`write_capture_manifest()` の production callsite は定義と `__all__` 以外 0 件だった。

根拠レビューの baseline:

```text
R4 focused tests: 362 passed
tests/architecture: 39 passed
total: 401 passed
ruff: All checks passed
mypy src/grafix: Success: no issues found in 290 source files
git diff --check: success
```

## 6. Phase 1 — dead primitive と publish 境界の整理

対象:

- `src/grafix/export/capture_publish.py`
- `src/grafix/export/capture.py`
- `src/grafix/export/output_paths.py`
- `tests/export/test_capture_service.py`
- `tests/interactive/runtime/test_draw_window_system.py`
- `tests/architecture/test_dependency_boundaries.py`
- `tests/architecture/test_output_composition_boundaries.py`

### 6.1 `write_capture_manifest()` の削除

- [x] `write_capture_manifest()` を削除する。
- [x] `capture_publish.__all__` から削除する。
- [x] 専用 no-clobber write test を削除する。
- [x] multiple-artifact test は filesystem I/O を使わない pure serialization test にする。
- [x] alias、wrapper、deprecated name を残さない。

### 6.2 publish primitive の private 化

- [x] `publish_capture_generation()` を `_publish_capture_generation()` に改名する。
- [x] `capture_publish.__all__` から publish primitive を除く。
- [x] `CaptureService` の二つの publish callsite を private name へ更新する。
- [x] capture service / draw window の monkeypatch test を private nameへ更新する。
- [x] architecture guard の forbidden name を private nameへ同期し、layer rule を弱めない。
- [x] `output_paths.py` の current docstring から旧 public 風 symbol 名を除く。
- [x] `_OwnedCaptureGeneration` は private のまま維持する。

Phase 1 完了条件:

- [x] `write_capture_manifest` の production/test参照が 0 件。
- [x] bare `publish_capture_generation` の production/callsite 参照が 0 件で、
  architecture tombstone guard だけに残る。
- [x] public root/API/stub surface に owned rollback capability を追加していない。
- [x] capture service の no-clobber、late collision、overwrite の意味が不変。

## 7. Phase 2 — thumbnail adapter の narrow callable boundary

対象:

- `src/grafix/interactive/runtime/variation_thumbnail_capture.py`
- `src/grafix/api/_runner_application.py`
- `tests/interactive/runtime/test_variation_thumbnail_capture.py`
- `tests/api/test_runner_authoring_composition.py`

### 7.1 private callable Protocol

- [x] owned export の bound callable だけを表す private Protocol を runtime adapter module に定義する。
- [x] Protocol は既存 `_export_owned()` の一つの call signature と
  `VariationThumbnailArtifact` return capability だけを記述する。
- [x] `make_variation_thumbnail_capture()` は concrete `CaptureService` ではなく、この callable を受ける。
- [x] adapter は live frame、portable path、`overwrite=False`、split 無効、thumbnail size、
  `gcode_params=None` の policy を引き続き所有する。
- [x] composition root は `draw_window.capture_service._export_owned` の bound method を渡す。
- [x] concrete token 型を runtime/GUI へ importしない。

### 7.2 test fake

- [x] fake service を owned-export callable spy へ置き換える。
- [x] concrete `CaptureService` を偽装する `cast(Any, ...)` を削除する。
- [x] exact token object を再構築せず返す assertion を維持する。
- [x] live frame と no-frame contract を維持する。
- [x] composition test を再実行し、新しい wiring を確認する。

Phase 2 完了条件:

- [x] runtime adapter の production import に concrete `CaptureService` がない。
- [x] concrete private method への参照は `CaptureService` 自身と composition root に限定される。
- [x] GUI leaf は export service/type を importしない。
- [x] new generic DI abstraction、service locator、repository を追加していない。

## 8. Phase 3 — test の責務再配置

対象:

- `tests/capture_manifest_test_support.py`（新規）
- `tests/core/test_capture_manifest.py`
- `tests/export/test_capture_publish.py`（新規）

### 8.1 shared value fixture

- [x] minimal な `_capture_provenance()` fixture factory だけを test support file へ移す。
- [x] production helper や pytest plugin にしない。
- [x] test module 間の直接 importを作らない。

### 8.2 core test

- [x] explicit provenance serialization を残す。
- [x] recording serialization を残す。
- [x] missing provenance rejection を残す。
- [x] multiple artifact を pure `as_dict()` serialization として残す。
- [x] field validation / implicit conversion rejection を残す。
- [x] `grafix.export.capture_publish` import を 0 件にする。
- [x] filesystem monkeypatch、`tmp_path` publish transaction を残さない。

### 8.3 export publish test

- [x] manifest path policy を移す。
- [x] late collision rollback、duplicate target、multi-artifact publish を移す。
- [x] private staged manifest cleanup policy を移す。
- [x] owned generation discard、事前外部置換、cleanup error aggregation を移す。
- [x] overwrite success / rollback を移す。
- [x] overwrite exact-bool validation を移す。
- [x] moved test の behavior assertion を弱めない。

### 8.4 R4P-001 の時点明示

- [x] external replacement test 名を「discard/identity check 前の置換」であると分かる名前にする。
- [x] test 内の arrangement も replacement が `discard()` 前に完了していることを明示する。
- [x] 非保証 race を固定する source-shape test、expected-failure test、platform-specific testを追加しない。

Phase 3 完了条件:

- [x] core test は core value contract だけを扱う。
- [x] export test は filesystem publish/ownership contract を扱う。
- [x] test 件数の移動を不要な mock permutation で埋めない。
- [x] test node ID の file path 変更以外に semantic coverage を落とさない。

## 9. Phase 4 — contract と文書の正確化

対象:

- `src/grafix/export/capture_publish.py`
- `architecture.md`
- `docs/developer_guide.md`
- `docs/architecture_visualization.md`
- `docs/migration_2026-07-23_r3.md`

### 9.1 best-effort identity cleanup

- [x] `_OwnedCaptureGeneration.discard()` の docstring を検査時点の保証へ限定する。
- [x] `_unlink_if_identity()` と `_unlink_owned_file()` の docstring を同じ前提へ揃える。
- [x] `_publish_capture_generation()` の rollback 説明から atomic な外部差し替え保護の断言を除く。
- [x] architecture 文書に stable namespace 前提と非原子的な compare-then-delete を明記する。
- [x] developer guide を同じ短い contract へ同期する。
- [x] architecture visualization の token/discard labelを best-effort 表現へ同期する。
- [x] platform lock/lease/journal を実装済みのように記載しない。

### 9.2 R3 migration の履歴復元

`git diff 1be99e3^ 1be99e3 -- docs/migration_2026-07-23_r3.md` で確認できる R4 追記だけを戻す。

- [x] Variation 手順2を R3 時点の owned artifact 説明へ戻す。
- [x] `artifact owner 構築 failure` 行を R3 の結果表へ戻す。
- [x] runtime adapter の publish-time identity/token 詳細を削除する。
- [x] `MpDraw` worker revision barrier の R4 詳細を削除する。
- [x] R3 冒頭に「R3 時点の移行履歴」であることと現行 architecture 正本への短い参照を加える。
- [x] 新しい R4 migration 文書を作らない。
- [x] historical review/plan 文書は変更しない。

Phase 4 完了条件:

- [x] current architecture/developer/visualization の保証が実装可能範囲と一致する。
- [x] R3 migration に R4 固有の implementation detail が残らない。
- [x] current contract と historical migration の正本が区別できる。

## 10. Phase 5 — 検証

### 10.1 focused tests

- [x] `tests/core/test_capture_manifest.py`
- [x] `tests/export/test_capture_publish.py`
- [x] `tests/export/test_capture_service.py`
- [x] `tests/interactive/runtime/test_variation_thumbnail_capture.py`
- [x] `tests/interactive/parameter_gui/test_variation_controller.py`
- [x] `tests/interactive/runtime/test_recording_session.py`
- [x] `tests/interactive/runtime/test_export_job_system.py`
- [x] `tests/interactive/runtime/test_capture_export_safety.py`
- [x] `tests/interactive/runtime/test_draw_window_system.py`
- [x] `tests/api/test_runner_authoring_composition.py`

### 10.2 architecture/static

- [x] `tests/architecture`
- [x] `ruff check src/grafix` と変更 test/support file
- [x] `mypy src/grafix`
- [x] `git diff --check`

### 10.3 inventory

- [x] `rg` で `write_capture_manifest` の参照が historical review/plan だけであることを確認する。
- [x] `rg` で bare `publish_capture_generation` の production/callsite 参照がなく、
  architecture tombstone guard だけに残ることを確認する。
- [x] `rg` で core manifest test に export import がないことを確認する。
- [x] `rg` で runtime thumbnail adapter に concrete `CaptureService` import がないことを確認する。
- [x] `rg` で R3 migration に R4 固有の identity/revision barrier 表現がないことを確認する。
- [x] root/API/stub inventory で owned tokenまたはrollback APIが増えていないことを確認する。

### 10.4 今回実行しない検証

- full pytest
- manual GUI smoke
- process soak
- benchmark

focused/static に新しい failure や timeout退行がある場合だけ、追加検証の許可を確認する。

検証結果:

```text
focused tests + tests/architecture: 285 passed in 28.84s
ruff: All checks passed
mypy src/grafix: Success: no issues found in 290 source files
git diff --check: success
```

## 11. Definition of Done

- [x] R4P-001: cleanup の保証が検査時点の best-effort contract として実装・文書・testで一致する。
- [x] R4P-001: platform-specific lock/lease/quarantine frameworkを追加していない。
- [x] R4P-002: unused `write_capture_manifest()` と専用test、export entryが削除されている。
- [x] R4P-003: publish primitive が private で、thumbnail adapter は narrow callableに依存する。
- [x] R4P-003: concrete service fake の `Any` castがない。
- [x] R4P-004: core manifest test と export publish test の責務が分離されている。
- [x] R4P-005: R3 migration がR3時点の履歴へ戻り、現行正本への参照が明確である。
- [x] public root/API/stub、capture result、recording、export job、variation保存の意味が不変である。
- [x] compatibility shim、汎用framework、依存追加がない。
- [x] focused tests、architecture tests、Ruff、mypy、`git diff --check` が成功する。

## 12. 実施記録

| Phase | 状態 | 完了内容 | 未完了・判断 |
|---|---|---|---|
| 0 | 完了 | HEAD、branch、差分、callsite、baseline を再確認 | 対象 file に競合差分なし |
| 1 | 完了 | dead primitive を削除し、publish primitive と architecture guard を private 境界へ整理 | 旧 public 名は再導入防止の tombstone guard にだけ保持 |
| 2 | 完了 | thumbnail adapter を bound callable Protocol 依存へ変更し、composition root で配線 | owned token の public 化なし |
| 3 | 完了 | core value test と export filesystem test を分離し、最小 test helper を共有 | semantic coverage の意図的な削減なし |
| 4 | 完了 | cleanup の保証範囲を正確化し、R3 migration を履歴へ復元 | atomic compare-and-delete は非対応と明記 |
| 5 | 完了 | 285 tests、Ruff、mypy、inventory、`git diff --check` が成功 | 計画どおり full pytest、GUI、soak、benchmark は未実行 |
