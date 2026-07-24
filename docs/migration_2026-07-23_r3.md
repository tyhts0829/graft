# R3 architecture / public API migration (2026-07-23)

この文書は R3-001〜R3-010 の最終実装へ移行するための正本である。R3 は import namespace、
authoring source、parameter recovery/capture、Variation、MIDI path、ParamStore mutation、
`mp_draw` ownership を一意にする破壊的変更である。

この文書は R3 完了時点の移行履歴を固定する。後続変更を含む現行の内部 architecture は
[`architecture.md`](../architecture.md) を参照する。

**削除した名前、引数、private path に compatibility wrapper、alias、re-export shim はない。**
旧 API と新 API を同時に扱う dual behavior もない。repository 方針どおり、consumer を新しい
一経路へ直接変更する。

同日に先行した config/storage/GUI ownership 移行の詳細は
[`migration_2026-07-23.md`](migration_2026-07-23.md) も参照する。この文書の namespace 記述を
R3 の正本とする。

## 1. 移行一覧

| 旧 contract | 新 contract |
|---|---|
| `from grafix import export` | `from grafix import save` |
| `export(frame, path)` | `save(frame, path)` |
| `from grafix.api.export import export` | `from grafix.api.export import save` |
| `from grafix.api import render` | `from grafix import render` または `from grafix.api.render import render` |
| `from grafix.api import run` | `from grafix import run` または `from grafix.api.runner import run` |
| `from grafix.api import render_variation_batch` | `from grafix import render_variation_batch` または `from grafix.api.variation_batch import render_variation_batch` |
| `from grafix.cc import cc` / `import grafix.cc` | `from grafix import cc` / `import grafix.api.cc` |
| custom lazy module / callable-submodule collision guard | 通常の `ModuleType` と標準 PEP 562 root mapping |
| `RenderSession(..., definitions=...)` / `render(..., definitions=...)` | draw に付与された generation または config authoring の通常解決 |
| `run(..., config_fallback=...)` | public `config=` / `config_path=`。fallback diagnostic は private composition |
| `render_variation_batch(..., capture_service=...)` | public seam を削除。capture seam は module-private |
| `RenderSession.param_store` | 削除。mutable store を公開しない |
| authoring root `__init__.py` | synthetic namespace root のため preflight error |
| function/class 内の deferred relative import | module lexical scope へ移動 |
| recovery session が構築時 schema を保持 | Keep dispatch 時に `ParameterSession` の current schema を渡す |
| parameter source/provenance の別々の provider | 一つの `ParameterCaptureState` provider |
| thumbnail capture 後に domain validation | `prepare -> capture -> commit` |
| MIDI の profile/save directory、optional/default path | exact `snapshot_path: Path` |
| store 外の mutable `_..._ref()` / 手動 `_touch()` | `_ParamStoreRead` で planし、`_ParamStoreMutation` で commit |
| `MpDraw` scalar telemetry properties | frozen `MpDrawStats` を返す `MpDraw.stats` |
| `mp_draw.py` 一箇所の DTO/state/worker/process 責務 | protocol/state/worker/parent owner の四 module |

## 2. 標準 public namespace と `save`

最終 namespace は次のとおりである。

| 名前 | 意味 |
|---|---|
| `grafix.export` | export subsystem package |
| `grafix.api.export` | 保存 API module |
| `grafix.save` | `Frame` 保存 callable |
| `grafix.api.export.save` | `grafix.save` と同一 callable |
| `grafix.render` | headless render callable |
| `grafix.api.render` | render API module |
| `grafix.api.render.render` | `grafix.render` と同一 callable |
| `grafix.run` | interactive runner callable |
| `grafix.api.runner` | runner API module |
| `grafix.api.runner.run` | `grafix.run` と同一 callable |
| `grafix.cc` | `CcView` object |
| `grafix.api.cc` | `CcView` 定義 module |

正規利用:

```python
from grafix import RenderOptions, render, save

frame = render(
    draw,
    0.0,
    options=RenderOptions(canvas_size=(300, 300)),
)
result = save(frame, "data/output/art.svg")
print(result.path, result.manifest_path)
```

定義 module から取得する場合:

```python
from grafix.api.export import save
from grafix.api.render import render
from grafix.api.runner import run
```

