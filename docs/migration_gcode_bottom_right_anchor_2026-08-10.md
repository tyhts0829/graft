# G-code 紙右下アンカーへの移行（2026-08-10）

## 概要

G-code の紙配置設定を、固定 offset の `origin` から、プロッターボード上で紙の右下角を
合わせる machine 座標 `paper_bottom_right_mm` へ変更した。併せて、実際の紙高と異なる値を
指定できた `canvas_height_mm` を削除した。

この変更は破壊的変更である。旧 key、constructor 引数、property、fallback は残さない。
config の `version: 1` と capture manifest の `schema_version: 3` は変更しない。
標準 export の manifest には従来どおり frame の `canvas_size` があり、effective config の
新 anchor と組み合わせて座標変換を再構成できる。

## 設定の変更

変更前:

```yaml
export:
  gcode:
    y_down: true
    origin: [154.019, 14.195]
    canvas_height_mm: null
```

2026-08-10 の初回移行後:

```yaml
export:
  gcode:
    y_down: true
    paper_bottom_right_mm: [302.019, 14.195]
```

2026-08-11 の初回 Y 再校正で使用した中間設定:

```yaml
export:
  gcode:
    y_down: true
    paper_bottom_right_mm: [302.019, 2.195]
```

同日の Y=`0.0` 後続再校正後の現行設定:

```yaml
export:
  gcode:
    y_down: true
    paper_bottom_right_mm: [302.019, 0.0]
```

`origin` と `canvas_height_mm` が残った config は受け付けない。両 key を削除し、
`paper_bottom_right_mm` を追加する。

Python で `GCodeParams` を直接構築または `dataclasses.replace()` している場合も同様である。

```python
# 変更前
params = replace(base, origin=(154.019, 14.195), canvas_height_mm=None)

# 現行設定
params = replace(base, paper_bottom_right_mm=(302.019, 0.0))
```

## 旧 origin からの換算

旧設定で位置合わせに使った基準 canvas size を `(W_ref, H_ref)`、旧 offset を `(ox, oy)` とする。
新 anchor は、旧変換でその canvas の右下 `(W_ref, H_ref)` が到達していた machine 座標である。

X は `y_down` に関係なく次で求める。

```text
anchor_x = ox + W_ref
```

Y は旧 `y_down` に応じて求める。

```text
# 旧 y_down=true
old_y_extent = canvas_height_mm if canvas_height_mm is not null else H_ref
anchor_y = oy + old_y_extent - H_ref

# 旧 y_down=false
anchor_y = oy + H_ref
```

通常どおり `y_down=true`、`canvas_height_mm=null` だった場合は単純に
`paper_bottom_right_mm=(ox + W_ref, oy)` となる。

Grafix の校正済み A5 は `W_ref=148`, `H_ref=210`,
`origin=(154.019, 14.195)`, `canvas_height_mm=null` だったため、換算結果は次である。

```text
anchor_x = 154.019 + 148 = 302.019
anchor_y = 14.195
```

この `(302.019, 14.195)` は旧 A5 の位置合わせから機械的に換算した、2026-08-10
初回移行時の歴史的 anchor である。後述の 2026-08-11 Y 再校正で中間値
`(302.019, 2.195)` を経て、現在の既定値は `(302.019, 0.0)` である。

旧 `canvas_height_mm` が実 canvas 高と異なっていた設定は、紙高を変えると右下位置も変わるため、
全紙サイズで旧挙動を保つ単一 anchor へは換算できない。上式で一つの基準 canvas を選び、以後は
その右下 machine 座標を全紙サイズで固定する。

## 2026-08-11 の Y 再校正と Y=`0.0` 後続整合

