# parameter_gui_display_order_code_order_checklist_2025-12-26.md
#
# どこで: parameter_gui（`src/grafix/interactive/parameter_gui/`）と parameters core。
# 何を: Primitive / Effect の表示順を「コードで書いた順（観測順）」に変更する実装チェックリスト。
# なぜ: スクリプトの思考順と GUI の操作順を一致させ、探す→調整→戻るの往復コストを下げるため。
#
# 参照: `docs/memo/parameter_gui_display_order_code_order_plan_2025-12-25.md`

## 事前確認（ここが OK なら実装に入る）

- [x] 「コード順」= `FrameParamsBuffer.record()` の append 順（= `merge_frame_params()` が受け取る `records` の順）で確定して良い
  - [x] ヘルパ関数に分割した場合でも「実行で観測した順」をコード順とみなす、で良い
- [x] Effect chain は「現行どおり 1 ブロック（折りたたみ維持）」で確定して良い（ステップ単位に分解しない）
- [x] `display_order` は runtime のみ（永続化しない）で確定して良い
- [x] Style（global + layer）は現状の最上段固定・順序維持で確定して良い

## 実装（runtime に観測順インデックスを追加）

- [x] `ParamStoreRuntime` に以下を追加（永続化対象に入れない）
  - [x] `display_order_by_group: dict[tuple[str, str], int]`（group=(op, site_id)）
  - [x] `next_display_order: int`
- [x] store 初期化時に `next_display_order=1`、辞書は空で開始する
- [x] 既存の JSON 永続化（ParamStore）に影響しないことを確認する（フィールド追加で dump/load が壊れない）

## 実装（merge で採番）

- [x] `merge_frame_params()` で `records` を処理する順に group を観測し、未登録なら `display_order` を採番する
- [x] 既存 group の `display_order` は実行中固定（再採番しない）
- [x] 1 フレーム内で同一 group が複数回出ても `display_order` は同一のまま

## 実装（GUI の並び: style 固定 + 非 style をブロック化してソート）

- [x] `store_bridge._order_rows_for_display()` を差し替える
  - [x] Style は先頭固定（既存ロジックを維持）
  - [x] 非 Style は以下のブロックに分けて「ブロック順」を sort する
    - [x] Primitive ブロック（現行どおり (op, ordinal) 単位）
    - [x] Effect chain ブロック（現行どおり chain_id 単位）
    - [x] other ブロック（必要最小限で、(op, site_id) を軸に扱う）
- [x] ブロックの順序キーを `display_order` に寄せる
  - [x] Primitive: そのブロックの (op, site_id) の `display_order`
  - [x] Effect chain: チェーン内ステップ（(op, site_id)）の `display_order` の min（チェーン先頭位置に寄せる）
- [x] ブロック内行順は現状維持
  - [x] Primitive: `arg` 安定
  - [x] Effect: `step_index -> arg`

## 付随（ヘッダ名 / 衝突解消の整合）

- [x] Primitive ヘッダ dedup（`name#1/#2`）の採番順を「表示順（display_order）」に合わせる
- [x] Effect chain ヘッダ（`effect#N`）の採番を `chain_ordinals` 依存から「表示順（display_order）」に変更する

## テスト（追加）

- [x] `tests/interactive/parameter_gui/` に表示順テストを追加する
  - [x] Primitive と Effect が interleave すること（style は常に先頭）
  - [x] Effect chain が「最初のステップ位置」に出ること（min(display_order)）
  - [x] ヘッダ名の dedup 番号が表示順と矛盾しないこと（必要なら）

## 手動スモーク

- [ ] `sketch/` の代表ケースで GUI 表示順が意図どおりか確認する（Style → 以降コード順）

## 追加で確認したいこと（実装中に出たら追記）

- [ ] 表示順が変わった際の「折りたたみ状態の保持」への影響がないか（キー体系に依存していないか）
- [ ] `site_id` の粒度が期待どおりか（同一行が別 group と誤認されないか）
