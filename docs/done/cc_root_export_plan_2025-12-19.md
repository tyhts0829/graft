# `from grafix import cc` 公開（CC 辞書）実装チェックリスト（2025-12-19）

目的: ユーザーコードから `from grafix import cc` で「最新の CC 値スナップショット辞書」にアクセスできるようにする。

前提:

- `cc` は **`cc[0]` のように添字アクセスできる**（キーは CC 番号、値は 0.0–1.0 正規化）。
- 既に `src/grafix/interactive/midi/midi_controller.py` が `dict[int, float]` を生成できる。
- 互換ラッパー/シムは作らない。
- `draw(t)` は multiprocessing worker で実行されうる（= プロセス間でグローバル変数は共有されない）。

## 0) 事前に決める（あなたの確認が必要）

- [x] マルチプロセス時の同期方式（重要）
  - A: **メインで作った `cc_snapshot` を task に同梱して worker に渡す**（推奨。単純・安全）；これで
  - B: `multiprocessing.Manager().dict()` 等で共有する（遅い/複雑なので非推奨）
  - C: worker で MIDI ポートを読む（ポート競合しやすいので非推奨）
- [x] `cc` の意味
  - A: 「プロセス内の最新値」を保持する共有辞書（メイン/worker でそれぞれ更新が必要）
  - B: **「フレーム内で固定された snapshot」へのビュー**（推奨。`parameter_context` と一致）；これで
- [x] `cc` の実体
  - A: `Mapping` 互換のビュー（`current_cc_snapshot()` を読む。推奨。mp でも自然に動く）
  - B: 実 `dict[int, float]`（各プロセスで `clear()/update()` 更新する前提）
- [x] `cc[cc_number]` が未設定キーのとき
  - A: **0.0 を返す**（推奨。`cc[0]` を気軽に使える）；これで
  - B: `KeyError`（厳密だが扱いにくい）
- [x] 参照の安定性
  - 採用案では `cc` は dict ではなく **読み取り専用ビュー（オブジェクト）**のため、参照は常に安定（これで）
- [ ] 追加 API（任意）
  - A: `cc` のみ公開（最小）
  - B: `cc_snapshot()` も公開（`dict(cc)` を返す。parameter_context 用に安全）

## 1) 公開 API の追加（root から import 可能にする）

- [x] `src/grafix/cc.py` を新規作成
  - [x] 先頭 3 行ヘッダ（どこで/何を/なぜ）
  - [x] （採用）`cc = CcView()` を定義（`cc[0]` が動く / 未設定は 0.0）
    - `CcView.__getitem__` は `current_cc_snapshot()` が `None` の場合も 0.0 を返す
    - `CcView` は読み取り専用（ユーザーが `cc[...] = ...` しない前提を型で担保）
  - [ ] （0) で「実体 B: dict」を選ぶ場合: `cc: dict[int, float] = {}` を定義
  - [ ] （0) で「未設定キーは 0.0」を選ぶ場合: `__getitem__` / `get` の既定値を 0.0 に寄せる
  - [ ] （任意）`def cc_snapshot() -> dict[int, float]` を定義（`dict(...)` のコピーを返す）
- [x] `src/grafix/__init__.py` を更新
  - [x] `from .cc import cc`（必要なら `cc_snapshot` も）を追加
  - [x] `__all__` に `"cc"`（必要なら `"cc_snapshot"`）を追加

## 2) cc_snapshot を worker へ届ける（mp-draw 対応）

狙い: draw が worker プロセスで動いても、`from grafix import cc` が各フレームの CC 値を参照できるようにする。

- [x] `src/grafix/interactive/runtime/mp_draw.py` を更新
  - [x] `_DrawTask` に `cc_snapshot: dict[int, float] | None` を追加
  - [x] worker 側で `parameter_context_from_snapshot(task.snapshot, cc_snapshot=task.cc_snapshot)` を使う
    - これで `current_cc_snapshot()` が worker 内でも有効になり、cc ビュー方式が成立する
- [x] `src/grafix/interactive/runtime/draw_window_system.py` を更新
  - [x] メインプロセスで MIDI をポーリングして `cc_snapshot` を作る（例: `MidiController.snapshot()`）
  - [x] `parameter_context(self._store, cc_snapshot=cc_snapshot)` に渡す（非 mp 実行でも cc が使える）
  - [x] `mp_draw.submit(..., snapshot=current_param_snapshot(), cc_snapshot=cc_snapshot)` で worker に同梱
  - [ ] `cc_snapshot` は「未受信 CC を含めない疎な dict」でよい（`cc[1]` の既定 0.0 はビュー側で担保する）

## 3) MIDI 入力（任意だが現実的には必要）

狙い: `cc` が常に空/0.0 ではなく、実デバイス入力で変化するようにする。

- [x] MIDI 入力の導線を決める
  - A: `grafix.api.run(..., midi_port_name="auto")` / `grafix.api.run(..., midi_port_name="TX-6 Bluetooth")` を使う（実装済み；これで）
  - B: `config.yaml` / 環境変数で指定する（実行環境依存が増える）
- [x] `DrawWindowSystem` が `MidiController` を所有し、毎フレーム `poll_pending()` する
  - 例: `midi.poll_pending()` → `midi.snapshot()` を `cc_snapshot` として使う
  - `save()` の頻度は必要になったら決める

## 4) テスト追加（import 経路 + mp 用の成立確認）

- [x] `tests/api/test_root_cc_export.py` を追加
  - [x] `from grafix import cc` で `cc[0]` が評価できること（未設定は 0.0）
- [x] context 連動テスト（推奨）
  - [x] `parameter_context_from_snapshot(..., cc_snapshot={0: 0.25})` の内側で `cc[0] == 0.25` になること
- [ ] mp-draw 連動テスト（必要なら）
  - [ ] `_draw_worker_main` 相当を直接呼ばず、`MpDraw` 経由で `cc_snapshot` が worker に届くことを確認する
    - ※ spawn + Queue が絡むので、重ければ integration マーカーに逃がす

## 5) 運用（使い方）を最小で明文化（任意）

- [ ] README か docs への追記（最小）
  - [ ] 例: `from grafix import cc` / `v = cc[74]` のような利用例
  - [ ] CC の値は `parameter_context` 内（= draw 内）で読むとフレーム単位で安定することを明記する

## 6) 受け入れ条件（Done の定義）

- [x] `PYTHONPATH=src python -c "from grafix import cc; assert cc[0] == 0.0"` が通る
- [ ] mp-draw でも `cc` が参照できる（unit or integration test で確認）
- [ ] MIDI 入力の更新で `cc` の値が変わる（少なくとも unit test で確認）
- [x] `PYTHONPATH=src pytest -q` の関連テストが通る
- [ ] `ruff check ...` / `mypy ...` が通る（環境にあれば）
