---
name: grafix-reference-reproduction
description: "与えられたPNG・JPEGなどの参照画像を、構図、余白、配色、文字、反復、重なり、線密度まで近づけた単一のGrafix Sketchとして忠実に再現・模写・トレースするときに使う。新規作品の自由制作、参照画像から着想だけを得る創作、複数案・バリエーション・contact sheet・winner選定、参照画像への一致を目的としない既存Sketchの一般調整には使わない。画像が添付されているだけでは使わず、再現の意図がある場合に使う。"
---

# Grafix Reference Reproduction

参照画像との差を縮める1本のSketchだけを実装し、画像比較で収束させる。候補、別concept、contact sheet、winner選定を作らない。

## 固定契約

- Pythonモジュールとして有効で衝突しない `<unique_id>` を決め、`sketch/agent_art/<unique_id>.py` だけを作品コードとして作る。別案のSketchを作らない。
- `CANVAS_SIZE = (148, 210)` に固定し、A5縦以外へ変更しない。
- import直後へ次の調整ブロックを集約する。
  - `CANVAS_SIZE`
  - `BACKGROUND_COLOR`
  - 線色palette
  - `LINE_THICKNESS = 0.001`
  - seed
  - 主要layout、scale、crop、offset
  - すべてのfill用 `density` と `min_spacing`
- 調整ブロックは次の形を基準にし、作品固有の名前へ置き換える。

  ```python
  CANVAS_SIZE = (148, 210)
  BACKGROUND_COLOR = (1.0, 1.0, 1.0)
  LINE_THICKNESS = 0.001
  SEED = 0
  LINE_COLORS = {"ink": (0.0, 0.0, 0.0)}
  FILL_DENSITIES = {"ink": 400.0}
  FILL_MIN_SPACINGS = {"ink": 0.05}
  ```

- fillが1種類なら `FILL_DENSITY` と `FILL_MIN_SPACING` を使う。複数なら意味名を持つ `FILL_DENSITIES` と `FILL_MIN_SPACINGS` のmappingへ全値を置く。すべての `E.fill` からこれらを参照し、`density` と `min_spacing` の数値をinlineに書かない。引数名は `min_spacing` とする。
- 参照画像で意図された線色1色につき、ちょうど1つのLayerを作る。背景色、紙色、アンチエイリアス、圧縮ノイズを線色へ数えない。
- 同色のGeometryをlistまたはtupleへまとめ、同じ色のLayerを複数作らない。濃淡を同色Layer内のhatch密度・間隔で表現し、重なりをGeometry側のclip・booleanなどで解決する。
- すべての `L(...).layer(..., thickness=LINE_THICKNESS)` と `run(..., line_thickness=LINE_THICKNESS)` に同じ定数を渡す。Layerごとに線幅を変えない。
- module直下に `draw`、`CANVAS_SIZE`、`BACKGROUND_COLOR`、`LINE_THICKNESS` を公開する。previewでも同じcanvas、背景、線幅、seedを使う。
- preview用の `run()` は必ず `if __name__ == "__main__":` の内側で呼び、rendererがmoduleを安全にimportできるようにする。
- `RealizedGeometry` を直接importしない。
- このworkflowから `grafix-art-loop` を呼ばない。

## 単一収束Workflow

0. 参照画像が会話またはアクセス可能なpathに存在することを確認する。画像を確認できない場合は推測で制作せず、再添付を依頼する。
1. 参照画像を画像として確認し、縦横比、外形、余白、主要形状、線色palette、文字、反復、重なり順、fill密度へ分解する。
2. 参照画像の比率がA5と異なる場合は、無言でstretchしない。等比fitを基準とし、必要なcrop、scale、offsetを冒頭の調整ブロックで制御する。
3. 実装前に、必要なoperationだけをlive registryで確認する。

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/envs/gl5/bin/python -m grafix list primitives
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/envs/gl5/bin/python -m grafix list effects
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/envs/gl5/bin/python -m grafix describe primitive <name>
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/envs/gl5/bin/python -m grafix describe effect <name>
   ```

4. `G.rect`、`G.circle`、`G.ellipse`、`G.polyline`、`G.spline`、`G.bezier`、`G.text`などの組み込みprimitiveを優先する。`E.fill`、`E.affine`、`E.translate`、`E.rotate`、`E.scale`、`E.clip`、`E.boolean`、`E.repeat`などの組み込みeffectを優先する。実在と引数はregistryで確認してから使う。
5. custom `@primitive` / `@effect` または手書きNumPyを、組み込みだけでは忠実度または可読性を保てない局所にだけ使う。custom operationの公開I/Oを `(coords, offsets)` tupleにし、`coords` は有限なC-contiguous `float32 (N, 3)`、`offsets` は先頭0・末尾NのC-contiguous `int32 (M+1,)` にする。
6. `sketch/agent_art/<unique_id>.py` を実装する。参照画像にない装飾や独自解釈を加えず、まず大きな構図と余白、次に色と重なり、最後に文字と線密度を合わせる。
7. `python -m grafix export` を使わず、同梱rendererで背景色を含む作業PNGを生成する。

   ```bash
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /opt/anaconda3/envs/gl5/bin/python \
     .agents/skills/grafix-reference-reproduction/scripts/render_sketch.py \
     --module "sketch.agent_art.<unique_id>" \
     --out "sketch/agent_art/<unique_id>.png" \
     --overwrite
   ```

8. rendererの終了コードが0で、指定PNGが存在することを確認する。rendererによるA5縦、1件以上のLayer、全線幅`0.001`、Layer数と一意な線色数の一致の検証を省略しない。
9. 作業PNGを画像として確認し、参照画像との差を具体化する。同じPythonファイルだけを修正して再renderし、明確な差が残らなくなるまで繰り返す。別候補へ分岐しない。
10. 確定後、作業PNGを衝突しない同名で `data/output/png/codex_generated/<unique_id>.png` に複製する。複製後の最終PNGをもう一度画像として確認する。

## 完了条件

- `sketch/agent_art/<unique_id>.py` から最終PNGを再生成できる。
- A5縦、線色数とLayer数の一致、全線幅`0.001`をrendererで検証し、冒頭のfill定数参照をコード上で確認済みである。
- 最終回答でSketchと最終PNGの絶対パスを短く報告する。
- 最終回答へ次の形式で実画像を必ず表示する。

  ```md
  ![再現結果](/absolute/path/to/data/output/png/codex_generated/<unique_id>.png)
  ```
