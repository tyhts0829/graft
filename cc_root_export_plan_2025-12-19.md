# `from grafix import cc` 公開（CC 辞書）実装チェックリスト（2025-12-19）

目的: ユーザーコードから `from grafix import cc` で「最新の CC 値スナップショット辞書」にアクセスできるようにする。

前提:

- `cc` は **`cc[0]` のように添字アクセスできる**（キーは CC 番号、値は 0.0–1.0 正規化）。
- 既に `src/grafix/interactive/midi/midi_controller.py` が `dict[int, float]` を生成できる。
- 互換ラッパー/シムは作らない。
- `draw(t)` は multiprocessing worker で実行されうる（= プロセス間でグローバル変数は共有されない）。

## 0) 事前に決める（あなたの確認が必要）

- [ ] マルチプロセス時の同期方式（重要）
  - A: **メインで作った `cc_snapshot` を task に同梱して worker に渡す**（推奨。単純・安全）
  - B: `multiprocessing.Manager().dict()` 等で共有する（遅い/複雑なので非推奨）
  - C: worker で MIDI ポートを読む（ポート競合しやすいので非推奨）
- [ ] `cc` の意味
  - A: 「プロセス内の最新値」を保持する共有辞書（メイン/worker でそれぞれ更新が必要）
  - B: **「フレーム内で固定された snapshot」へのビュー**（推奨。`parameter_context` と一致）
- [ ] `cc` の実体
  - A: `Mapping` 互換のビュー（`current_cc_snapshot()` を読む。推奨。mp でも自然に動く）
  - B: 実 `dict[int, float]`（各プロセスで `clear()/update()` 更新する前提）
- [ ] `cc[cc_number]` が未設定キーのとき
  - A: **0.0 を返す**（推奨。`cc[0]` を気軽に使える）
  - B: `KeyError`（厳密だが扱いにくい）
- [ ] 参照の安定性
  - A: **同一 dict オブジェクトを維持**し、更新は `clear()/update()` で行う（推奨）
  - B: `cc = {...}` のように再代入して更新（`from grafix import cc` の参照が古くなるので非推奨）
- [ ] 追加 API（任意）
  - A: `cc` のみ公開（最小）
  - B: `cc_snapshot()` も公開（`dict(cc)` を返す。parameter_context 用に安全）

## 1) 公開 API の追加（root から import 可能にする）

- [ ] `src/grafix/cc.py` を新規作成
  - [ ] 先頭 3 行ヘッダ（どこで/何を/なぜ）
  - [ ] （0) で「実体 A: ビュー」を選ぶ場合: `cc = CcView()` を定義（`cc[0]` が動く）
  - [ ] （0) で「実体 B: dict」を選ぶ場合: `cc: dict[int, float] = {}` を定義
  - [ ] （0) で「未設定キーは 0.0」を選ぶ場合: `__getitem__` / `get` の既定値を 0.0 に寄せる
  - [ ] （任意）`def cc_snapshot() -> dict[int, float]` を定義（`dict(...)` のコピーを返す）
- [ ] `src/grafix/__init__.py` を更新
  - [ ] `from .cc import cc`（必要なら `cc_snapshot` も）を追加
  - [ ] `__all__` に `"cc"`（必要なら `"cc_snapshot"`）を追加

## 2) cc_snapshot を worker へ届ける（mp-draw 対応）

狙い: draw が worker プロセスで動いても、`from grafix import cc` が各フレームの CC 値を参照できるようにする。

- [ ] `src/grafix/interactive/runtime/mp_draw.py` を更新
  - [ ] `_DrawTask` に `cc_snapshot: dict[int, float] | None` を追加
  - [ ] worker 側で `parameter_context_from_snapshot(task.snapshot, cc_snapshot=task.cc_snapshot)` を使う
    - これで `current_cc_snapshot()` が worker 内でも有効になり、cc ビュー方式が成立する
- [ ] `src/grafix/interactive/runtime/draw_window_system.py` を更新
  - [ ] メインプロセスで MIDI をポーリングして `cc_snapshot` を作る（例: `MidiController.snapshot()`）
  - [ ] `parameter_context(self._store, cc_snapshot=cc_snapshot)` に渡す（非 mp 実行でも cc が使える）
  - [ ] `mp_draw.submit(..., snapshot=current_param_snapshot(), cc_snapshot=cc_snapshot)` で worker に同梱

## 3) MIDI 入力（任意だが現実的には必要）

狙い: `cc` が常に空/0.0 ではなく、実デバイス入力で変化するようにする。

- [ ] MIDI 入力の導線を決める
  - A: `grafix.api.run(..., midi_port_name=..., midi_mode=...)` のように API 引数で受け取る
  - B: `config.yaml` / 環境変数で指定する（実行環境依存が増える）
- [ ] `DrawWindowSystem` が `MidiController` を所有し、毎フレーム `poll_pending()` する
  - 例: `updated = midi.poll_pending(max_messages=...)`（過負荷時の上限を検討）
  - `updated` があったフレームだけ `save()` するかどうかも決める

## 4) テスト追加（import 経路 + mp 用の成立確認）

- [ ] `tests/api/test_root_cc_export.py`（仮）を追加
  - [ ] `import grafix` 後に `hasattr(grafix, "cc")` を確認
  - [ ] `from grafix import cc` で `cc[0]` が評価できること（未設定は 0.0 or KeyError は 0) の決定に従う）
- [ ] context 連動テスト（推奨）
  - [ ] `parameter_context_from_snapshot(..., cc_snapshot={0: 0.5})` の内側で `cc[0] == 0.5` になること
- [ ] mp-draw 連動テスト（必要なら）
  - [ ] `_draw_worker_main` 相当を直接呼ばず、`MpDraw` 経由で `cc_snapshot` が worker に届くことを確認する
    - ※ spawn + Queue が絡むので、重ければ integration マーカーに逃がす

## 5) 運用（使い方）を最小で明文化（任意）

- [ ] README か docs への追記（最小）
  - [ ] 例: `from grafix import cc` / `v = cc[74]` のような利用例
  - [ ] CC の値は `parameter_context` 内（= draw 内）で読むとフレーム単位で安定することを明記する

## 6) 受け入れ条件（Done の定義）

- [ ] `PYTHONPATH=src python -c "from grafix import cc; _ = cc[0]"` が通る
- [ ] mp-draw でも `cc` が参照できる（unit or integration test で確認）
- [ ] MIDI 入力の更新で `cc` の値が変わる（少なくとも unit test で確認）
- [ ] `PYTHONPATH=src pytest -q` の関連テストが通る
- [ ] `ruff check ...` / `mypy ...` が通る（環境にあれば）