`grafix.api` package は authoring DSL/decorator と公開 value type を提供するが、application callable は
package 直下に再公開しない。`grafix.export` は通常 package であり callable ではない。
Python の標準 import 規則上、`from grafix import export` が package を取得する場合があるため、
旧コードを単に残してはならない。必ず `save` へ明示移行する。

`grafix` / `grafix.api` は通常の `ModuleType` である。旧 `_LazyPublicName`、module subclass、
assignment guard、`sys.modules[...].__class__` の変更は削除済みで、shim はない。

## 3. Runner と public capability の閉鎖

`src/grafix/api/runner.py` は正規 signature/docstring を持つ軽量 wrapper である。`run()` が実際に
呼ばれたときだけ private `src/grafix/api/_runner_application.py` を importする。`grafix.run` の参照、
`inspect.signature()`、help だけでは `pyglet` / `grafix.interactive` を loadしない。

public signature から次を削除した。

- `RenderSession(..., definitions=...)`
- `render(..., definitions=...)`
- `run(..., config_fallback=...)`
- `render_variation_batch(..., capture_service=...)`

通常の application は draw/config から generation を解決する。exact generation や capture fake が必要な
runtime/test は public 引数を増やさず、package-private composition/helper を使う。

`RenderSession.param_store` も削除した。公開 property は `options`、`config`、`runtime_limits`、
`metadata` の immutable value だけである。mutable `ParamStore`、catalog、evaluation context、
cache/resource owner を借用する代替 public API は追加していない。

public signature に現れる `RuntimeConfig`、`ParameterLoadMode`、`ParameterLoadState`、
`ParamStoreLoadDiagnostic`、`LoadProvenance`、`CaptureProvenance`、`SessionProvenance`、
`FrameStyle`、`RealizedLayer` などの共通型は root と `grafix.api` の正式 path から取得できる。
`ConfigProvenance`、`FrameProvenance`、`GitProvenance`、`ParameterSnapshotProvenance`、
`SourceProvenance`、`SceneItem` に加え、public dataclass の再帰 annotation に現れる `Geometry`、
`GCodeParams`、`Layer`、`RealizedGeometry`、`GeometryCacheKey`、`ParamMeta` は advanced
`grafix.api` surface にある。`ParamStore`、`AuthoringDefinitionsSnapshot`、
`RuntimeConfigFallback` は public type graph に含めない。

## 4. Authoring source import

`preset_module_dirs` の各 entry は通常 package ではなく synthetic namespace source root になった。

- root 直下の `__init__.py` は path と理由を示して load 前に拒否する。
- nested package の `__init__.py` は通常 initializer として一度だけ実行する。
- relative import は module lexical scope にだけ置ける。
- module 直下の `if` / `try` / `with` 内は許可する。
- function、async function、class の内側は path、line、scope 付き error で拒否する。
- local helper の absolute import は使わず、package-relative import にする。
- 全 candidate source の preflight が成功するまで一件も実行しない。
- filesystem capture と pickle/worker から復元した recipe の両方が同じ preflight を通る。

旧:

```python
def build():
    from .shapes import make_shape

    return make_shape()
```

新:

```python
from .shapes import make_shape


def build():
    return make_shape()
```

共通 validator は private `grafix._source_import_policy`、一時 import transaction は
private `grafix._snapshot_import` が所有する。deferred import 用の長寿命 finder/module registry は
提供しない。

先行移行で削除した `grafix.core.authoring_loader` にも shim はない。config authoring loader は
`grafix.authoring_loader` から importする。

## 5. Parameter recovery と capture state

interactive の `ParameterSession` が次の current state を一意に所有する。

- 最後に採用された `KnownOperationSchemaSnapshot`
- current `ParameterLoadState`
- `ParamStore`、history、snapshot slots、autosave
- Keep/Discard action と detached load result の採用
- capture-state provider と終了時 persist

source reload が成功した場合だけ current schema を交換する。`ParamStoreRecoverySession` は schema を
構築時に保持せず、Keep action の dispatch 時に `ParameterSession` が current schema を渡す。
Keep/Discard 後の store contents と load state は session が同じ箇所で採用する。

capture/record/export は parameter source と load provenance を別々に読まない。

```python
state = parameter_session.capture_state()
source = state.source
load_provenance = state.load_provenance
```

`capture_state()` は一つの `_load_state` sample から frozen
`ParameterCaptureState(source, load_provenance)` を作る。interactive は provider を frame ごとに
一度だけ読む。headless `RenderSession` は construction-time load result から static state を作り、
metadata/provenance に固定する。

