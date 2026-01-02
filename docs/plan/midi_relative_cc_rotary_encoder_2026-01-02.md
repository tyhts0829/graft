# ロータリーエンコーダーの相対 MIDI CC（min/max 非依存）対応チェックリスト（2026-01-02）

目的: endless ロータリーエンコーダー等が送る「相対 CC（増減）」を解釈し、

- Parameter GUI の `ui_min/ui_max` に依存せず（= UI レンジは UI のためだけに使い）、
- “今の値から” 滑らかに増減できる（= 絶対値への写像ではなく増分で動かす）

ように、CC learn 済みパラメータを操作できるようにする。

背景:

- 現状の `MidiController` は CC value を「絶対値」として正規化（value/127）している。
- ロータリーエンコーダーは value が「増分/減分（Δ）」を表すことが多く、現状の実装では意図したパラメータ操作にならない。
- さらに現在の CC→ パラメータは `resolver` 側で `ui_min/ui_max` を使って 0..1 を値域へ写像しており、
  `ui_min/ui_max` が “UI 用レンジ” であるにも関わらず CC の操作感（1 クリックの増分）がレンジに引きずられる。

方針（採用案）:

- `cc_snapshot: dict[int, float]`（0.0–1.0 の “絶対”）は維持する（既存の `cc[...]` / 絶対 CC は壊さない）。
- 相対 CC は `cc_snapshot` に “絶対値として” 変換しない。
  - 代わりに「受信 value を Δ（整数 step）へ復号」し、**CC learn 済みの ParamStore の `ui_value` を Δ で直接更新**する。
  - これにより **`ui_min/ui_max` と無関係な増減**（= UI レンジは UI 用のまま）を実現する。
- 相対方式は `run(..., midi_mode=...)` で選択できるようにする（既存シグネチャ維持）。
- v1 は「Parameter GUI に割当済みの `cc_key` を持つパラメータを nudge する」機能に絞る。

非目的（v1 ではやらない）:

- CC 番号ごとの「相対/絶対の混在」設定（必要なら別タスクとして設計）
- soft takeover / pickup（絶対フェーダーの飛び防止）
- 14bit の相対 CC
- “相対 CC を `cc[...]` として意味のある値にする” 仕様追加（必要なら別タスク）

## 0) 事前に決める（あなたの確認が必要）

- [x] サポートする相対 CC 方式（v1 で入れる範囲）
  - [ ] `signed_64`（64=no-op、1..63=+1..+63、65..127=-1..-63）
  - [ ] `twos_complement`（0..63=+0..+63、64..127=-64..-1）
  - [x] `binary_offset`（0..127 を -64..+63 にオフセット：`delta=value-64`、64=no-op）
  - [ ] `inc_dec`（1=+1、127=-1、その他=0）※必要なら
- [x] `midi_mode` の命名
  - [x] `7bit`（既存: absolute）
  - [x] `14bit`（既存: absolute）
  - [x] `7bit_rel`（v1 は binary_offset を意味する）
- [x] 相対 CC の “1 step” をパラメータ値へ変換するルール（`ui_min/ui_max` を使わない）
  - [x] `ParamMeta` に `nudge_step` を追加し、未指定は kind 別 default（体験重視）
    - float: default `1e-4`（最小 1e-4）
    - int: default `1`
    - choice: default `1`（±で前後へ、端で止める）
    - vec3/rgb: v1 では対象外（後回し）
- [x] 相対 CC で値を更新する際、対象パラメータの `override` をどう扱うか
  - [x] CC 操作が来たら常に `override=True` にする（既存の CC 優先と整合）

## 1) 受け入れ条件（完了の定義）

- [ ] `mode="7bit"` / `mode="14bit"` の挙動と既存テストが維持される
- [ ] `mode="7bit_rel"` が使え、binary_offset（`delta=value-64`）として解釈される
- [ ] 相対 mode で受信した CC が **`cc_snapshot` 経由ではなく ParamStore の `ui_value` を更新**して効く
- [ ] 相対 CC の増減量が **`ui_min/ui_max` の設定に依存しない**（同じ操作で同じ Δ が適用される）
- [ ] 連続操作が滑らか（1 step が小さく、かつ高速回転で破綻しない）
- [ ] float の step は最小 `1e-4`（`nudge_step` 未指定時も `1e-4`）
- [ ] Δ==0 のメッセージは「更新なし」として扱う（`last_cc_change` を増やさない）
- [ ] `PYTHONPATH=src pytest -q tests/interactive/midi/test_midi_controller.py`（Δ 復号）
- [ ] `PYTHONPATH=src pytest -q tests/interactive/parameter_gui/test_*.py` 相当があれば追加（無ければ最小のユニットテストを新設）

## 2) 実装（設計メモ）

