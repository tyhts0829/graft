# direct script の spawn operation identity 恒久修正計画（2026-07-27）

- 状態: **完了（2026-07-28）**
- 対象: direct 実行した単一ファイル sketch と spawn worker 間の operation identity
- 計画作成時 HEAD: `eed82b4`
- 計画作成時 branch: `main`
- 実装開始時 HEAD: `e547e30`
- 実装開始時 branch: `main`
- 実装開始時 working tree: clean
- 発端となった例:
  `sketch/agent_loop/runs/run_20260727_120003_n3/final/sketch.py`
- 実装前の局所回避:
  同 sketch の `run(..., n_worker=0)`（修正後に撤去済み）

本計画は、`python sketch.py` で直接起動した sketch に同一ファイル定義の
custom primitive/effect がある場合でも、既定の spawn マルチプロセス描画を安全に使えるようにする。
production code、test、既存文書、対象 sketch は、本計画への明示承認後に変更する。

## 1. 現象と原因

親 process では direct entrypoint module の名前が `__main__`、
`multiprocessing` の spawn worker では同じファイルが `__mp_main__` になる。

Grafix の evaluation fingerprint は、評価関数が参照する definition graph を符号化する。
通常の同一ファイル関数だけなら module 名は fingerprint に残らないが、次の依存は
型または external symbol の module identity を含む。

- 同一ファイルで宣言した dataclass / Enum の型または instance
- `functools.lru_cache` など、同一ファイルの関数を包む callable wrapper
- `cache_policy="none"` の dynamic operation が持つ `source_owner`

このため、意味も source file も同じ operation が親では `__main__`、worker では
`__mp_main__` として別 fingerprint になり、worker が返した Geometry の `OpRef` を
親の strict evaluation catalog で解決した時点で `CatalogMismatchError` になる。

調査では、dataclass-only、`lru_cache`-only、両者併用の standalone direct script を
実 spawn し、いずれも現行コードで不一致を再現した。単なる同一ファイル helper 関数のみの
ケースは一致しており、全 sketch や multiprocessing 全般の故障ではない。

## 2. 採用する恒久設計

process 起動方式が作る一つの既知 alias だけを、semantic identity の生成時に正規化する。

```python
def canonical_authoring_module_name(name: str) -> str:
    return "__main__" if name == "__mp_main__" else name
```

実際の関数名は実装時に repository の命名規則へ合わせるが、意味は上記から広げない。
private な pure helper を新設し、同じ規則を fingerprint、dynamic operation owner、
parameter site identity から共有する。

この向きを採る理由は次のとおり。

1. 親の direct execution は現在も `__main__` なので、既存の親 fingerprint、GeometryId、
   parameter identity を変えず、誤って分岐した worker 側だけを親へ収束できる。
2. `__main__` と `__mp_main__` を第三の sentinel へ変換する案と異なり、新しい identity 空間や
   migration を作らない。
3. 通常 module、source reload、config authoring の既存 canonical namespace を変更しない。
4. catalog の厳密比較を維持したまま、operation を定義した意味が同じ場合だけ一致させられる。

### 2.1 適用点

- `definition_fingerprint._fingerprint_module_name()`
  - `__grafix_fingerprint_name__` と parent package の explicit canonical marker を先に評価する。
  - どちらもない最終 fallback の実 module 名だけを正規化する。
- Enum の型 module identity
  - 現在の `enum_type.__module__` 直書きを同じ canonical module identity に通す。
- dataclass instance の型 module identity
  - 現在の `value_type.__module__` 直書きを同じ規則に通す。
- `operation_authoring._source_owner()`
  - module の `__grafix_source_owner__` がある場合は、その明示値を無加工で優先する。
  - marker がない fallback module 名だけを正規化する。
- `parameters/key.py` の direct-module fallback
  - `__grafix_source_owner__` がある場合は、その明示値を無加工で優先する。
  - marker がない場合だけ `frame.f_globals["__name__"]` を正規化して、
    `_code_file_id()` / `_automatic_site_id()` の cache key に渡す。

### 2.2 変更しない identity

次は実際の runtime 状態や解決位置を表すため、`__mp_main__` のまま保持する。