storage API は引き続き `ParamStoreLoadResult(store, status, load_state, error)` を返す。旧
`ParamStoreReadResult`、store 内 load metadata、`accept_loaded_state()` はない。詳細は先行
migration 文書を参照する。

## 6. Variation の確定順

GUI の named variation 保存は次の一方向 flow である。

1. `prepare_variation()` が name/note/seed/t/duplicate、immutable parameter snapshot、base revision を
   I/O 前に検証し、`VariationDraft` を返す。
2. thumbnail callback が exact published path と `discard()` を持つ owned artifact を返す。
3. `commit_variation()` が owner/revision/duplicate を再確認し、一件だけ追加する。
4. commit failure では controller が今回の PNG/manifest artifact family だけを `discard()` する。

callback は同期実行中に `ParamStore` を変更しない contract である。callback または concurrent
change で revision が変わった場合も、commit は state を変更せず artifact rollback へ進む。

結果:

| 経路 | Variation | Thumbnail |
|---|---|---|
| prepare failure | なし | capture しない |
| capture failure | あり | なし |
| artifact owner 構築 failure | あり | 今回分を rollback |
| commit failure | なし | 今回分を rollback |
| success | あり | 実際に publish された exact path |

public `create_variation()` も同じ prepare/commit 経路を使う。汎用 transaction manager や互換経路は
追加していない。

headless variation batch は public `RenderSession` から raw store を取得しない。session 内に閉じた
variation 列挙、一時適用/render、exact rollback capability を使う。public `capture_service=` seam は
削除済みである。

## 7. MIDI の exact `snapshot_path`

production の private composition root は、解決済み `RuntimeConfig` と draw/run ID を
`output_path_for_draw()` へ渡し、MIDI snapshot path を一度だけ確定する。
factory/controller/storage helper はその exact `Path` を必須で受ける。

```python
from pathlib import Path

from grafix.interactive.midi.factory import create_midi_session

midi = create_midi_session(
    port_name="auto",
    mode="7bit",
    snapshot_path=Path("data/output/sketch-midi.json"),
)
```

live load/save、controller 不在時の frozen load、reconnect、discard は同じ path を使う。
reconnect closure も最初の value を captureし、再探索しない。旧 `profile_name` / `save_dir` /
`persistence_path`、optional path、default path helper は受け付けず、MIDI leaf は
`runtime_config_loader`、CWD、HOME、YAML を参照しない。旧 signature の wrapper/alias はない。

MIDI CC JSON schema と MIDI disabled 時の frozen behavior は変更していない。

## 8. ParamStore の read-plan-commit

これは主に repository 内 extension の破壊的 private API 変更である。store 外から mutable container を
借りる `_labels_ref()`、`_variations_ref()`、`_runtime_ref()` 等と、手動 `_touch()` / mutation batch
操作は削除した。

新しい command flow:

1. private `_ParamStoreRead` から copy/frozen value と base revision を読む。
2. sibling command module が validation、no-op 判定、allocation、detached replacement の planを完了する。
3. 必要なら `_ParamStoreMutation.prepare_history()` で変更前観測を commit 前に済ませる。
4. domain-specific commit method が expected revision を再確認し、参照 swap と
   revision/history/cache 更新だけを行う。

sibling command は `_ParamStoreMutation` だけを write port として使い、raw mutable container を
受け取らない。`ParamStore` 自身が所有する construction/contents replacement/transient rollback は
store boundary 内に残る。旧 `_PendingStoreMutation` と汎用 mutation batch API は削除し、一つの
完成済み plan を一つの domain commit で確定する。transient evaluation の rollback は別の owner-bound
`ParamStoreRollback` が担当する。

effective value/source だけが変わる frame merge は
`_ParamStoreMutation.commit_runtime_value_patch()` へ完成済み sparse patch を渡す。full store/runtime
plan を作らず、persistent revision と runtime object identity を保ったまま effective revision だけを
一度進める。

公開 extension は private port を直接使わず、`apply_parameter_edits()`、variation/effect-order/label 等の
domain command を使う。validation/planning failure と no-op は live state、history、revision、cache
identity を変更しない。

parameter JSON schema、history/undo の意味、revision domain は変更していない。

## 9. `mp_draw` の四責務と immutable stats