- [ ] `src/grafix/interactive/midi/midi_controller.py`

  - [ ] 相対 mode を許可し、CC message を “Δ” として取り出せるようにする
    - 例: `poll_pending()` 内で相対 CC を検出し、`_pending_cc_deltas: dict[int, int]` に加算
    - 取り出し API: `take_pending_cc_deltas() -> dict[int, int]`（呼ぶとクリア）
  - [ ] Δ 復号ロジックを 1 箇所へ集約（v1 は binary_offset 固定）
    - `delta = int(value) - 64`（64 は no-op）
  - [ ] `last_cc_change` は “learn 用” のため維持（相対でも更新するが、Δ==0 は更新しない）

- [ ] `src/grafix/interactive/runtime/draw_window_system.py`

  - [ ] `draw_frame()` 冒頭（`SceneRunner.run()` の前）で
    - `midi.poll_pending()`
    - `deltas = midi.take_pending_cc_deltas()`
    - `apply_relative_cc_deltas_to_store(store, deltas, ...)`
      を実行し、このフレームの `parameter_context` が見る `store_snapshot` に反映させる

- [ ] `src/grafix/core/parameters/`（新規ユーティリティ）
  - [ ] `apply_relative_cc_deltas_to_store(store, deltas, *, step_policy=...)`
    - store_snapshot(store) を走査し、`state.cc_key` に一致する ParameterKey を集める
    - kind ごとに `ui_value` を Δ 更新（`ui_min/ui_max` は参照しない）
    - 更新は `update_state_from_ui()` を通して canonicalize/型整合を保つ
    - 相対入力が来た key は `override=True` にする（今回の決定）

## 3) 仕様（挙動の約束）

- [ ] CC learn 済み（`cc_key` 設定済み）のみ対象
- [ ] `ui_min/ui_max` は “スライダー表示レンジ” としてのみ扱い、相対 CC の Δ 計算には使わない
- [ ] 1 step の Δ は `nudge_step`（または kind 別 default）で決め、値域が広くても同じ操作感になる
  - float の step は `max(abs(nudge_step), 1e-4)`（未指定は `1e-4`）
  - int/choice の step は `max(abs(nudge_step), 1)`（未指定は `1`）
- [ ] choice は ± で前後へ（wrap はしない、端で止める）

## 4) 変更箇所（ファイル単位）

- [ ] `src/grafix/interactive/midi/midi_controller.py`
- [ ] `src/grafix/interactive/runtime/draw_window_system.py`
- [ ] `src/grafix/core/parameters/relative_cc_ops.py`（新規、または既存 ops 群へ追加）
- [ ] `src/grafix/core/parameters/meta.py`（`ParamMeta` に `nudge_step` 追加）
- [ ] `src/grafix/api/runner.py`（docstring: `midi_mode` の説明を拡張）
- [ ] `README.md`（任意: `run(..., midi_mode=\"...\")` の例を 1 行追加）
- [ ] `tests/interactive/midi/test_midi_controller.py`
  - [ ] binary_offset の Δ 復号（代表値）
  - [ ] Δ==0 のとき learn 通知が進まない
- [ ] `tests/core/parameters/test_relative_cc_ops.py`（新規）
  - [ ] `ui_min/ui_max` を変えても 1 step の増減が変わらない
  - [ ] float/int/choice の nudge
  - [ ] `override` の扱い（採用案どおり）

## 5) 手順（実装順）

- [x] 事前確認: `git status --porcelain`（依頼範囲外の差分なし）
- [x] 0. を確定（相対方式/命名/step ルール/override 方針）
- [ ] `MidiController` に「Δ を蓄積して取り出す」経路を追加（復号 + take API）
- [ ] `apply_relative_cc_deltas_to_store()` を追加（min/max 非依存の nudge）
- [ ] DrawWindowSystem に配線（フレーム開始で store を更新）
- [ ] ユニットテスト追加（Δ 復号 / store 反映）
- [ ] 最小確認: 対象テスト + 既存 MIDI テストを実行
- [ ] 任意: `mypy` / `ruff`

## 6) 実行コマンド（ローカル確認）

- [ ] `PYTHONPATH=src pytest -q tests/interactive/midi/test_midi_controller.py`
- [ ] `PYTHONPATH=src pytest -q tests/interactive/midi/test_midi_persistence.py`
- [ ] `PYTHONPATH=src pytest -q tests/interactive/midi/test_midi_factory.py`
- [ ] `PYTHONPATH=src pytest -q tests/core/parameters/test_relative_cc_ops.py`
- [ ] `mypy src/grafix`
- [ ] `ruff check src/grafix/interactive/midi/midi_controller.py`

## 7) 手動確認（実機）

- [ ] `run(..., midi_port_name=..., midi_mode=\"7bit_rel\")` で起動
- [ ] CC learn で対象パラメータへ割当 → エンコーダー回転で滑らかに増減する
- [ ] UI の min/max を狭くしても操作感が変わらない（相対 Δ がレンジ依存しない）
- [ ] 逆回転/高速回転でも破綻しない（Δ 復号 + step）

## 追加で事前確認したほうがいい点 / 追加提案（気づいたら追記）

- [ ] 「絶対フェーダー + 相対エンコーダー混在」を想定するなら、CC 番号ごとの mode 指定をどこに置くか（ParamState 拡張 / config.yaml / 別マッピングファイル）
