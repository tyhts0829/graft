# `src/grafix` R4 実装後コードレビュー（2026-07-24）

## 1. 結論

R4 の実装方針は概ね正しい。

- `MpDraw` の revision barrier は worker 内の局所条件として修正され、protocol や parent scheduler に
  責務を広げていない。
- capture generation の identity/discard ownership は export publish 層へ集約され、runtime adapter と
  controller は明確に単純化された。
- production source は差分全体で 115 行、test は 82 行の純減であり、責務移動後の不要な防御分岐も
  実際に削除されている。
- focused test、architecture test、Ruff、mypy、`git diff --check` はすべて成功した。

一方、**「外部差し替えを保持する」という現在の説明は、非原子的な `stat() -> unlink()` が保証できる
範囲より強い**。実行中の置換を注入すると外部ファイルが削除されることを再現した。
また、同じ module の `write_capture_manifest()` だけは private staging cleanup failure が成功済み publish
や元の例外を上書きする旧 error policy のままである。

判定:

- High: **0 件**
- Medium: **2 件**
- Low: **3 件**
- R4-001 / R4-002 の主要な責務再設計をやり直す必要: **なし**
- merge 前に少なくとも判断すべき事項: **R4P-001 の保証範囲**

## 2. 対象

- baseline HEAD: `e3d3324`
- branch: `main`
- 対象:
  - 現在の tracked worktree 差分 18 files
  - `docs/plan/src_grafix_architecture_simplicity_improvement_plan_2026-07-23_r4.md`
  - 変更箇所の直接 callsite、test、architecture 文書
- 観点:
  - アーキテクチャ
  - 責務の分離
  - 美しさ
  - シンプルさ
  - 可読性
  - ownership / lifecycle の正しさ

依頼外の既存差分は変更していない。本レビュー文書だけを新規作成した。

## 3. 検証結果

| 確認 | 結果 |
|---|---|
| R4 focused tests | **362 passed** |
| `tests/architecture` | **39 passed** |
| 合計 | **401 passed** |
| Ruff（`src/grafix` と変更 test） | All checks passed |
| `mypy src/grafix` | Success: no issues found in 290 source files |
| `git diff --check` | success |

401 tests は次の三群を独立実行した合計である。

- capture、variation、recording、`MpDraw` の focused 271 tests
- export job / capture export safety の 91 tests
- architecture の 39 tests

full pytest、GUI smoke、process soak は実行していない。

### 3.1 競合の独立再現

`_unlink_owned_file()` が identity を確認した直後、実際の `Path.unlink()` より前に同名 path を
`os.replace()` するよう注入した。

```text
target_exists False
replacement_exists False
```

identity を確認した owned file ではなく、後から置かれた external replacement が削除された。

### 3.2 staging cleanup error の独立再現

`write_capture_manifest()` の publish 自体を成功させ、最後の private staged manifest の
`unlink()` だけを失敗させた。

```text
OSError cleanup failed
target_exists True
```

呼び出し側には失敗が返る一方、正式 target は既に公開済みになる。

## 4. Findings summary

| ID | 優先度 | 観点 | 要約 |
|---|---|---|---|
| R4P-001 | Medium | ownership、正しさ | identity check と unlink の間の置換で external file を削除し得る |
| R4P-002 | Medium | lifecycle、単純性 | `write_capture_manifest()` だけ staging cleanup の error policy が異なる |
| R4P-003 | Low | 境界、可読性 | private owned contract が module 間の de facto API になっている |
| R4P-004 | Low | test 責務 | export publish test が `tests/core` の manifest test に混在する |
| R4P-005 | Low | 文書、履歴 | 後続 R4 の詳細を R3 migration 正本へ追記している |

## 5. 詳細

### R4P-001 [Medium] identity check と unlink の間の置換で external file を削除し得る

#### 根拠

- `src/grafix/export/capture_publish.py:111-120`
  - publish failure 用 `_unlink_if_identity()` も `stat() -> unlink()` の二操作である。
