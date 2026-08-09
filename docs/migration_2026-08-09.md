# 2026-08-09 G-code レイヤ単位最適化への移行

G-code の stroke-order contract を、source polyline 単位から `Layer` 単位へ変更した。
この変更は、2026-07-22 に導入した source-polyline 限定 contract を置き換える破壊的変更である。

## 1. 新しい最適化境界

G-code exporter は、各 input polyline を紙の安全領域へクリップした後、同一レイヤ内に残った
全 stroke を一つの列として扱う。

- source polyline 境界を越えて stroke を並べ替えられる。
- `allow_reverse=true` なら、source polyline 境界を越えた候補も逆向きに描画できる。
- `bridge_draw_distance` は、source polyline 境界を越えた隣接 stroke 間にも適用する。
- 並べ替え、逆向き描画、bridge は別レイヤへ跨がない。
- 頂点数、閉曲線らしさ、producer 順から face、glyph、group を推測しない。

source polyline の情報は G-code comment、診断、決定的な tie-break のために保持するが、
最適化や bridge の semantic boundary にはしない。

## 2. レイヤ単位のマスタースイッチ

`L.layer()` に `gcode_optimize` keyword を追加した。既定値は `True` である。

```python
optimized = L.layer(geometry, gcode_optimize=True)
preserved = L.layer(geometry, gcode_optimize=False)
```

`gcode_optimize=True` のレイヤは、global `GCodeParams` または
`.grafix/config.yaml` の次の 3 設定に従う。

- `optimize_travel`
- `allow_reverse`
- `bridge_draw_distance`

`gcode_optimize=False` は粗いマスタースイッチであり、そのレイヤでは次の処理をすべて停止する。

- stroke の並べ替え
- stroke の逆向き描画
- stroke 間の pen-down bridge

この場合、clip 後 stroke の入力順と向きを維持し、stroke 間では必ずペンアップする。
`gcode_optimize` は G-code 出力だけに影響し、SVG、PNG、GL 描画や Geometry の cache identity は
変更しない。

## 3. Global 設定の関係

`gcode_optimize=True` のレイヤでは、global 設定を次の順で解釈する。

1. `optimize_travel=true` なら、同一レイヤ内の全 clip 後 stroke を並べ替える。
2. `optimize_travel=true` かつ `allow_reverse=true` なら、2 本目以降の候補を逆向きから描くことも
   許可する。
3. 並べ替えと向きが確定した後、`bridge_draw_distance=d` なら、隣接 stroke 間の距離が
   `d` mm **未満**の gap を pen-down のまま描画して繋ぐ。

`optimize_travel=false` では入力順と向きを維持する。この場合も
`bridge_draw_distance` が `null` でなければ、入力順の隣接 stroke 間に bridge 判定を行う。
`bridge_draw_distance=null` では connector を追加しない。距離が指定値と等しい場合も bridge しない。

bridge は移動順だけを変える機能ではない。指定距離未満の隙間を描画してよいという利用者の
明示的な許可であり、その短い connector を実際に pen-down で追加する。

## 4. G-code commentの変更

layer-wideの並べ替え後は、同じsource polylineのstrokeが連続するとは限らない。このため、
連続blockを表していた次のcommentを削除した。

```text
; source_polyline {poly_idx} start
; source_polyline {poly_idx} end
```

各strokeの出典と反転状態を示す次のcommentは維持する。診断toolはこの形式を使う。

```text
; stroke polyline {poly_idx} seg {seg_idx}[ reversed]
```

commentは実行命令ではないため、旧block形式の互換出力は提供しない。

## 5. 既存 sketch の移行

`gcode_optimize` の既定値は `True` なので、既存の `L.layer(...)` と暗黙 Layer は global 設定を
そのまま適用する。従来は通常の別 input polyline 間で実質 no-op だった設定が、同一レイヤ全体へ
作用するため、生成される G-code の stroke 順、向き、Z 上下回数が変わり得る。

描画順、描画方向、各 stroke 間のペンアップをまとめて保持したいレイヤには、明示的に
`gcode_optimize=False` を指定する。

```python
layer = L.layer(
    geometry,
    gcode_optimize=False,
)
```

全レイヤで並べ替えだけを止め、短距離 bridge は維持したい場合は、レイヤのマスターを有効のまま
global `optimize_travel: false` を設定する。connector を全レイヤで禁止する場合は、
global `bridge_draw_distance: null` を設定する。

旧 source-polyline 限定 mode、face-block heuristic、互換 wrapper は提供しない。