- `sys.modules` の key と module object の `__name__`
- callable/class の `__module__`
- pickle/import locator
- `OpDeclaration.provenance` などの診断情報
- error message に表示する raw runtime module 名

`OperationCatalog.resolve_ref()` の fingerprint 完全一致も変更しない。不一致を operation 名だけで
解決する fallback、worker Geometry の ref の付け替え、同期描画への自動 fallback は追加しない。

## 3. 実装後に守る contract

- direct `python sketch.py` と spawn worker が同じ source definition を評価した場合、
  operation evaluation fingerprint と dynamic `source_owner` が一致する。
- `__main__` 側の既存 fingerprint / GeometryId / parameter site ID は変更しない。
- `__mp_main__` 以外の module 名は一切統合しない。
- encoder が従来追跡している code、external module content、default、closure、参照 global の
  意味が変われば、引き続き fingerprint は変化する。
- `__grafix_fingerprint_name__`、`__grafix_source_owner__`、module content fingerprint は
  alias fallback より常に優先される。
- source reload の `_grafix_watch_source.*` と config authoring の
  `_grafix_config_authoring.*` identity は bit-for-bit で維持する。
- direct execution は source snapshot / hot reload へ変換しない。起動後の source 編集を
  同一 definition と偽装しない。
- public API、stub、`run()` の `n_worker` default は変更しない。

「異なる source bytes は常に異 fingerprint」とは約束しない。たとえば dataclass instance は
現行どおり型名と field 値を意味として扱う。本修正の感度試験は、現行 encoder が追跡対象とする
helper code や external module content などを変えた場合に fingerprint が分離することを確認する。

## 4. Option 1（custom operation の別 module 化）を恒久策にしない理由

custom operation を helper module へ移せば、その sketch 単体の `__main__` / `__mp_main__`
分岐は避けられる。しかし、これは次の理由で局所回避に留まる。

- すべての既存・将来 sketch に「custom operation は entrypoint に置けない」という
  隠れた authoring 制約を課す。
- 単一ファイルで配布・複製・保存できる creative-coding sketch の利点を失う。
- dataclass、Enum、decorated callable、dynamic operation など、新しい依存の追加時に再発する。
- framework が production で明示的に spawn を使う一方、その runtime alias を各作品側へ
  処理させる責務配置になる。
- affected sketch ごとのファイル分割では、framework 全体の回帰テストを作れない。

したがって Option 1 は、恒久修正が入るまでの一時的な escape hatch としてのみ扱う。

## 5. 対象ファイル

### production

- [x] `src/grafix/core/python_module_identity.py`（新規 private helper。名前は実装時確定）
- [x] `src/grafix/core/definition_fingerprint.py`
- [x] `src/grafix/core/operation_authoring.py`
- [x] `src/grafix/core/parameters/key.py`

### tests

- [x] `tests/core/test_definition_fingerprint.py`
- [x] `tests/core/test_operation_declaration.py`（既存配置を確認し、最も近い宣言 test へ追加）
- [x] `tests/core/parameters/test_site_id.py`（既存の parameter identity test 配置へ追加）
- [x] `tests/interactive/runtime/test_direct_entrypoint_mp_draw.py`（新規 integration test）
- [x] 追加の fixture/support file は不要と判断した。

### docs / acceptance sketch

- [x] `README.md`
- [x] `docs/developer_guide.md`
- [x] `sketch/agent_loop/runs/run_20260727_120003_n3/final/sketch.py`
  - framework 検証後に一時的な `n_worker=0` を除き、既定の spawn 描画へ戻す。

private core utility のため root/API export と stub は追加しない。新規 dependency も追加しない。

## 6. 実装フェーズ

### Phase 0 — baseline と再現条件の固定

- [x] 元の traceback と `CatalogMismatchError` の発生位置を特定した。
- [x] 親 `__main__` / worker `__mp_main__` の alias 分岐を特定した。
- [x] dataclass-only、`lru_cache`-only、併用の direct spawn で現象を再現した。
- [x] 全 built-in sketch / 全 multiprocessing 描画の障害ではないことを確認した。
- [x] 実装開始時に HEAD、branch、`git status --porcelain` を本書へ追記する。
- [x] 対象 production/test file に並行差分がないことを再確認する。
- [x] 修正前の親 `__main__` fixture の fingerprint、GeometryId、parameter site ID を記録する。
- [x] focused tests の baseline を実行し、既存 failure と新規再現 failure を区別する。