- `src/grafix/export/capture_publish.py:123-135`
  - strict discard 用 `_unlink_owned_file()` も同じ二操作である。
- `src/grafix/export/capture_publish.py:36-37`
  - docstring は「外部差し替えを保持」と無条件に読める。
- `architecture.md:519-523`
  - architecture 文書も identity mismatch の外部差し替えを保持すると説明する。
- `tests/core/test_capture_manifest.py:392-416`
  - test は `discard()` 開始前に置換済みの場合だけを検証する。

#### 問題

現在の実装が保持できるのは、`stat()` の時点ですでに観測できる replacement である。
`stat()` 後、`unlink()` 前に同名 directory entry が交換されると、unlink は新しい entry に作用する。

R4 は publish 後から runtime 側の identity 再取得までにあった広い競合窓を正しく削除したが、
compare-and-delete 自体は atomic ではない。したがって R4 の改善を否定する問題ではないものの、
現在の保証表現と実装には差がある。

R4 計画も OS-level `stat -> unlink` race を非対象としているため、狭い残存競合として Medium とする。

#### 改善方向

次のどちらかを明示的に選ぶ。

1. **単純性を優先する**
   - contract を「identity check より前に完了し、そこで観測できた置換を保持する best-effort cleanup」
     に狭める。
   - docstring、architecture、developer/migration 文書を同じ表現へ直す。
   - unlink 直前の置換は非保証であることを test 名または注記で固定する。
2. **並行 writer に対して強い保証を持つ**
   - shared lock/lease、または quarantine rename と no-clobber restore を含む compare-and-delete を
     独立設計する。
   - identity check と delete の間へ replacement を注入する deterministic test を追加する。

後者は局所的な `Path` helper の修正では済まない。今回の方針を維持するなら、前者が最小で正確である。

### R4P-002 [Medium] `write_capture_manifest()` だけ staging cleanup の error policy が異なる

#### 根拠

- `src/grafix/export/capture_publish.py:93-99`
  - `_cleanup_staged_manifest()` は private cleanup の `OSError` を best-effort として扱う。
- `src/grafix/export/capture_publish.py:306-309`
  - generation publish はこの helper を使い、成功または primary publish error の意味を維持する。
- `src/grafix/export/capture_publish.py:323-336`
  - `write_capture_manifest()` は同じ `_stage_manifest()` を使うが、`finally` で
    `staged.unlink(missing_ok=True)` を直接呼ぶ。

#### 問題

direct unlink が失敗すると次のどちらも起こり得る。

- target の publish は成功したのに、cleanup error を送出する。
- collision、link、fsync など本来の primary error を cleanup error で隠す。

呼び出し側から見ると「例外なのに target は存在する」状態になり、安易な再試行と相性が悪い。
repository 内の production callsite は現在 0 件だが、関数は `__all__` に含まれるため、無視できる
private dead code とも言い切れない。

#### 改善方向

- 正式に残すなら、`finally` を `_cleanup_staged_manifest(staged)` に統一し、成功時と primary failure
  時を各 1 test で固定する。
- subsystem contract として不要なら、未使用の `write_capture_manifest()` と専用 test を削除する方が
  さらに単純である。
- 互換 wrapper や第二の cleanup helper は追加しない。

### R4P-003 [Low] private owned contract が module 間の de facto API になっている

#### 根拠

- `src/grafix/export/capture.py:431-440`
  - `CaptureService._export_owned()` が private token を返す。
- `src/grafix/interactive/runtime/variation_thumbnail_capture.py:40-64`
  - 別 subsystem の runtime adapter が concrete service の private method を直接呼ぶ。
- `tests/interactive/runtime/test_variation_thumbnail_capture.py:25-59`
  - fake が private method の全 keyword signature を複製し、service を `Any` cast して渡す。
- `src/grafix/export/capture_publish.py:227-234,340-343`
  - `publish_capture_generation()` は `__all__` に残る一方、return annotation は
    `_OwnedCaptureGeneration` である。

