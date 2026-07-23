# Ownership / import migration notes (2026-07-23)

この変更は authoring import、output transaction、parameter load metadata、Parameter GUI cache、公開
facade の owner を一意にする破壊的変更である。削除した path/API の compatibility wrapper、alias、
re-export shim は追加していない。

通常の sketch は引き続き `from grafix import G, E, L, P, run` を使える。deep import を使う extension、
tool、test は以下の差分を同時に移行する。

## 1. 公開 facade と core-only import

`grafix` と `grafix.api` は lazy facade になった。公開名と object identity は維持するが、実装 group は
必要になるまで importしない。

- `import grafix` だけでは `grafix.api` を初期化しない。
- `G` / `E` / `L` / `P` の参照では render、export、variation batch、interactive runner を loadしない。
- `run` は参照時ではなく呼び出し時に runner を loadする。
- `grafix.export` や `grafix.cc` submoduleを先に importしても、root の `export` / `cc` は同じ callableを
  返す。
- `import grafix.core.geometry` は API、export、parameter storage、runtime config loader を loadしない。

公開利用では deep implementation import を避け、次を正規入口にする。

```python
from grafix import G, RenderSession, export, render, run
```

core module から convenience のために root facade を importすると core-only contract を壊す。domain
code は必要な core moduleを直接 importし、outer capability は composition root から注入する。

## 2. Authoring loader と snapshot import

削除した path:

- `grafix.core.authoring_loader`

新しい owner:

- `grafix.authoring_loader`: config authoring directory の探索、source bytes capture、candidate catalog 構築
- `grafix._snapshot_import`: config authoring/source reload が共有する temporary import transaction
- `grafix.core.authoring_definitions`: registration target と immutable snapshot のみ
- `grafix.core.authoring_recipe`: worker-safe な immutable source recipe

import を更新する。

```python
# 旧: ImportError
from grafix.core.authoring_loader import load_config_authoring_definitions

# 新
from grafix.authoring_loader import load_config_authoring_definitions
```

`grafix._snapshot_import` は public extension API ではない。独自の finder、`sys.meta_path` mutation、
`sys.modules` cleanup を実装せず、config authoring は `grafix.authoring_loader`、source reload は既存
runtime compositionを使う。共通 import primitive は一つの reentrant lock で process-global mutationを
直列化し、`BaseException` でも finder と未採用 candidate moduleを除去する。

config candidate は catalog snapshot 完成後に moduleを除去する。source reloadだけが採用中の
last-good generation moduleを保持する。source discovery、relative-import policy、catalog registration、
accept/rollback を低水準 import primitiveへ移さない。

## 3. Explicit RuntimeConfig と output transaction

`grafix.export` は runtime config discoveryを行わない。application entry pointで解決した同じ
`RuntimeConfig` を output path helperへ明示的に渡す。

`config=` が必須になった helper:

| helper | 必須入力 |
|---|---|
| `output_path_for_draw(...)` | `draw=...`, `config=RuntimeConfig` |
| `default_param_store_path(draw, ...)` | `config=RuntimeConfig` |
| `default_png_output_path(draw, ...)` | `scale=...`, `canvas_size=...`, `config=RuntimeConfig` |
| `default_video_output_path(draw, ...)` | `config=RuntimeConfig` |
| `default_workspace_state_path(draw, ...)` | `config=RuntimeConfig` |

```python
from grafix.export.image import default_png_output_path
from grafix.export.output_paths import default_param_store_path, output_path_for_draw
from grafix.runtime_config_loader import load_runtime_config

config = load_runtime_config(".grafix/config.yaml")

svg_path = output_path_for_draw(
    kind="svg",
    ext="svg",
    draw=draw,
    config=config,
)
parameter_path = default_param_store_path(draw, config=config)
png_path = default_png_output_path(
    draw,
    scale=3.0,
    canvas_size=(300, 300),
    config=config,
)
```

`config=None` や helper 内の `runtime_config()` fallback はない。test/tool は ambient CWD/config に
依存せず、fixture で作った configを渡す。

### 3.1 Variation batch transaction

`render_variation_batch()` の公開導線は維持するが、内部 ownerを分割した。

- `grafix.api.variation_batch`: variation request/order、unknown variation、itemごとの transient rollback、
  render/capture callback、partial failure
- `grafix.export.variation_batch`: private workspace、thumbnail manifest relocation、contact sheet/summary
  encode、no-clobber generation retry、overwrite publish failure時の旧 generation復元

API側で `json` / `os` / `shutil` / `tempfile`、fsync/link/replace、staging/publish codecを再実装しない。
export側から API/interactive を importしない。batch directory全体が一つの公開 generationである。

### 3.2 Interactive variation thumbnail

