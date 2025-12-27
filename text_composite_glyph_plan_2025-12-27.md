# text プリミティブ: コンポジット glyph 対応（実装改善計画）

作成日: 2025-12-27

## 背景

`src/grafix/core/primitives/text.py` は fontTools でグリフを `RecordingPen` に描画し、`FlattenPen` で直線分割したコマンドからポリラインを生成している。
ただし TrueType のコンポジット glyph（`addComponent` で部品合成される glyph）を展開していないため、
該当グリフが “空” 扱いになり、文字が欠ける（例: `GoogleSans-Regular.ttf` の `f`, `é`, `ï`）。

## ゴール

- `text(...)` でコンポジット glyph も輪郭ポリラインとして生成される
  - 最低限の期待: `GoogleSans-Regular.ttf` の `f` が空にならない

## 非ゴール（今回やらない）

- 文字組み（GSUB/GPOS）、合字、カーニング
- ヒンティング
- API 変更（`text(...)` の引数追加など）

## 事前確認（方針を決めたい）

- [ ] 欠損コンポーネント参照があった場合の挙動
  - 候補 A: 例外で落とす（フォント不正を早期に露出）
  - 候補 B: スキップして空にする（実用優先）；こちらで
- [ ] `reverseFlipped` を有効にするか; True で
  - 候補 A: False（そのまま）
  - 候補 B: True（反転コンポーネントの線向きを補正）

## 改善チェックリスト（実装前の分割）

- [ ] 現状の再現を固定する（最小の確認手順）
  - 例: `text(text="f", font="GoogleSans-Regular.ttf")` が空になることを確認
- [ ] 実装方針を確定する
  - `fontTools.pens.recordingPen.DecomposingRecordingPen` を使って `addComponent` を分解してから平坦化する
- [ ] `TextRenderer.get_glyph_commands()` を修正する
  - `RecordingPen()` → `DecomposingRecordingPen(glyph_set, ...)`
  - 分解後に既存の `FlattenPen` パイプラインへ流す（キャッシュ形式は維持）
- [ ] 最小のテストを追加する
  - `tests/core/primitives/test_text_composite_glyph.py`
  - [ ] コンポジット例: `f` の `coords` が空でない
  - [ ] 合成アクセント例: `é` または `ï` の `coords` が空でない
  - [ ] 既存の単純 glyph 例: `i` が引き続き空でない
- [ ] 影響確認（軽量）
  - [ ] `.ttc` を 1 つ選び、最低 1 文字だけ描画して落ちないことを確認
  - [ ] `.otf` を 1 つ選び、最低 1 文字だけ描画して落ちないことを確認
- [ ] 対象限定で検証コマンドを回す
  - `PYTHONPATH=src pytest -q tests/core/primitives/test_text_composite_glyph.py`
  - `ruff check src/grafix/core/primitives/text.py`
  - 必要なら `mypy src/grafix`
- [ ] 本 md のチェックを更新して完了状況を明確化する

## 実装メモ（案）

- 現在はコンポジット glyph が `addComponent` のみになり、`_glyph_commands_to_polylines_em()` が無視してポリラインが生成されない。
- `DecomposingRecordingPen` を使うと、`addComponent` が部品輪郭に展開され、最終的に `moveTo/lineTo/closePath` の列として扱える。
