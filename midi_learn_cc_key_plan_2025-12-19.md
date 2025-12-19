# Parameter GUI: cc_key を MIDI Learn ボタンに置き換える実装チェックリスト（2025-12-19）

目的: Parameter GUI の `cc_key` 入力欄を廃止し、「Learn ボタン → 次に動かしたノブの CC 番号を割り当てる」方式に変更する。

想定 UX:

- `cc` 列のボタンを押すと、状態に応じて次のいずれかになる:
  - 未割当: **Learn モード**へ入る（そのボタンが “listening...” になる）。
  - Learn 中: そのボタンを押すと Learn モード解除（キャンセル）。
  - 割当済: そのボタンを押すと割当クリア（`cc_key=None`）。
- Learn 中に MIDI コントローラーの任意ノブを動かして CC が来たら、その **CC 番号をそのパラメータへ割り当て**る（`cc_key` を更新）。
- 割り当てが完了したら Learn モードは自動解除する（1 回で終了）。
- Learn は同時に 1 件のみ（新しく押したら前の Learn はキャンセル）。

前提:

- MIDI 入力は `run(..., midi_port_name=...)` が生成した 1 つの `MidiController` のみを対象とする。
- MIDI メッセージの polling は draw 側が行い、GUI 側は「最後に来た CC」を参照するだけにする（GUI で `poll_pending()` しない）。
- `cc_key` の保存/復元は既存の永続化（`data/output/midi/{script}.json`）に従う。

## 0) 事前に決める（確定）

- [x] Learn 対象（vec3/rgb の扱い）
  - A: **3 成分それぞれに Learn ボタン**（推奨: 明確）；こちらで
    - vec3: `X/Y/Z`、rgb: `R/G/B`
  - B: 1 つの Learn ボタンで「未割当の成分に順に割り当て」（簡単だが挙動が分かりにくい）
- [x] 解除/クリア操作
  - A: Learn ボタン再押下でキャンセル（learn 中に同じボタンを押したら learn 解除）。Clear ボタンは増やさず、**割当済の Learn ボタン押下でクリア**（`cc_key=None`）。割当済の cc_key 番号は **ボタン上に表示**し、未割当は **ボタン上に文字を表示しない**。
  - B: 右クリック/Modifier（例: Alt+Click）でクリア（UI はすっきりするが分かりにくい）

## 1) MIDI 側: 「最後に来た CC」を GUI から参照できるようにする

- [ ] `src/grafix/interactive/midi/midi_controller.py` に **最後の CC イベント**を記録する仕組みを追加
  - [ ] `cc_change_seq: int`（単調増加）を持つ
  - [ ] `last_cc_change: tuple[int, int] | None`（`(seq, cc_number)`）を持つ
  - [ ] 7bit: `update_cc(control=value)` で `cc_change_seq += 1`、`last_cc_change = (seq, control)`
  - [ ] 14bit: LSB を処理して `msb_cc` に値を確定したタイミングで `last_cc_change = (seq, msb_cc)`
- [ ] 既存の `poll_pending()` / `snapshot()` の責務は維持（追加は「最後の CC を覚える」だけ）

## 2) GUI ランタイム: MIDI controller を GUI に渡す（参照のみ）

- [ ] `src/grafix/interactive/runtime/parameter_gui_system.py` を拡張
  - [ ] `ParameterGUIWindowSystem(store, midi_controller: MidiController | None)` を受け取れるようにする
- [ ] `src/grafix/interactive/parameter_gui/gui.py` の `ParameterGUI` も同様に `midi_controller` を受け取る
  - [ ] GUI 側では **poll しない**（draw 側が更新した `last_cc_change` を読むだけ）
- [ ] `src/grafix/api/run.py` は配線のみ
  - [ ] `ParameterGUIWindowSystem(store=..., midi_controller=midi_controller)` を渡す

## 3) Learn 状態（どの行が listening か）を保持する

- [ ] `ParameterGUI`（インスタンス状態）に Learn 状態を追加
  - [ ] `active_target: ParameterKey | None`
  - [ ] `active_component: int | None`（vec3/rgb 用。A 案なら 0/1/2）
  - [ ] `last_seen_cc_seq: int`（同じ CC を多重適用しないため）
- [ ] Learn の状態遷移
  - [ ] ボタン押下: 状態に応じて処理する
    - [ ] learn 中の同一ボタン → learn 解除（キャンセル）
    - [ ] 割当済のボタン → その割当をクリア（scalar は `None` / vec3-rgb は該当成分だけ `None`。全成分 `None` なら `cc_key=None`）
    - [ ] 未割当のボタン → learn 開始（既に別ターゲットがあればキャンセルして切替）
  - [ ] Learn 中に `last_cc_change.seq` が進んだら、対応 row の `cc_key` を更新して Learn 終了

## 4) UI: cc_key 入力欄を Learn ボタンに置き換える

- [ ] `src/grafix/interactive/parameter_gui/table.py` の `cc` 列描画を変更
  - [ ] `input_int / input_int3` を撤去
  - [ ] Learn ボタン（現在割当の表示もボタン上に寄せる）
    - [ ] 非 Learn:
      - [ ] 未割当: ボタン上の文字は空（表示しない）
      - [ ] 割当済: ボタン上に cc_key 番号を表示（例: `12`）
    - [ ] Learn 中（対象行）: `Listening...`（色/テキストで分かるように）
  - [ ] `override` チェックボックスは現状維持（セル内配置だけ調整）
- [ ] vec3/rgb の場合（0 章の決定に従う）
  - [ ] A 案: 3 つの小ボタン（`X/Y/Z` or `R/G/B`）を並べる
  - [ ] それぞれが独立に Learn 対象になれるよう `active_component` を使う

## 5) store 反映: 既存の差分適用に乗せる

- [ ] Learn による `cc_key` 更新は「そのフレームの rows_after に反映」し、既存の
  - `src/grafix/interactive/parameter_gui/store_bridge.py`
  - `grafix.core.parameters.view.update_state_from_ui`
    を通じて ParamStore に反映させる（新しい裏口 API は作らない）

## 6) テスト（mido 無しで learn を検証）

- [ ] `tests/interactive/parameter_gui/test_midi_learn.py`（新規）
  - [ ] Dummy `MidiController` 相当（`last_cc_change` を手動で進められる）で Learn 状態遷移をテスト
  - [ ] `active_target` が設定されている状態で CC イベントが来たら `cc_key` が更新される
  - [ ] 同じ `seq` では再適用されない
  - [ ] （vec3/rgb を採用した場合）成分ごとに割り当てできる

## 7) 受け入れ条件（Done の定義）

- [ ] GUI の `cc` 列に数値入力欄が無く、Learn ボタンで割り当てできる
- [ ] Learn 中にノブを回すと 1 回で割当され、Learn が解除される
- [ ] `PYTHONPATH=src pytest -q` が通る
