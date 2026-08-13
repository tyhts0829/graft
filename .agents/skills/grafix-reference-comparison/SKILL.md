---
name: grafix-reference-comparison
description: "参照画像とGrafixの現在のPNG出力をA5縦へ等比配置し、参照をシアン、現行をマゼンタ、一致部を濃紺で重ねた比較画像・差分ヒートマップ・横並び画像・数値指標を生成して差異を分析する。ユーザーが『色を変えて重ねて』『参照との差を明確にして』『比較画像を作って』『どこがずれているか確認して』など、実装を変更せず画像比較・位置合わせ・差分分析を求めたときに使う。"
---

# Grafix Reference Comparison

参照画像と現在のレンダーを同じA5座標系へ揃え、再現上の差を可視化する。比較依頼だけならSketchや最終PNGを変更しない。

## Workflow

1. 参照画像と比較対象PNGが読めることを確認する。
2. 次のスクリプトを実行する。

   ```bash
   PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/envs/gl5/bin/python \
     .agents/skills/grafix-reference-comparison/scripts/compare_images.py \
     --reference "/absolute/path/to/reference.jpg" \
     --current "/absolute/path/to/current.png" \
     --out-dir "/absolute/path/to/comparison"
   ```

3. `color_overlay.png` と `side_by_side.png` を画像として確認する。必要なら `difference_heatmap.png` も確認する。
4. レポートJSONの指標と目視を組み合わせ、差を以下の順で短く報告する。
   - 主要図形の位置・寸法
   - ガイド線と交点
   - 反復形状の本数・ピッチ
   - 文字の位置・幅・位相・字間
   - 背景色、黒濃度、紙や印刷の質感
5. ユーザーが実装変更不要と指定した場合は、Sketch、現行PNG、参照画像を一切編集しない。

## Output interpretation

- シアン: 参照にだけあるインク
- マゼンタ: 現行にだけあるインク
- 濃紺: 両者が重なるインク
- `ink_iou`: 暗部マスクのIntersection over Union。全体傾向の補助値であり、文字の可読差や紙質を代替しない。
- `current_to_reference_ink_ratio`: 参照に対する現行の暗部面積比。

色が単独で強く見える場所ほど位置・輪郭・文字形状の差が大きい。写真の紙粒子や照明ムラも差分へ入るため、ヒートマップだけで判断しない。

## Alignment contract

- 参照画像は無言で縦横別々にstretchしない。
- 既定は参照を現在PNGへ等比fitし、余白を中央配置する。
- A5縦の比較対象を前提に、画像全体を同じページ範囲として扱う。
- 参照に余分な撮影枠や明確なcropがある場合のみ、`--reference-crop LEFT TOP RIGHT BOTTOM` を使う。
- すでに正確な同寸画像同士を比較するときだけ `--alignment exact` を使う。

## Deliverables

最終回答では最低限、次を示す。

- `color_overlay.png` の絶対パスと実画像
- 主要な差を箇条書き
- 実装を変更したか否か

必要に応じて `side_by_side.png`、`difference_heatmap.png`、`comparison_report.json` のパスも示す。
