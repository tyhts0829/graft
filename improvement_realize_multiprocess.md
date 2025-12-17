# realize の multiprocessing 化（Queue 通信）実装計画

## 目的

- `graft.core.realize.realize()` による Geometry 評価をマルチプロセス化し、CPU 負荷の高いフレームでのスループットを上げる。
- `graft.api.run(..., n_worker=...)` でワーカープロセス数を指定できるようにする。

## 前提 / 制約（今回の前提として固定）

- 通信は `multiprocessing.Queue` を使う（内部は pipe + pickle による serialize/deserialize）。
- `spawn` 環境で動くことを前提にする（macOS / Windows）。
  - 子プロセスへ渡す **Process target / コールバックは picklable**（= モジュールトップレベル定義）である必要がある。
  - 追加で、ユーザースケッチ側は `if __name__ == "__main__":` ガード必須（README 例と同様）。
- 「賢い」分散はやらず、まずは **シンプルで動く最小実装** を優先する（過度に防御的にしない）。

## スコープ（やる / やらない）

### やる

- `realize` を「メインプロセス → ワーカープロセス」にオフロードする仕組みを追加する。
- `run` に `n_worker` 引数を追加し、interactive 描画で利用できるようにする。
- `pytest` で最低限の動作確認テストを追加する（spawn で動くことを担保）。

### やらない（初期版では見送る）

- ノード単位（DAG 内部）の細粒度スケジューリング（依存解決・共有キャッシュなど）。
  - Queue + pickle 前提だと中間 `RealizedGeometry(np.ndarray)` の往復が増えて逆効果になりやすい。
- 共有メモリ最適化（`shared_memory` / `memmap` / zero-copy）。
- ワーカー死活監視・自動再起動・タイムアウト等（必要になってから足す）。

## 方式案（決定案）

### 決定: 「レイヤー（root Geometry）単位」を並列化する

- `draw(t)` / `normalize_scene` / style 解決は **メインプロセス**のまま。
- 各 Layer の `geometry: Geometry` を **タスクとして Queue で送る**。
- ワーカー側は受け取った `Geometry` に対して既存の `realize(geometry)` を実行し、`RealizedGeometry` を返す。

狙い:
- 送受信する `RealizedGeometry` は「最終結果の分」だけに限定できる（中間結果を送らない）。
- 既存の `realize_cache` / `_inflight` を大きく壊さずに導入できる（ただしキャッシュは “プロセス内”）。

トレードオフ:
- 1 枚の巨大 Geometry だけを描くケースでは並列化が効きにくい（将来の拡張ポイント）。
- 異なるワーカー間でキャッシュ共有できないので、同一サブグラフが複数レイヤーに跨ると重複計算が起きうる。

## 追加コンポーネント（新規）

### `RealizeProcessPool`（仮）: ワーカープール + Queue

- `multiprocessing.get_context("spawn")` で `Queue` と `Process` を作る（start method を固定）。
- 役割:
  - ワーカー起動 / 終了
  - タスク投入（task_id 付与）
  - 結果回収（task_id で突合して入力順に整列）
  - 例外の伝播（traceback 文字列化してメインで `RealizeError` にする）

### Worker の初期化（重要）

- ワーカー側で `primitive_registry` / `effect_registry` が空だと `realize()` が失敗する。
- そのため、ワーカー起動時に以下を import して “組み込み op の登録” を確実に行う:
  - `graft.api.primitives`
  - `graft.api.effects`
- ユーザー定義 primitive/effect について:
  - `spawn` では子プロセスがメインモジュール（= スケッチ）を import するため、
    **トップレベルで decorator 実行されていれば登録される**（`if __name__ == "__main__":` 内に定義すると登録されない）。
  - 初期版では「トップレベル定義を推奨」としてドキュメント化する（動的登録の同期はやらない）。

## 変更点（ファイル案）

- `src/graft/core/realize_mp.py`（新規）
  - `RealizeTask` / `RealizeResult` の dataclass（picklable）
  - ワーカーループ関数（トップレベル定義）
  - `RealizeProcessPool` 本体
- `src/graft/core/pipeline.py`
  - `realize_scene(..., *, realizer=None)` のようにオプション注入で切替可能にする
  - 既定は現状通りシングルプロセス `realize()`
- `src/graft/interactive/runtime/draw_window_system.py`
  - `DrawWindowSystem` が `realizer` を保持して `realize_scene` に渡す
  - close 時に pool を終了（`finally` で確実に呼ぶ）
- `src/graft/api/run.py`
  - `run(..., *, n_worker: int = 0)` を追加（`0/1` は無効=単一、`>=2` で有効）
  - `DrawWindowSystem` に `n_worker`（もしくは `realizer`）を渡して初期化
- （任意）`src/graft/api/export.py`
  - `Export(..., *, n_worker: int = 0)` を追加し、同じ仕組みで高速化
- `tests/core/test_realize_multiprocess.py`（新規）
  - spawn で pool を立ち上げ、`Geometry.create("circle", ...)` 等の結果一致を確認

## キュー通信プロトコル（案）

### Task

- `RealizeTask(task_id: int, geometry: Geometry)`

### Result

- `RealizeResult(task_id: int, ok: bool, payload: RealizedGeometry | str)`
  - `ok=True` のとき `payload` は `RealizedGeometry`
  - `ok=False` のとき `payload` は `traceback.format_exc()`（文字列）

### 終了シグナル

- `None` を sentinel として投入（n_worker 個）し、ワーカーは受信したら break。

## 実装チェックリスト（最初の PR で達成したい最小単位）

- [ ] `src/graft/core/realize_mp.py` を追加（spawn 固定 / Queue / ワーカーループはトップレベル）
- [ ] ワーカー初期化で `graft.api.primitives` / `graft.api.effects` を import（組み込み op 登録）
- [ ] `RealizeProcessPool.realize_many(geometries: Sequence[Geometry]) -> list[RealizedGeometry]` を実装
- [ ] `pipeline.realize_scene` に `realizer` 注入（未指定なら既存 `realize()`）
- [ ] `api.run.run` に `n_worker` を追加し、interactive で有効化できるようにする
- [ ] `DrawWindowSystem` のクローズで pool を停止（run の finally でも担保）
- [ ] `pytest` の最小テスト追加（spawn で動く / 例外伝播も確認）
- [ ] README に `n_worker` と「__main__ ガード必須」「top-level 定義推奨」を追記

## 事前に確認したいこと（あなたに質問）

1. `n_worker` のデフォルトはどうする？
   - 案A: `n_worker: int = 0`（既定は無効、明示指定時のみ mp）
   - 案B: `n_worker: int | None = None`（None で `os.cpu_count()` など自動）
2. 並列化粒度はまず「レイヤー単位」で問題ない？（巨大 1 ジオメトリを速くしたい要件が強いなら別案が必要）
3. `Export` も同じ `n_worker` で同時に対応する？（スコープ内なら一緒にやるのが自然）
4. ユーザー定義 primitive/effect は「トップレベル定義必須（推奨）」で進めてよい？
   - 例: `draw()` 内で decorator 登録するような使い方は初期版では非対応扱い。

