# どこで: `src/grafix/api/run.py` / `src/grafix/interactive/runtime/draw_window_system.py` / `src/grafix/interactive/midi/factory.py` / `src/grafix/interactive/midi/midi_controller.py`。
# 何を: MIDI が接続できない（または起動時に存在しない）状態でも、前回保存した CC スナップショットを `cc_snapshot` として使い、`cc_key` が付いたパラメータの描画が変わらないようにする。
# なぜ: 現状は `cc_snapshot=None` になり resolver が CC 経路をスキップするため、CC で動かしていた値が GUI/base に戻って描画が変わる。

## ゴール / 非ゴール

- ゴール:
  - 描画を閉じた後、MIDI 未接続で再度起動しても **CC 割当済みパラメータの effective が維持**される（=描画が変わらない）。
  - コントローラーによって CC 数（16/20 等）や CC 番号体系が違っても、**別コントローラーのスナップショットを誤って適用しない**。
- 非ゴール:
  - MIDI が無い状態で MIDI learn を可能にする（学習は実デバイス前提で OK）。
  - 未観測 CC（スナップショットに存在しない CC 番号）に対する「ゼロ埋め」等の新仕様追加。

## 方針（採用案）

### 1) MIDI 無しでも `cc_snapshot` を渡す（Frozen CC）

- `DrawWindowSystem.draw_frame()` で
  - MIDI 接続あり: 従来通り `midi.snapshot()`（更新あり）
  - MIDI 接続なし: **起動時にロードした frozen `cc_snapshot`**（更新なし）
  を `SceneRunner.run(..., cc_snapshot=...)` に渡す。
- `midi_port_name=None`（ユーザーが明示的に MIDI 無効）では frozen を使わない。
  - 「意図して無効」と「繋ぎたいが繋がらない」を区別するため。

### 2) Frozen のスナップショットを “コントローラー単位” に分離する

問題: profile 名（スケッチ名）だけで保存すると、コントローラーを替えたときに別デバイスの CC を凍結してしまう。

対策:
- 保存/ロードの単位を `(script_stem, midi_mode, controller_id)` にする。
  - controller_id は **port_name（実ポート名）** を基本とする。
  - ファイル名は既存の `MidiController` の `profile_name` に埋め込む（最小変更）。
    - 例: `profile_name = f"{script_stem}__{midi_mode}__{port_name}"`
    - `default_cc_snapshot_path(profile_name=...)` の正規化が既にあるので安全。

### 3) `midi_port_name="auto"` のときの「最後に使った実ポート名」を記録する

問題: auto で「起動時にポートが 0 件」だと、どの port の snapshot を凍結すべきか分からない。

対策:
- auto で接続できたときに、`(script_stem, midi_mode) -> last_port_name` を JSON で保存する。
  - 保存先: `data/output/midi/<script_stem>__<midi_mode>__last_port.json`（案）
  - 内容: `{ "port_name": "...", "mode": "7bit" }` 程度で十分。
- auto で接続できないとき:
  - 上記ファイルがあれば last_port_name を読み、そこから frozen snapshot のパスを決定してロードする。
  - 無ければ frozen は使わない（現状維持。初回はどうしようもないため）。

## 変更箇所（案）

- `src/grafix/interactive/midi/factory.py`
  - `create_midi_controller()` で実ポート名が確定したら、`profile_name` を `"{script_stem}__{mode}__{port_name}"` にする。
  - auto 成功時に `last_port.json` を保存する（`mido` 依存無しのファイル I/O）。
  - auto 失敗時（ポート無し/ mido 無し）に使う `load_frozen_cc_snapshot()` を追加する。
- `src/grafix/api/run.py`
  - `midi_port_name is not None and midi_controller is None` の場合のみ `frozen_cc_snapshot` をロード。
  - `DrawWindowSystem(..., frozen_cc_snapshot=...)` のように渡す（引数追加）。
- `src/grafix/interactive/runtime/draw_window_system.py`
  - `__init__` に `frozen_cc_snapshot: dict[int, float] | None` を追加して保持。
  - `draw_frame()` で `cc_snapshot` を `midi.snapshot()` / `frozen_cc_snapshot` から選ぶ。

## テスト方針（案）

- `tests/interactive/midi/test_midi_factory.py`
  - auto 成功時に `last_port.json` が書かれる（tmp_path を使う or save_dir を注入できるようにする）。
  - auto 失敗時に `last_port.json` があれば frozen snapshot をロードできる。
  - 明示 port 指定時は port 名から一意に frozen snapshot をロードできる。
- 既存の `tests/interactive/midi/test_midi_persistence.py` は「保存→復元」の経路としてそのまま有効。

## 互換性 / 既存データ

- snapshot ファイル名が `script_stem.json` から `script_stem__mode__port.json` へ変わるため、既存の snapshot は自動では読まれない（破壊的変更）。
- 既存 snapshot を引き継ぎたい場合は、手動でリネーム/コピーする（実装での互換ローダは作らない）。

## チェックリスト

- [ ] 事前確認: 「MIDI なしでも frozen `cc_snapshot` を渡す」「スナップショットは (script, mode, port) で分離」「auto は last_port 記録で解決」で進めてよい
- [ ] `factory.create_midi_controller()` で `profile_name` を `"{script}__{mode}__{port}"` に変更
- [ ] auto 成功時に `last_port.json` を保存
- [ ] auto 失敗時/明示ポート失敗時に frozen snapshot をロードする関数を追加
- [ ] `run()` から `DrawWindowSystem` へ `frozen_cc_snapshot` を渡す
- [ ] `DrawWindowSystem.draw_frame()` で `cc_snapshot` を live/frozen から選ぶ
- [ ] テスト追加: factory の frozen ロードと last_port
- [ ] 手動確認:
  - MIDI 接続ありで CC を動かす → 終了（保存）
  - MIDI 切断 → 再起動 → 描画が変わらない