| module | owner |
|---|---|
| `_mp_draw_protocol.py` | pickle DTO、wire validation、result/error value、frozen `MpDrawStats` |
| `_mp_draw_state.py` | ACK、known revision、latest-wins、stale 判定の I/O-free parent transition |
| `_mp_draw_worker.py` | spawn 可能な top-level entrypoint、worker evaluation/cleanup |
| `mp_draw.py` | parent process、Queue、restart、timeout、close resource |

private DTO の module path は persistent/external wire contract ではない。必要な正式 import surface は
canonical owner moduleが再公開する。

```python
from grafix.interactive.runtime.mp_draw import (
    DrawResult,
    MpDraw,
    MpDrawStats,
    MpDrawWorkerError,
)
```

旧:

```python
broadcasts = mp_draw.snapshot_broadcast_count
acks = mp_draw.snapshot_ack_count
restarts = mp_draw.restart_count
```

新:

```python
stats = mp_draw.stats
broadcasts = stats.snapshot_broadcast_count
acks = stats.snapshot_ack_count
restarts = stats.restart_count
```

`MpDrawStats` は frozen/slots の一時点 snapshot である。telemetry のためだけの lock はない。
`generation`、`evaluation_timeout`、`current_epoch` など制御/lifecycle contract に必要な property は
`MpDraw` に残る。wire field、timeout/restart、latest-wins、last-good、close 順は変更していない。
旧 private module path の shim と scalar telemetry forwarding property はない。

## 10. 同日先行 migration の最終状態

R3 consumer が同時に確認すべき先行変更:

- config authoring loader は `grafix.authoring_loader` にある。
- output path helper は composition root で解決した `RuntimeConfig` を必須 `config=` で受ける。
- parameter load/recovery は frozen `ParamStoreLoadResult` を返す。
- load provenance/diagnostics は `ParamStore` に格納しない。
- 削除した `grafix.interactive.parameter_gui.store_bridge` に shim はない。
- GUI table query は session-owned `ParameterTableViewCache` を明示注入し、mutation は
  `parameter_gui.table_commit` / controller から domain command へ渡す。
- Parameter GUI leaf は export serviceを importせず、runtime capture callbackを受ける。

詳細な旧→新例は [`migration_2026-07-23.md`](migration_2026-07-23.md) にある。

## 11. 移行チェック

1. 保存 callable をすべて `save` へ変更する。
2. application callable を root または定義 module から importする。
3. `grafix.export` を package、`grafix.api.*` dotted name を通常 module として扱う。
4. `grafix.cc` module import を root `cc` object または `grafix.api.cc` module へ変更する。
5. 削除された public composition/test seam と mutable `param_store` borrow を除去する。
6. authoring root/init と relative-import lexical policy を満たす。
7. recovery/capture を current `ParameterSession` state の一 sample へ揃える。
8. Variation artifact を prepare/capture/commit/rollback owner に従って扱う。
9. MIDI leaf に exact `snapshot_path: Path` を渡す。
10. ParamStore private mutationを read-plan-port 経路へ、mp-draw telemetry を `stats` へ移す。

repository consumer の監査例:

```bash
rg 'from grafix import export|from grafix\.api import (render|run|export)'
rg 'from grafix\.cc|import grafix\.cc|RenderSession\.param_store'
python - <<'PY'
import inspect

import grafix

assert "definitions" not in inspect.signature(grafix.RenderSession).parameters
assert "definitions" not in inspect.signature(grafix.render).parameters
assert "config_fallback" not in inspect.signature(grafix.run).parameters
assert (
    "capture_service"
    not in inspect.signature(grafix.render_variation_batch).parameters
)
PY
rg 'profile_name=|save_dir=|persistence_path=' src tests
rg 'mp_draw\.(snapshot_broadcast_count|snapshot_ack_count|restart_count)' src tests
rg 'store\._(labels_ref|variations_ref|runtime_ref|touch(_favorites)?)\(' src/grafix/core/parameters
```

history/review/plan document と、この旧→新比較を含む migration 文書は検索対象から除外する。

## 12. 変更していない contract

- `python -m grafix export` の CLI command 名
- `grafix.export` package
- `ExportFormat` / `ExportResult`
- parameter JSON、capture manifest、workspace、MIDI JSON schema
- Geometry DAG/ID、operation catalog、typed cache identity
- mp-draw wire field、generation、timeout/restart、last-good、close semantics
- external dependency

互換 shim、外部 dependency、汎用 DI/transaction/process framework は追加していない。
