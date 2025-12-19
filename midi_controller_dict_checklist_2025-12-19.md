# MIDI コントローラ（dict 版）実装チェックリスト（2025-12-19）

目的: `src/grafix/interactive/midi/midi_controller_old.py` を参考に、`DualKeyDict` 依存を廃して「ただの辞書（built-in `dict`）」で CC 値を扱う `midi_controller` を新規実装する。

注意:

- 互換ラッパー/シムは作らない（旧 API を無理に残さない）。
- 旧モジュール（`midi_controller_old.py`）は参照用。削除/移動するなら別途確認する（Ask-first）。

## 0) 事前に決める（あなたの確認が必要）

- [x] `cc_snapshot` のキー形:
  - A: `dict[int, float]` のみ（推奨。`ParamState.cc_key: int` と一致）；これで
  - B: `dict[int | str, float]`（数値 CC と論理名を混在。同期が面倒）
  - C: `dict[int, float]` + `name_to_cc: dict[str, int]`（名前参照は補助）
- [x] `cc_map`（名前 ↔ CC 番号）の入力形式:
  - A: `dict[str, int]`（推奨）
  - B: `dict[int, str]`
  - C: 使わない（名前機能を持たない）；これで
- [x] 永続化（JSON）の粒度:
  - A: `port_name` 単位（例: `data/output/midi_cc/<port>.json`）
  - B: `port_name` + `profile_name`（= スクリプト名相当）単位（旧仕様寄せ）；こちらで
- [x] 既定の保存先ディレクトリ:
  - A: `<repo>/data/output/midi_cc`（推奨。既存の `data/output` と整合）；これで
  - B: `<repo>/data/cc`（旧仕様寄せ）
  - C: 常に呼び出し側が `save_dir` を渡す（デフォルト無し）
- [x] Intech Grid 向けノブ同期（旧 `sync_grid_knob_values`）の扱い:
  - A: 削除（推奨。デバイス依存の副作用を無くす）；これで
  - B: `sync_*()` として明示呼び出しのみ残す
  - C: `auto_sync=True` のときだけ `__init__` で実行（既定 False）
- [x] モジュールの形:
  - A: `MidiController` クラス（入力ポートを持つ）；こちらで
  - B: 純粋関数 + `dict` 状態（テスト容易だが呼び出し側が状態を持つ）

## 1) 設計（DualKeyDict なしの最小構成に落とす）

- [ ] 「辞書」をどこまで責務にするか決める
  - 値は **常に 0.0–1.0** に正規化して `dict[int, float]` に格納（core 側の `cc_snapshot` に直結）
  - 14bit の MSB 待ち状態は `msb_by_cc: dict[int, int]`（0–31 → 0–127）で保持
- [ ] MIDI メッセージの型（mido 依存を外へ漏らさない）
  - `Protocol` or 「必要属性だけ読む」（`type`, `control`, `value`, `note`, `velocity`）
  - mido は利用箇所でローカル import（未インストールでも import 可能な設計）
- [ ] 14bit CC の正規化を単純化
  - 旧: 0–16383 → 0–127 → /127
  - 新: **0–16383 → /16383**（同等でより直感的）
- [ ] 例外方針を決める
  - 不正ポートは `InvalidPortError`
  - JSON 破損は「初期化して続行」か「例外」かを決める（シンプル優先）

## 2) 新規実装（ファイル追加）

- [ ] `src/grafix/interactive/midi/__init__.py` を追加してパッケージ化
- [ ] `src/grafix/interactive/midi/midi_controller.py` を新規作成
  - [ ] ファイル先頭の 3 行ヘッダ（どこで/何を/なぜ）
  - [ ] 公開 API に NumPy スタイル docstring + 型ヒント
  - [ ] `open_input(port_name)` / `iter_pending()` / `close()`（必要なら）
  - [ ] `update(msg)`（CC のみ更新、必要なら note も返す）
  - [ ] `snapshot()`（`dict[int, float]` を返す。必要ならコピー）
- [ ] 永続化（必要なら）
  - [ ] `load()` / `save()` を `save_dir` と `snapshot_id`（0) の決定）で分離
  - [ ] JSON のキーは文字列になる点（`"64": 0.5`）を吸収する

## 3) テスト（mido 無しで回す）

- [ ] `tests/interactive/midi/test_midi_controller.py` を追加
  - [ ] 7bit: `value=0/127` が `0.0/1.0` になる
  - [ ] 14bit: MSB→LSB の順で更新され、片方だけでは更新されない
  - [ ] 14bit: 端点（0, 16383）で `0.0/1.0`
  - [ ] 永続化: `tmp_path` で save/load 往復（採用する場合のみ）

## 4) interactive への接続（必要なら）

- [ ] `draw_window_system` 等で `parameter_context(..., cc_snapshot=controller.snapshot())` を渡す
- [ ] CC 入力のポーリング頻度（毎フレーム/間引き）を決める

## 5) 仕上げ

- [ ] `PYTHONPATH=src pytest -q tests/interactive -k midi`
- [ ] `ruff check src/grafix/interactive/midi`
- [ ] `mypy src/grafix`（必要なら対象限定でも可）
- [ ] 旧ファイルの扱いを決める（残す/移動/削除。削除/移動は Ask-first）
