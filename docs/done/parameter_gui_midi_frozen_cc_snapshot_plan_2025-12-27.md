# どこで: `src/grafix/api/run.py` / `src/grafix/interactive/runtime/draw_window_system.py` / `src/grafix/interactive/midi/midi_controller.py`。
# 何を: MIDI が接続できない（または起動時に存在しない）状態でも、前回保存した CC スナップショットを `cc_snapshot` として使い、`cc_key` が付いたパラメータの描画が変わらないようにする。
# なぜ: 現状は `cc_snapshot=None` になり resolver が CC 経路をスキップするため、CC で動かしていた値が GUI/base に戻って描画が変わる。

## ゴール / 非ゴール

- ゴール:
  - 描画を閉じた後、MIDI 未接続で再度起動しても **CC 割当済みパラメータの effective が維持**される（=描画が変わらない）。
  - CC スナップショットの JSON 命名ルールは現行のまま維持する（`data/output/midi/<script_stem>.json`）。
- 非ゴール:
  - MIDI が無い状態で MIDI learn を可能にする（学習は実デバイス前提で OK）。
  - 未観測 CC（スナップショットに存在しない CC 番号）に対する「ゼロ埋め」等の新仕様追加。
  - コントローラーごとのスナップショット分離（別コントローラーに替えたときの誤凍結は許容する）。

## 方針（採用案）

### 1) MIDI 無しでも `cc_snapshot` を渡す（Frozen CC）

- `DrawWindowSystem.draw_frame()` で
  - MIDI 接続あり: 従来通り `midi.snapshot()`（更新あり）
  - MIDI 接続なし: **起動時にロードした frozen `cc_snapshot`**（更新なし）
  を `SceneRunner.run(..., cc_snapshot=...)` に渡す。
- frozen のロード元は、現行の永続化ファイルと同一:
  - `data/output/midi/<script_stem>.json`
  - 実装上は `default_cc_snapshot_path(profile_name=script_stem, save_dir=None)` + `load_cc_snapshot(...)` を使う。
- `midi_port_name=None`（ユーザーが明示的に MIDI 無効）では frozen を使わない。
  - 「意図して無効」と「繋ぎたいが繋がらない」を区別するため。

### 2) コントローラー識別は行わない（命名ルール維持）

- `script_stem` 単位で 1 枚の snapshot を共有する（現行のまま）。
- トレードオフ:
  - 別コントローラーへ乗り換えた場合、古い CC 値が凍結として残り続ける可能性がある。
  - その場合は手動で `data/output/midi/<script_stem>.json` を削除/初期化する運用で対処する（互換ラッパーは作らない）。

## 変更箇所（案）

- `src/grafix/interactive/midi/midi_controller.py`
  - `maybe_load_frozen_cc_snapshot()` を追加し、MIDI 未接続時に frozen snapshot をロードできるようにする（命名ルールは現行維持）。
- `src/grafix/api/run.py`
  - `midi_port_name is not None and midi_controller is None` の場合のみ `frozen_cc_snapshot` をロード。
  - `DrawWindowSystem(..., frozen_cc_snapshot=...)` のように渡す（引数追加）。
- `src/grafix/interactive/runtime/draw_window_system.py`
  - `__init__` に `frozen_cc_snapshot: dict[int, float] | None` を追加して保持。
  - `draw_frame()` で `cc_snapshot` を `midi.snapshot()` / `frozen_cc_snapshot` から選ぶ。

## テスト方針（案）

- 既存の `tests/interactive/midi/test_midi_persistence.py` は「保存→復元（JSON→cc_snapshot）」の経路としてそのまま有効。
- 追加（最小）:
  - `maybe_load_frozen_cc_snapshot()` が「MIDI 無効/接続あり/接続なし」で期待通りの値を返すことをテストする。

## 互換性 / 既存データ

- 互換性は維持する（既存の `data/output/midi/<script_stem>.json` をそのまま frozen に流用する）。

## チェックリスト

- [x] 事前確認: 「MIDI なしでも frozen `cc_snapshot` を渡す」「命名ルールは現行維持（コントローラー分離しない）」で進めてよい
- [x] `run()` から `DrawWindowSystem` へ `frozen_cc_snapshot` を渡す
- [x] `DrawWindowSystem.draw_frame()` で `cc_snapshot` を live/frozen から選ぶ
- [x] テスト追加: `maybe_load_frozen_cc_snapshot()` を追加
- [ ] 手動確認:
  - MIDI 接続ありで CC を動かす → 終了（保存）
  - MIDI 切断 → 再起動 → 描画が変わらない
