# parameter_gui_display_order_code_order_plan_2025-12-25.md
#
# どこで: `src/grafix/interactive/parameter_gui/store_bridge.py` ほか。
# 何を: parameter_gui の Primitive / Effect の表示順を「コードで書いた順」へ変更する計画。
# なぜ: 探す→調整→戻る の往復コストを減らし、スクリプト（思考）と GUI（操作）を一致させるため。

## 背景（現状）

現状の表示順は概ね以下で決まっている。

- `rows_from_snapshot()` が `op → ordinal → arg` で並べる（`src/grafix/core/parameters/view.py`）。
- その後 `store_bridge._order_rows_for_display()` が
  - Style（global/layer）を先頭に寄せる
  - Primitive は「まとめて」
  - Effect は「まとめて（チェーン順→ステップ順→arg）」
  - …という順で返しており、Primitive と Effect がコード順に interleave しない（`src/grafix/interactive/parameter_gui/store_bridge.py`）。

## ゴール

- Style ヘッダ（global + layer）は **現状どおり最上段**。
- それ以外（Primitive / Effect / other）は **「コードで書いた順」**に並ぶ。
  - Primitive と Effect が interleave する。
  - Primitive の種類（op）や EffectBuilder のチェーン順に依存しない。
- 既存の永続化（ParamStore JSON）とキー体系は極力壊さない（表示順のみの変更を優先）。

## 非ゴール

- ラベル命名規則（`circle#1` 等）やパラメータ key（`ParameterKey`）の仕様変更。
- 既存のブロック化 UI（Style / primitive header / effect chain header）の全面刷新。

## 定義（採用）: 実行時の“初出順（観測順）”

- 「書いた順」= `FrameParamsBuffer.record()` の append 順（= `merge_frame_params()` が受け取る `records` の順）とみなす。
- (op, site_id) 単位の “group” を **この実行で初めて観測した順**に、`display_order=1..N` を採番する。
- `display_order` は **実行中は固定**（毎フレームの並び替えで UI が跳ねない）。
- `display_order` は **永続化しない**。
  - 理由: 永続化すると「既存 group の途中に新しい行を挿入した」ケースで、新規 group が末尾へ押しやられやすい（コード順とズレる）。
  - 目的は「現在のコードの順序に追従」なので、起動ごとに観測順で再構築する方が直感に一致する。

## 実装方針（採用: 観測順）

- Style（global/layer）は現行ロジックのまま先頭に固定（固定順も維持）。
- ParamStore の runtime に **group の表示順インデックス**を追加する（永続化しない）:
  - 例: `ParamStoreRuntime.display_order_by_group: dict[tuple[str, str], int]`
  - 例: `ParamStoreRuntime.next_display_order: int`
- `merge_frame_params()` で records を処理する順に、
  - group=(op, site_id) が未登録なら display_order を採番
  - 以後、その group の表示順キーとして使う
- GUI の並び順は「ブロック単位」で決める（Primitive/Effect を interleave させるため）:
  - Primitive ブロック: 現行どおり (op, ordinal) 単位（= 同一 (op, site_id) の集合）。
  - Effect ブロック: 現行どおり chain_id 単位（見出し/折りたたみを維持）。
  - other: (op, site_id) 単位（必要なら後で調整）。
- ブロックの順序キー:
  - Primitive: そのブロックの (op, site_id) の display_order。
  - Effect chain: チェーン内ステップ（(op, site_id)）の display_order の **min**（チェーン先頭位置に寄せる）。
- ブロック内の行順:
  - Primitive: `arg` で安定（現状維持）。
  - Effect: `step_index → arg`（現状の “ステップ順” は維持）。

## 付随タスク（UX 一貫性）

表示順を変えると、ヘッダ名の衝突解消（`name#1/#2`）の “番号” が表示順と逆転する可能性がある。

- Primitive ヘッダ（`G(name=...)` の dedup）: dedup の順序を **表示順**に合わせる。
- Effect チェーンヘッダ（`E(name=...)` / `effect#N`）: `chain_ordinals` ではなく **表示順**（= 観測順由来）で `effect#1..` を割り当てる。

## チェックリスト（この順で進める）

- [ ] 定義の確認: 「コード順」= **観測順（records の順）**で良いか？（ヘルパ関数跨ぎでも期待通りか？）
- [ ] 仕様確定: Effect chain は “1 ブロック”維持で良いか？（ステップ単位で分解する必要はあるか？）
- [ ] 実装: runtime に display_order index を追加（永続化しない）
- [ ] 実装: `merge_frame_params()` で group の display_order を採番（records の順）
- [ ] 実装: `store_bridge._order_rows_for_display()` を「style 固定 + 非 style をブロック化して display_order でソート」に差し替え
- [ ] 実装: ヘッダ名 dedup / `effect#N` 採番も “表示順” に合わせる
- [ ] テスト追加: `tests/interactive/parameter_gui/` に
  - [ ] Primitive と Effect が interleave する順序のテスト
  - [ ] Effect chain が “最初のステップ位置”に出るテスト
  - [ ] ヘッダ名の dedup 番号が表示順と矛盾しないテスト（必要なら）
  - [ ] 既存テスト（Style 順）の維持
- [ ] 手動スモーク: `sketch/` の代表ケースで、GUI の順が意図通りか確認（Style が先頭、以降がコード順）
- [ ] ドキュメント（任意）: `architecture.md` の parameter_gui 節に「表示順は code order」を追記

## 追加で確認したいこと（判断材料）

- 典型ケースは「1つの draw 関数に順番に書く」か、「ヘルパ関数に分ける」か。
- “コード順”が欲しい理由は「見た目の順」より「スクリプトの流れ」を追いたい、で合っているか。