2026-08-10 の初回 anchor で A4 用紙全体を置くと、既定の `y_down=true` で
machine Y 範囲は `14.195 .. 311.195` mm となる。machine Y の安全圏を
`0 .. 300` mm とする前提で上限を超えるため、全 canvas size に対して anchor Y を
12 mm 小さい中間値 `2.195` mm へ再校正した。その後、紙の右下角を machine Y=`0.0` mm
に合わせることが確定し、anchor Y をさらに `2.195` mm 小さくした。

```text
paper_bottom_right_mm = (302.019, 14.195)  # 旧 A5 校正由来の初回値
                         -> (302.019, 2.195)  # -12 mm の中間校正値
                         -> (302.019, 0.0)    # 追加 -2.195 mm、2026-08-11 現行値
```

現行出力は旧 `14.195` mm anchor 比で全 machine Y 座標が一律 `-14.195 mm`、
中間 `2.195` mm anchor 比で一律 `-2.195 mm` となる。X anchor、X 非反転、Y 反転、
右下アンカー契約は変えない。repository に実機の公式上限は記録されておらず、
`bed_y_range` は `null` のままである。最終整合の詳細は
[`gcode_bottom_right_anchor_y_zero_followup_plan_2026-08-11.md`](plan/gcode_bottom_right_anchor_y_zero_followup_plan_2026-08-11.md)
を参照する。

## 新しい座標契約

canvas は左上原点、X は右向き、Y は下向きである。canvas size を `(W, H)`、
`paper_bottom_right_mm` を `(anchor_x, anchor_y)` とすると、変換は次となる。

```text
dx = canvas_x - W
dy = canvas_y - H

machine_x = anchor_x + dx
machine_y = anchor_y - dy  # y_down=true
machine_y = anchor_y + dy  # y_down=false
```

- canvas 右下 `(W, H)` は常に `(anchor_x, anchor_y)` となる。
- X は反転しない。canvas で右へ進むと machine X も増える。
- 既定の `y_down=true` では Y だけを反転し、preview と紙出力の上下鏡像を防ぐ。
- `allow_reverse` は stroke の走査順最適化であり、座標軸や図形の反転ではない。
- `canvas_size` が紙の幅と高さに関する唯一の値である。

現行の校正済み anchor `(302.019, 0.0)` と既定の `y_down=true` での紙領域は
次となる。

| 紙              |        machine X範囲 | machine Y範囲 |             右下 |
| --------------- | -------------------: | ------------: | ---------------: |
| A5 `(148, 210)` | `154.019 .. 302.019` |    `0 .. 210` | `(302.019, 0.0)` |
| A4 `(210, 297)` |  `92.019 .. 302.019` |    `0 .. 297` | `(302.019, 0.0)` |

alignment sketch を現行 anchor で出力した実描画範囲は、A5 が
`X=159.019 .. 297.019`, `Y=5 .. 205` mm、A4 が `X=97.019 .. 297.019`,
`Y=5 .. 292` mm である。

2026-08-10 の初回右下アンカー移行のみでは、A5 の G-code 座標は旧出力と同じで、
A4 は旧固定 `origin` の出力から X 方向だけ `210 - 148 = 62` mm 左へ移った。
2026-08-11 の最終再校正後は、その初回移行結果から A5/A4 を含む全 canvas size の
machine Y が一律 `14.195 mm` 小さくなる。Y 反転、clip、安全マージン、bed 検証、
stroke ordering、feed、Z の契約は変更しない。

## Capture / worker

標準の headless save と interactive `ExportJobSystem` は、capture 時点の
`GCodeParams.paper_bottom_right_mm` と frame `canvas_size` を snapshot として encoder へ渡す。
spawn worker は config を再探索しない。capture manifest の effective config では
`paper_bottom_right_mm` が2要素配列として記録され、`origin` と `canvas_height_mm` は現れない。

低レベルの `CaptureService.export(..., gcode_params=...)` に frame の effective config と
異なるパラメータを直接渡した場合、manifest の config snapshot は frame 側のままである。
再現可能な manifest を必要とする経路では、frame と encoder に同じ effective config を使用する。