実装開始時 baseline:

```text
definition fingerprint / operation declaration / parameter site: 30 passed
mp draw / source reload / authoring loader: 93 passed
```

親 `__main__` identity の修正前後比較:

```text
evaluation fingerprint: 6a2877ebe4392323c2f822a9d10e8be5650d01668eecc818d4087e64c92b874f
GeometryId:             bac7e92a748acda337829b426ed3acc3
parameter site ID:      grafix_direct_spawn_parent_identity.py:1:350
```

同じ direct-entrypoint fixture を baseline source と修正後 source へ実行し、三値が完全一致した。

Phase 0 完了条件:

- 修正対象と無関係な差分を一切変更していない。
- 同一 bytes / 同一 file の `__main__` と `__mp_main__` だけで不一致を再現できる。
- 修正前の親 identity を、修正後不変性の比較基準として保存できている。

### Phase 1 — process-main alias の単一定義

- [x] private core module に exact `__mp_main__ -> __main__` helper を追加する。
- [x] exact `str` を受けて `str` を返す pure function とし、I/O、global mutation、runtime lookup を持たせない。
- [x] `__main__`、通常 module、Grafix canonical namespace をそのまま返す unit test を追加する。
- [x] helper を root package や public `__all__` へ export しない。
- [x] compatibility alias、feature flag、第二の正規化 helper を追加しない。

Phase 1 完了条件:

- module alias 規則が repository 内の一箇所にだけ定義される。
- mapping domain が `__mp_main__` 一値から広がっていない。

### Phase 2 — evaluation fingerprint への適用

- [x] `_fingerprint_module_name()` の explicit marker / parent marker 判定後の fallback に適用する。
- [x] Enum の type module identity に適用する。
- [x] dataclass instance の type module identity に適用する。
- [x] diagnostic 用 `_symbol_name()` や callable の raw `__module__` は変更しない。
- [x] `evaluation-spec-fingerprint-v1` の payload tag は変更しない。
- [x] synthetic `__main__` / `__mp_main__` module pair の unit test を追加する。
- [x] dataclass-only、`lru_cache`-only、dataclass/Enum/`lru_cache` 併用の三ケースが
  同一 fingerprint になることを確認する。
- [x] 通常 module 名 `entry_a` / `entry_b` は同一視されない負例を追加する。
- [x] alias が同じでも、追跡対象 helper の code/意味を変えれば fingerprint が変わる負例を追加する。
- [x] explicit `__grafix_fingerprint_name__` と module content fingerprint の優先順位を固定する。

Phase 2 完了条件:

- 親 fixture の修正前 fingerprint / GeometryId が修正後も同じである。
- worker alias のみが親 identity へ収束する。
- source reload/config authoring の canonical fingerprint が変わらない。

### Phase 3 — dynamic operation owner と parameter identity への適用

- [x] `_source_owner()` で explicit `__grafix_source_owner__` を最優先し、fallback だけ正規化する。
- [x] `cache_policy="content"` の primitive/effect declaration が親/workerで一致する test を追加する。
- [x] `cache_policy="none", version=...` の primitive/effect declaration も一致する test を追加する。
- [x] provenance / raw module locator は親と worker の実値を保持する test を追加する。
- [x] parameter key は explicit source owner がない direct-module fallback だけ正規化する。
- [x] cwd 内・cwd 外の file path、および automatic / explicit key の site ID を検証する。
- [x] 親 `__main__` の既存 site ID が変わらないことを固定する。

Phase 3 完了条件:

- cache policy によらず、同じ direct-script operation declaration の evaluation ref が一致する。
- GUI parameter identity が親/workerの module alias だけでは分岐しない。
- explicit source owner と診断用 provenance を上書きしていない。

### Phase 4 — actual spawn の end-to-end 回帰 test

新規 integration test は `python -c` や importlib 擬似実行ではなく、temporary
`sketch.py` を `sys.executable <path>` で直接起動する。これにより親 `__main__` と spawn child
`__mp_main__` の production 条件そのものを作る。

