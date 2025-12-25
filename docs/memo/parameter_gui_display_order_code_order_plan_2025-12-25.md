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

## 「コードで書いた順」の定義（要確認）

候補は 2 つある（どちらも“それっぽい”が、想定スクリプトで差が出る）。

1) **callsite（site_id）のソート順**で決める（実装が最小）
   - `site_id` は `"{filename}:{co_firstlineno}:{f_lasti}"` 形式（`src/grafix/core/parameters/key.py`）。
   - これを (filename, co_firstlineno, f_lasti) として比較し、GUI の並び順キーにする。
   - 長所: 追加の永続化が不要。表示順だけ差し替え可能。
   - 短所: 複数ファイル/複数関数にまたがる場合、「実行順」とズレる可能性がある。

2) **実行時の“初出順（観測順）”**で決める（より直感に近い可能性）
   - `merge_frame_params()` で records を処理する順に、(op, site_id) にグローバルな表示 ordinal を採番する。
   - 長所: 関数呼び出し/モジュール跨ぎでも「実行順＝書いた順（体感）」に一致しやすい。
   - 短所: ParamStore に新しい永続化フィールド（display_ordinals など）を足すのが自然。

まずは **(1) site_id ソート**で進め、必要なら (2) に切り替える方針を提案。

## 実装方針（案: 1) site_id ソート）

- Style（global/layer）は現行ロジックのまま先頭に固定。
- それ以外の行を「ブロック単位」で並び替える:
  - Primitive ブロック: 現行どおり (op, ordinal) 単位（≒同一 callsite）。
  - Effect ブロック: 現行どおり chain_id 単位（見出し/折りたたみを維持）。
  - other: 現行の扱いに合わせて（必要なら (op, ordinal) 単位）。
- ブロック順のキー:
  - Primitive: そのブロックに属する行の `site_id` を parse して得た callsite key。
  - Effect chain: そのチェーンに属する全ステップの callsite key の **min**（最初に現れた位置）。
- ブロック内の行順:
  - 既存どおり `arg` ベースで安定（将来的に「よく触る順」テーブルを追加してもよいが今回は不要）。

## チェックリスト（この順で進める）

- [ ] 定義の確認: 「コード順」を (1) site_id ソート で良いか？（複数ファイル/関数のケースも想定するか？）
- [ ] 仕様確定: Effect chain は “1 ブロック”のまま維持して良いか？（ステップ単位で分解する必要はあるか？）
- [ ] 実装: `site_id` を安全に parse する関数を追加（失敗時のフォールバックも定義）
- [ ] 実装: `store_bridge._order_rows_for_display()` を「style 固定 + 非 style をブロック化して callsite key でソート」に差し替え
- [ ] テスト追加: `tests/interactive/parameter_gui/` に
  - [ ] Primitive と Effect が interleave する順序のテスト
  - [ ] Effect chain が “最初のステップ位置”に出るテスト（必要なら）
  - [ ] 既存テスト（Style 順）の維持
- [ ] 手動スモーク: `sketch/` の代表ケースで、GUI の順が意図通りか確認（Style が先頭、以降がコード順）
- [ ] ドキュメント（任意）: `architecture.md` の parameter_gui 節に「表示順は code order」を追記

## 追加で確認したいこと（判断材料）

- 典型ケースは「1つの draw 関数に順番に書く」か、「ヘルパ関数に分ける」か。
- “コード順”が欲しい理由は「見た目の順」より「スクリプトの流れ」を追いたい、で合っているか。

