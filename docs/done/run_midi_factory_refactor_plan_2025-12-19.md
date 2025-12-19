# run.py から MIDI controller 生成を分離する実装チェックリスト（2025-12-19）

目的: `src/grafix/api/run.py` を肥大化させないため、MIDI ポート選択/auto 接続/mido 有無ハンドリング等のロジックを `grafix.interactive.midi` 側へ移す。

前提:

- 公開 API `run(..., midi_port_name=..., midi_mode=...)` のシグネチャは変えない。
- ユーザーの import は引き続き `from grafix import E, G, L, cc, run` のみ。
- 挙動は現状維持（auto の条件、mido 不在時の扱い、例外メッセージ）を基本とし、差分が出る場合はこのファイルに追記して確認する。

## 1) 新しい置き場所（モジュール設計）

- [x] 新規モジュールを追加: `src/grafix/interactive/midi/factory.py`
  - ここに「controller を作る責務」を集約する（run.py から呼ぶだけにする）。
- [x] `auto` 分岐（選択/接続ロジック）を factory 側へ移す（run.py から消す）
  - run.py 側は `"auto"` を「値として受け取り渡す」だけにする（解釈しない）。

## 2) API（factory 関数）を決める

狙い: run.py 側が持つ情報を最小化しつつ、永続化（スケッチ単位 `data/output/midi/{script_stem}.json`）も崩さない。

- [x] factory の入口関数を追加（案）
  - [ ] A 案（run.py を最小化）: `create_midi_controller_for_draw(draw, *, port_name, mode) -> MidiController | None`
    - `default_param_store_path(draw).stem` を内部で使い `profile_name` を決める
    - `MidiController(..., profile_name=...)` に渡し、保存先は controller の既定ロジックに任せる
  - [x] B 案（依存方向をさらに単純化）: `create_midi_controller(*, port_name, mode, profile_name) -> MidiController | None`
    - run.py 側が `script_stem` を計算して渡す（run.py の行数は少し増える）

※ A 案を採用するなら `factory.py` は `grafix.core.parameters.persistence.default_param_store_path` へ依存する。

## 3) 現行挙動（要件）を factory に移植

- [x] `port_name is None` の場合は `None`（MIDI 無効）
- [x] `port_name == "auto"` の場合
  - [x] mido import できない → `None`（MIDI 無効）
  - [x] 入力ポートが 0 → `None`
  - [x] 入力ポートが 1+ → 先頭へ接続して `MidiController` を返す
- [x] `port_name` が明示文字列の場合
  - [x] mido import できない → `RuntimeError`（ユーザーの意図が強いのでエラー）
  - [x] それ以外は `MidiController(port_name, ...)`（既存の validate に任せる）

## 4) run.py 側の簡素化（配線だけにする）

- [x] `src/grafix/api/run.py` から `_try_create_midi_controller` を削除する
- [x] `run()` 内の MIDI 初期化を、factory 呼び出し 1 行程度に置き換える
  - 例: `midi_controller = create_midi_controller_for_draw(draw, port_name=midi_port_name, mode=midi_mode)`
- [x] run.py から不要になった import（`Path` 等）を削除する

## 5) テスト

方針: mido に依存せずにテストできるところだけを担保し、mido 依存は「軽い smoke」程度に留める。

- [x] `tests/interactive/midi/test_midi_factory.py`（新規）を追加
  - [x] `port_name=None` → `None` を返す
  - [x] `port_name="auto"` で mido import 失敗をモック → `None` を返す
  - [x] 明示ポート指定で mido import 失敗をモック → `RuntimeError`
  - [x] `get_input_names()` をモックして「先頭ポートを使う」ことを確認

## 6) 受け入れ条件（Done の定義）

- [x] `src/grafix/api/run.py` に MIDI の詳細ロジック（auto/mido 無しの分岐）が存在しない
- [x] 既存の `run(..., midi_port_name=..., midi_mode=...)` の挙動が維持される
- [x] `PYTHONPATH=src pytest -q` が通る