Parameter GUI leafから export dependencyを削除した。GUI extensionは capture/preview callableだけを扱う。
concrete adapterは `grafix.interactive.runtime.variation_thumbnail_capture` にあり、`CaptureService`、
live frame provider、base path、canvas sizeを受ける。capture要求ごとに current frameを取得するため、
以前の frameを closureに固定しない。返り値は `CaptureService` が実際に公開した no-clobber pathである。

## 4. ParamStoreLoadResult / ParameterLoadState

三つの load API は同じ frozen resultを返す。

```python
from grafix.parameter_storage import (
    read_param_store,
    recover_param_store_session,
    recover_primary_param_store,
)

read_result = read_param_store(path)
primary_result = recover_primary_param_store(path)
session_result = recover_param_store_session(path)

store = session_result.store
status = session_result.status
provenance = session_result.load_state.provenance
diagnostics = session_result.load_state.diagnostics
error = session_result.error
```

`ParamStoreLoadResult` の field:

- `store: ParamStore`
- `status: Literal["missing", "loaded", "partial", "invalid"]`
- `load_state: ParameterLoadState`
- `error: Exception | None`

`ParameterLoadState` は `provenance` (`primary` / `session_recovery` / `quarantined`) と immutable
diagnostics tupleを持つ I/O-free core valueである。

削除した contract:

- `ParamStoreReadResult` / `ParamStoreReadStatus`
- `ParamStore.load_provenance`
- `ParamStore.load_diagnostics`
- `ParamStore.accept_loaded_state()`
- `ParamStoreRuntime` の load provenance/diagnostics field

`recover_param_store_session(path)` を `ParamStore` として直接使っていた codeは `.store` を取得する。

```python
# 旧
store = recover_param_store_session(path)
state = store.get_state(key)

# 新
loaded = recover_param_store_session(path)
state = loaded.store.get_state(key)
```

storage moduleから `ParamStore._runtime_ref()` へ metadataを書き込む経路はない。load stateは parameter
persistence、history、adjustment snapshot、transient rollbackに含めない。

interactive の `ParameterSession` は current `ParameterLoadState` を所有する。Keep/Discard は detached
store と load state を束ねた新 result を返し、session が一箇所で自身の store contents と load state を
同時に採用する。capture/record/export は frame ごとに current provenance provider を読む。一方、
headless `RenderSession` は構築時 load result を
`RenderSession.metadata.parameter_load_state` と capture provenance へ固定する。

## 5. Parameter GUI table internals

削除した path:

- `grafix.interactive.parameter_gui.store_bridge`

shimはない。内部 extension/testは変更理由ごとの canonical moduleへ移す。

| 責務 | 新しい owner |
|---|---|
| snapshotからmodel、row order、visibility、filter/search | `parameter_gui.table_view` |
| effective source badgeの導出 | `parameter_gui.source_badge` |
| model/view/visibility/search corpus cache | `ParameterTableViewCache` |
| row edit、effect order、collapse、MIDI clear、history unit、render commit | `parameter_gui.table_commit` |
| pure ImGui boundary (`TableRenderInput -> TableEdits`) | `parameter_gui.table` |
| cache/widget/table-view lifetime | `ParameterGuiSessionState` |

queryは session-owned cacheを必須注入する。

```python
from grafix.interactive.parameter_gui.table_view import (
    ParameterTableViewCache,
    parameter_table_view_for_store,
)

cache = ParameterTableViewCache(catalog)
view = parameter_table_view_for_store(
    store,
    cache=cache,
    show_inactive_params=False,
)
```

module-global cache、default catalog fallback、global build counter、global clear APIはない。同じ storeを
表示する複数 GUI sessionは独立した cacheを持つ。catalog交換は当該 sessionだけを invalidationし、
`ParameterGuiSessionState.close()` が table view、cache、widget stateをまとめて clearする。

commitは `table_commit.commit_table_edits()` または `render_store_parameter_table()` を使う。private
cache/containerへ到達したり、query moduleに mutationを戻したりしない。

## 6. 移行チェック

1. public sketch importを `from grafix import ...` に揃える。
2. authoring loader importを top-level pathへ変更する。
3. output path helperの全 callsiteへ同じ `RuntimeConfig` を渡す。
4. read/recovery結果を `.store` / `.status` / `.load_state` / `.error` として扱う。
5. ParamStoreに load metadataを書かない。
6. GUI table queryへ session-owned `cache=` を渡し、commitは `table_commit` へ移す。
7. GUI leafから export service/type importを削除し、runtime callbackを注入する。

旧 path/APIを検出する例:

```bash
rg 'grafix\.core\.authoring_loader|parameter_gui\.store_bridge' src tests
rg 'ParamStoreReadResult|ParamStoreReadStatus|accept_loaded_state' src tests
rg '\.load_provenance|\.load_diagnostics' src tests
```

いずれも production consumerでは 0 件にする。歴史的な review/plan/migration documentは検索対象から
除外する。