#### 影響

root/public API は汚しておらず、現時点の callsite も少ないため correctness defect ではない。
ただし「private」という名前と、複数 module が依存する実際の contract が一致せず、
generic export の private signature 変更が runtime test まで波及する。

#### 改善方向

- capture publish primitive を internal とするなら、名前と `__all__` も一貫して private にする。
- subsystem API とするなら、返す capability の名前と保証を明示する。
- consumer が増えない限り、汎用 repository や ownership framework は作らない。

### R4P-004 [Low] export publish test が `tests/core` の manifest test に混在する

#### 根拠

- `tests/core/test_capture_manifest.py:251-486`
  - 今回追加した主な test は hard-link publish、inode ownership、外部置換、strict cleanup を扱う。
- 同 file は現在 657 行で、manifest value/serialization と filesystem transaction の二責務を持つ。
- production architecture では filesystem publish/rollback は `grafix.export` の責務である。

#### 改善方向

- publish/write/owned-discard 系を `tests/export/test_capture_publish.py` へ移す。
- `tests/core/test_capture_manifest.py` は manifest value、validation、serialization に限定する。
- 正しさへの影響はないため、次回この test 群を変更する時の整理でよい。

### R4P-005 [Low] 後続 R4 の詳細を R3 migration 正本へ追記している

#### 根拠

- `docs/migration_2026-07-23_r3.md:1-13`
  - 文書自身を R3-001〜R3-010 の移行正本と定義する。
- `docs/migration_2026-07-23_r3.md:189-215,312-319`
  - 後続 R4 の publish-owned token と worker revision barrier の最終状態を追記する。
- 同じ現行説明は `architecture.md`、`docs/developer_guide.md`、
  `docs/architecture_visualization.md` にも存在する。

#### 改善方向

- 現行内部 architecture の正本は `architecture.md` に置く。
- R3 migration は当時の移行履歴として保持する。
- public migration がない R4 なら、新しい長い migration 文書は不要である。必要なら architecture への
  短い参照だけにする。

## 6. 良い点

### 6.1 worker revision barrier は局所的で読みやすい

`src/grafix/interactive/runtime/_mp_draw_worker.py:130-164` は、

1. payload を current state へ適用する
2. requested/current revision を比較する
3. 一致時だけ worker-owned pair を評価する

という直線的な flow になった。旧 payload local と worker current の二重 ownership が消え、
stale task が `_TaskStarted` より前に終了することもコードから読み取れる。

### 6.2 filesystem ownership の owner は適切な層へ移った

`src/grafix/export/capture_publish.py:22-50,227-311` に identity と discard capability が集まり、
publish failure の best-effort rollback と explicit discard の strict error aggregation も分離されている。
runtime に同じ stat/unlink helper を再実装しない判断は正しい。

### 6.3 runtime と controller は明確に小さくなった

- `variation_thumbnail_capture.py`: live frame、portable path、thumbnail size、no-frame policy
- `variation_controller.py`: prepare/capture/commit の順序と commit failure 時の discard

に責務が絞られた。到達不能な hostile-callback fault matrix を削除したことも、typed internal flow と
整合する。

### 6.4 `ParamStore` 文書は実装責務と一致した

「薄いデータ入れ物」ではなく、logical state、revision/history、cache invalidation、
transient rollback の aggregate owner と説明する変更は正確である。terminal ownership transfer の
記載も、実際の参照 swap と一致する。

## 7. 推奨順序

1. R4P-001 について、atomic な外部 writer 保護を実装するか、contract を best-effort に限定するか決める。
2. R4P-002 の `write_capture_manifest()` を共通 cleanup policy へ揃えるか、未使用 primitive を削除する。
3. R4P-003〜005 は次回関連箇所を触る際に整理する。

R4P-001/002 のために transaction framework、repository、compatibility shim を追加する必要はない。