- [x] temporary sketch に module-local dataclass、`lru_cache` helper、custom primitive、`draw()`、
  `if __name__ == "__main__": main()` を定義する。
- [x] GUI/OpenGL を起動せず `SceneRunner(..., n_worker=1)` と production の spawn path を使う。
- [x] worker が返した Geometry を親 catalog が strict resolve / realize できるまで確認する。
- [x] realized coordinates または同等の deterministic output を assertion する。
- [x] worker error がなく、operation ref / GeometryId が親の期待 identity と一致することを確認する。
- [x] test は deadline、`try/finally`、normal `close()` を持ち、process/semaphore を残さない。
- [x] macOS 限定 skip を置かず、明示 `spawn` を使う supported environment 共通の contract とする。

Phase 4 完了条件:

- 現行コードでは本件の `CatalogMismatchError` を再現し、修正後は同じ fixture が成功する。
- catalog comparison、worker protocol、recipe transfer を緩めず成功する。
- automated test は GUI availability に依存しない。

### Phase 5 — 文書化と実作品の workaround 撤去

- [x] `README.md` の spawn 説明へ、direct single-file custom primitive/effect が
  `n_worker=1` で動作する contract を追記する。
- [x] `docs/developer_guide.md` の operation/cache identity 節へ、exact alias rule、
  explicit marker precedence、direct execution は source snapshot ではないことを記載する。
- [x] public API/stub/migration guide の更新が不要であることを再確認する。
- [x] Fault Garden から一時的な `n_worker=0` を削除する。
- [x] ユーザーが実行した元のコマンドで起動し、GUI を操作してから通常終了する。
- [x] fingerprint mismatch traceback がなく、parameter GUI と background drawing が応答することを確認する。

Phase 5 完了条件:

- Fault Garden が同期 fallback なしで描画できる。
- README / developer guide の説明と実際の default runtime が一致する。

### Phase 6 — 最終検証

- [x] focused unit / integration tests を実行する。
- [x] source reload、config authoring、既存 mp draw の回帰 tests を実行する。
- [x] 対象 production/tests へ `ruff` と `mypy` を実行する。
- [x] `git diff --check` を実行する。
- [x] `git diff --stat` と対象 file の diff を読み直す。
- [x] 計画書の完了項目と検証結果を更新する。
- [x] 依頼外差分が変更されていないことを最終確認する。

## 7. 検証コマンド

実装時の対象限定コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  pytest -q tests/core/test_definition_fingerprint.py -k 'main or mp_main'

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  pytest -q tests/interactive/runtime/test_direct_entrypoint_mp_draw.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  pytest -q \
    tests/core/test_definition_fingerprint.py \
    tests/core/test_operation_declaration.py \
    tests/core/parameters/test_site_id.py \
    tests/interactive/runtime/test_mp_draw.py \
    tests/interactive/runtime/test_source_reload.py \
    tests/interactive/runtime/test_direct_entrypoint_mp_draw.py \
    tests/test_authoring_loader.py

ruff check \
  src/grafix/core/python_module_identity.py \
  src/grafix/core/definition_fingerprint.py \
  src/grafix/core/operation_authoring.py \
  src/grafix/core/parameters/key.py \
  tests/core/test_definition_fingerprint.py \
  tests/core/test_operation_declaration.py \
  tests/core/parameters/test_site_id.py \
  tests/interactive/runtime/test_direct_entrypoint_mp_draw.py

mypy \
  src/grafix/core/python_module_identity.py \
  src/grafix/core/definition_fingerprint.py \
  src/grafix/core/operation_authoring.py \
  src/grafix/core/parameters/key.py

git diff --check
```

実機 acceptance:

```bash
/opt/anaconda3/envs/gl5/bin/python \
  /Users/tyhts0829/Documents/Grafix/sketch/agent_loop/runs/run_20260727_120003_n3/final/sketch.py
