# draw(t) の multiprocessing 化（Queue 通信）実装計画

## 目的

- interactive 描画ループの中で CPU 負荷の高い `draw(t)` をワーカープロセスへオフロードし、メインプロセス（イベント処理 + OpenGL 描画）の停止を避ける。
- `graft.api.run(..., n_worker=...)` でプロセス数を指定できるようにする。

## 前提 / 制約（今回の前提として固定）

- 通信は `multiprocessing.Queue` を使う（内部は pipe + pickle による serialize/deserialize）。
- `spawn` 環境で動くことを前提にする（macOS / Windows）。
  - 子プロセスへ渡す `draw` は picklable（= モジュールトップレベル定義）である必要がある。
  - 追加で、ユーザースケッチ側は `if __name__ == "__main__":` ガード必須（README 例と同様）。
- ワーカーは CPU 計算だけを行う。pyglet window / OpenGL / GPU リソースはメインプロセスのみで扱う。
- Parameter GUI の実体 `ParamStore` はメインプロセスに残す。
  - メインで作った `ParamStore.snapshot()` をワーカーへ渡し、`resolve_params` が参照する `current_param_snapshot` を固定する。
  - ワーカーで観測した `FrameParamRecord` と `set_label(...)` 呼び出しをメインへ戻し、メインで `ParamStore` にマージする。
- 「賢い」分散はやらず、まずは **シンプルで動く最小実装** を優先する（過度に防御的にしない）。

## スコープ（やる / やらない）

### やる

- 「1フレーム = draw(t) + normalize_scene」 をタスクとして並列実行する `DrawProcessPool` を追加する。
- interactive 描画ループでは non-blocking に結果を取り込み、描画は常にメインで行う。
- `n_worker<=1` では現行の同期パス（既存挙動）を維持し、`n_worker>=2` で mp を有効化する。
- `pytest` で最低限の動作確認テストを追加する（spawn で動くことを担保。GUI は立ち上げない）。

### やらない（初期版では見送る）

- 1 回の `draw(t)` の内部を自動分割して並列化する（ユーザーコードの意味論に踏み込むため）。
- `RealizedGeometry(np.ndarray)` を毎フレームワーカーから返す「draw+realize」モード（pickle 転送が重くなりやすいので後回し）。
- 共有メモリ最適化（`shared_memory` / `memmap` / zero-copy）。
- ワーカー死活監視・自動再起動・タイムアウト等（必要になってから足す）。

## 方式案（決定案）

### 決定: フレーム単位の非同期パイプライン（最新結果優先）

- メインプロセスは「イベント処理」「Style 確定」「結果のレンダリング」を担当する。
- ワーカーは `draw(t)` を実行（parameter snapshot を固定）→ `normalize_scene(scene)` → `list[Layer]` を返す。
- 計算が追いつかない場合は「古い結果は捨てる」。UI の追従性を優先する。

### スケジューリング（案）

- `frame_id` を単調増加で付与する。
- タスクキューは `maxsize = n_worker` とし、in-flight を増やしすぎない（キューを膨らませない）。
- `DrawWindowSystem.draw_frame()` では以下を繰り返す:
  - 結果キューを drain して最新 `frame_id` の結果だけ採用する。
  - 空きがあれば現在時刻 `t` でタスク投入する（詰まっていれば投入しない）。
  - 結果が無ければ前回の `RealizedLayer` を描画し続ける。

## Parameter GUI と整合を保つ設計

- ワーカーでは `ParamStore` 本体を持たない。
- worker 用の context を用意し、contextvars を以下で固定する:
  - `param_snapshot` = メインから渡された snapshot
  - `frame_params` = `FrameParamsBuffer()`（終了時に records を回収）
  - `param_store` = `ParamStoreSnapshotProxy`（`set_label` のみ収集）
- メインで結果を採用したタイミングで:
  - 収集された label を `store.set_label(...)` で反映する
  - 収集された records と、メインで生成する layer_style_records を `store.store_frame_params(...)` でマージする

## 追加コンポーネント（新規）

- `src/graft/core/draw_mp.py`（新規）
  - `DrawTask` / `DrawResult` dataclass（picklable）
  - ワーカーエントリポイント（トップレベル関数）
  - `DrawProcessPool`（start/close, submit, poll）
  - `ParamStoreSnapshotProxy`（label 収集）
- `src/graft/core/parameters/context.py`（更新）
  - store を持たない worker 用 context（snapshot を与えて records を回収）
    - 例: `parameter_snapshot_context(snapshot, *, store_proxy, cc_snapshot)`
- `src/graft/core/pipeline.py`（更新）
  - `realize_scene(draw, ...)` を分割し、「layers から realize」できる関数を追加する
    - 例: `realize_layers(layers, defaults, store) -> tuple[list[RealizedLayer], list[FrameParamRecord]]`
- `src/graft/interactive/runtime/draw_window_system.py`（更新）
  - `n_worker` に応じて mp を有効化し、非同期結果を使う。
- `src/graft/api/run.py`（更新）
  - `run(..., *, n_worker: int = 0)` 追加。
- `tests/core/test_draw_multiprocess.py`（新規）
  - spawn で `DrawProcessPool` を起動し、正常系/例外系/label 収集を確認する。
- `README.md`（更新）
  - `n_worker` の説明と制約（spawn / picklable / `__main__` ガード / プロセス分離）を追記する。

## キュー通信プロトコル（案）

### Task

- `DrawTask(frame_id: int, t: float, param_snapshot: dict, cc_snapshot: dict | None)`

### Result

- `DrawResult(frame_id: int, ok: bool, payload: list[Layer] | str, records: list[FrameParamRecord], labels: list[tuple[str, str, str]])`
  - `ok=True` のとき `payload` は `list[Layer]`
  - `ok=False` のとき `payload` は `traceback.format_exc()`（文字列）

### 終了シグナル

- `None` を sentinel として投入（n_worker 個）し、ワーカーは受信したら break。

## 実装チェックリスト（最初の PR で達成したい最小単位）

- [ ] `src/graft/core/draw_mp.py` を追加（spawn 固定 / Queue / ワーカーループはトップレベル）
- [ ] ワーカー初期化で `graft.api.primitives` / `graft.api.effects` を import（組み込み op 登録）
- [ ] worker 用 `parameter_snapshot_context` を追加し、records/labels を回収できるようにする
- [ ] `pipeline` を `layers -> realize` に分割し、mp/非mp の両方で使う
- [ ] `api.run.run` に `n_worker` を追加し、interactive で有効化できるようにする
- [ ] `DrawWindowSystem` で non-blocking に結果を取り込み、最新結果を描画する
- [ ] close 時にワーカーを停止（例外でも必ず）
- [ ] `pytest` の最小テスト追加（spawn で動く / 例外伝播 / label 収集を確認）
- [ ] `README.md` に `n_worker` と制約（`__main__` ガード、top-level 定義、プロセス分離）を追記

## 事前に確認したいこと（あなたに質問）

1. `n_worker` のデフォルトは `0`（無効）で良い？
2. 追いつかない場合は「最新結果優先（古い結果は捨てる）」で良い？
3. 初期版は「ワーカーは draw+normalize だけ」に限定して良い？（draw+realize は次フェーズ）
4. mp モードでは `draw` はプロセス分離される前提（グローバル状態に依存しない）で進めて良い？
