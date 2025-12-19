# MIDI CC 永続化（前回状態の復元）実装チェックリスト（2025-12-19）

目的: `from grafix import cc` で参照できる CC 値を、アプリ終了時に `data/output/midi/main.json` のようなファイルへ保存し、次回起動時に復元する。

前提・注意:

- ここでの「復元」は **アプリ側の初期値**を前回値にすること。ノブの物理位置は戻らない。
- 次回起動時、実機ノブ位置が保存値と違う場合、最初にその CC が送られた瞬間に値がジャンプする（仕様上避けにくい）。
- mp-draw（draw が別プロセス）でも成立させるため、CC は「フレーム内 `cc_snapshot`」として worker に渡す方針を維持する。
- `data/` は `.gitignore` 対象なので、ローカル状態がコミットされない。

## 0) 事前に決める（あなたの確認が必要）

- [x] 保存ファイルの粒度（どの単位でファイルを分けるか）
  - A: **スケッチ（draw 定義元）単位**: `data/output/midi/{script_stem}.json`（推奨。`param_store` と揃う）；こちらで
  - B: スケッチ + ポート名単位: `data/output/midi/{script_stem}_{port}.json`（複数デバイスに強いがファイル名が長い）
- [x] 保存する内容
  - A: **CC 値だけ**（`dict[int,float]`、0.0–1.0）を保存（推奨）；こちらで
  - B: CC 値 + メタ情報（port_name, mode, timestamp）
- [x] 保存タイミング
  - A: **終了時に 1 回だけ保存**（推奨。I/O 最小）；こちらで
  - B: 値が変化したフレームで間引き保存（クラッシュに強いが I/O 増）
- [x] 復元の優先順位
  - A: **保存値 →（受信した CC で上書き）**（推奨）；こちらで
  - B: 受信値のみ（保存値は UI 表示用途だけ）

## 1) 永続化パスの決定（param_store と揃える）

- [x] `script_stem` の算出は `default_param_store_path(draw).stem` を流用する
  - 例: `sketch/main.py` → `main`
- [x] MIDI 保存先ディレクトリを新設: `data/output/midi/`
- [x] 0. の決定に従い `Path("data/output/midi") / f"{script_stem}.json"`（または port を含める）を生成する

## 2) 実装方針（どこで load/save を行うか）

狙い: ユーザーに追加 import を要求しない（`from grafix import E, G, L, cc, run` のみ）。

- [x] `MidiController` の永続化 API をどう扱うか決める
  - A: `MidiController` に「保存先パスを直接指定」できる引数を追加する（推奨: 単純）
    - 例: `MidiController(..., persistence_path=Path(...))`
  - B: `grafix.api.run` 側で `load/save` を行い、controller には dict を注入する（責務が散るので非推奨）
- [x] load の流れ（起動時）
  - `run()` が MIDI を有効化した場合、controller 初期化直後に保存ファイルを読み、controller 内の `cc` 初期値に反映
- [x] save の流れ（終了時）
  - A: `DrawWindowSystem.close()` で `midi.save()` → `midi.close()`（所有者が閉じるので自然）
  - B: `run()` の finally で `midi.save()`（param_store と並列に扱える）
  - ※ 二重保存は避ける（責務の置き場所を 1 箇所に固定）

## 3) mp-draw との整合

- [x] メインプロセス側は毎フレーム `cc_snapshot` を作る
  - 保存から復元した初期値が入っていれば、最初のフレームから `cc_snapshot` に乗る
- [x] worker には `cc_snapshot` を同梱して渡す
  - `parameter_context_from_snapshot(..., cc_snapshot=...)` で worker 側 `CcView` が読める

## 4) テスト（mido 無しで確認）

- [x] `tests/interactive/midi/test_midi_persistence.py`（仮）を追加
  - [x] `tmp_path` へ保存 → ロードの往復で辞書が一致する
  - [x] 保存値がある状態で `cc_snapshot` が供給されると、`cc[n]` が初回からその値になる
  - [x] 未設定キーは `cc[n] == 0.0`

## 5) 受け入れ条件（Done の定義）

- [x] `run(draw)`（midi_port_name="auto"）で、MIDI が接続できる環境なら `data/output/midi/{script_stem}.json` が作られる
- [x] 次回起動時、ノブ未操作でも `cc[...]` が前回値になる
- [x] `PYTHONPATH=src pytest -q` が通る