```

長時間 soak、full pytest、dependency 更新、snapshot 更新、commit、push は本計画に含めない。
必要になった場合は別途確認する。

最終検証結果:

```text
focused unit / mp / source reload / authoring / architecture: 168 passed
ruff: All checks passed
mypy: Success: no issues found in 4 source files
git diff --check: success
parent identity comparison: fingerprint / GeometryId / parameter site ID unchanged
Fault Garden GUI smoke: default n_worker=1, drawing and Parameter GUI visible, exit code 0
```

actual-spawn integration test は修正前 source で本件と同じ `CatalogMismatchError` を再現し、
修正後 source では親 catalog の strict resolve / realization まで成功した。

## 8. 非対象

- custom operation を全 sketch から helper module へ移すこと
- `n_worker` の既定値や multiprocessing architecture の変更
- fork/forkserver 向けの別 identity 規則
- `OperationCatalog.resolve_ref()` の比較緩和
- operation 名による fallback resolution
- worker Geometry / OpRef の書き換え
- fingerprint mismatch 時の `n_worker=0` 自動 fallback
- source reload、config recipe、capture protocol の再設計
- `python -c`、stdin、notebook など file identity が曖昧な entrypoint の一般化
- dataclass class method 全体の source digest 化など、既存 encoder semantics の拡張
- public compatibility shim、feature flag、migration layer

## 9. リスクと停止条件

### リスク

- 正規化を raw runtime identity に広げると、pickle/import/provenance が壊れる。
- explicit Grafix canonical marker より先に正規化すると、source reload/config authoring の
  captured-source identity を壊す。
- fingerprint だけを直し `source_owner` を直さないと、
  `cache_policy="none"` の operation で不一致が残る。
- external symbol だけを直し Enum/dataclass instance の直書きを残すと、別の definition graph で再発する。
- unit test だけでは actual spawn の module alias と catalog round-trip を検証できない。

### 実装を止めて確認する条件

- 親 `__main__` の既存 fingerprint、GeometryId、parameter site ID が変わる。
- `__mp_main__` 以外の通常 module identity が collapse する。
- 現行 encoder が追跡する helper code / external module content の意味変更を検出できなくなる。
- source reload/config authoring の explicit canonical identity または captured-source test が変わる。
- provenance、pickle、stub generation、import resolution に変更が必要になる。
- strict catalog comparison を弱めないと actual spawn test を通せない。
- 対象 file に本計画と意味が衝突する並行差分が生じる。

停止条件に当たった場合、framework 変更を一旦全て戻す。feature flag や半端な dual path は残さず、
原因を再設計するまで個別 sketch の `n_worker=0` または Option 1 の module 分離を一時回避として使う。

## 10. 完了基準

- exact `__mp_main__ -> __main__` 規則を一つの private helper で説明できる。
- 親側の既存 identity を変えず、worker 側だけが同じ semantic identity へ収束する。
- dataclass、Enum、`lru_cache` wrapper、dynamic operation の各経路を test で覆う。
- actual direct-script spawn から親 catalog realization まで自動回帰 test が通る。
- source reload/config authoring、通常 module、意味変更検出の負例が維持される。
- catalog の厳密性、worker protocol、public API、`n_worker` default を変更していない。
- Fault Garden の `n_worker=0` workaround を撤去し、元の起動方法で操作できる。
- README / developer guide と実装が一致する。
- focused pytest、ruff、mypy、`git diff --check` が成功する。
- 計画書に実行結果と未完了項目が反映されている。

## 11. 計画作成時の依頼外差分

計画作成前の `git status --porcelain` は次のとおり。これらは本件の対象外として触らない。

```text
 M .agents/skills/grafix-art-loop/SKILL.md
 M .agents/skills/grafix-art-loop/scripts/make_contact_sheet.py
 M sketch/readme/6.py
 M sketch/readme/7.py
?? docs/plan/grafix_art_loop_contact_sheet_output_plan_2026-07-25.md
```

計画作成時点では、本計画ファイル自身だけをこの依頼の差分として追加した。
実装開始時の `e547e30` では working tree が clean になっており、上記差分は本実装の外で
すでに解消されていた。本実装からこれらの file は変更していない。

## 12. 承認と実施境界

- 2026-07-28 に明示承認を受け、Phase 0 から順に実施した。
- 停止条件には該当しなかった。
- dependency 追加、破壊的操作、full test、commit、push は別途依頼または承認なしに行わない。
