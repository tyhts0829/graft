# G-code 右下アンカー Y 再校正計画（2026-08-11）

- 状態: **実装・検証完了（後続の Y=`0.0` 再校正で置換）**
- 対象: 全 `canvas_size` の G-code 紙右下アンカー Y 位置
- 前提: machine Y の安全圏を `0 .. 300 mm` とする

## 0. 後続の Y=`0.0` 再校正

本計画は、旧 A5 校正由来の anchor Y=`14.195` mm から `12 mm` 下げ、
中間 anchor Y=`2.195` mm を実装・検証した履歴を記録する。以下の `-12 mm`、
`2.195` mm、実生成範囲はすべてこの中間校正に対する値である。

その後、紙の右下を machine Y=`0.0` mm に合わせる後続再校正を行った。
現在の anchor は `(302.019, 0.0)`、旧 `14.195` mm anchor 比の変化量は一律
`-14.195 mm` である。現行の紙全体と alignment 実描画の machine Y 範囲は次のとおり。

| 対象 | 紙全体 | alignment 実描画 |
|---|---:|---:|
| A5 `(148, 210)` | `0 .. 210` | `5 .. 205` |
| A4 `(210, 297)` | `0 .. 297` | `5 .. 292` |

現行設定とテストの最終整合は
[`gcode_bottom_right_anchor_y_zero_followup_plan_2026-08-11.md`](gcode_bottom_right_anchor_y_zero_followup_plan_2026-08-11.md)
を正本とする。

## 1. 調査結果

`data/output/gcode/gcode_test/alignment_A4_210x297_001.gcode` の157個の XY 命令は、
次の範囲にある。

```text
X = 97.019 .. 297.019 mm
Y = 19.195 .. 306.195 mm
```

- Y 上限を300 mmとすると、実命令は最大 `6.195 mm` 超過し、`Y >= 300`は8点ある。
- A4 用紙全体の理論 machine Y 範囲は `14.195 .. 311.195 mm`で、上端は
  `11.195 mm` 超過する。
- canvas 上の描画は紙端から5 mm内側で、`paper_margin_mm=2` の紙安全領域には
  収まっている。問題は紙クリップではなく machine Y 上限である。
- 現在は `bed_y_range: null` で、repository と capture manifest に実機安全上限の
  記録はない。そのため300 mm超過という判定は上記前提に基づく。

## 2. 中間再校正値と方向

`y_down=true` の現行式は次である。

```text
machine_y = canvas_height - canvas_y + anchor_y
```

したがって全座標を12 mm下げるには、アンカーを machine の小さいY方向へ
12 mm移動する。

```text
paper_bottom_right_mm: (302.019, 14.195)
                       -> (302.019, 2.195)
```

`+12 mm` の `(302.019, 26.195)` は Y 上限超過を悪化させるため採用しない。

中間再生成後の代表範囲:

| 対象                |    再校正前の machine Y |       中間再校正後 |       変化 |
| ------------------- | ------------------: | -----------------: | ---------: |
| A4 alignment 実命令 | `19.195 .. 306.195` | `7.195 .. 294.195` | 全点 `-12` |
| A4 用紙全体         | `14.195 .. 311.195` | `2.195 .. 299.195` | 全点 `-12` |
| A5 alignment 実命令 | `19.195 .. 219.195` | `7.195 .. 207.195` | 全点 `-12` |

X座標、右下アンカー契約、Y反転、幾何、clip、feed、Z、stroke orderingは変えない。

## 3. 実装チェックリスト

- [x] `src/grafix/core/gcode_params.py`
  - [x] 中間既定 `paper_bottom_right_mm` を `(302.019, 2.195)` へ更新する。
- [x] `src/grafix/resource/default_config.yaml`
  - [x] packaged default のアンカーを `[302.019, 2.195]` へ更新する。
- [x] `.grafix/config.yaml`
  - [x] 実利用 project config の中間アンカーを `[302.019, 2.195]` へ更新する。
- [x] `tests/core/test_runtime_config.py`
  - [x] 中間既定アンカーを固定する。
- [x] `tests/export/test_gcode.py`
  - [x] A4/A5/複数 canvas で右下Y=`2.195` が固定されることを検証する。
  - [x] 旧校正値と比べ、全 machine Y が一律12 mm減ることを検証する。
  - [x] X非反転/Y反転の四隅契約を維持する。
- [x] `architecture.md` と G-code 移行文書
  - [x] 中間既定アンカー、A4/A5範囲、旧校正値からの再校正を更新する。
- [x] 先の右下アンカー実装計画に、後続再校正への参照を追記する。

## 4. 安全範囲の扱い

今回の提案では `bed_y_range` は `null` のままとする。repository内に実機の
公式な可動範囲がなく、アンカー再校正と範囲検証は別の設定だからである。

実機安全圏が厳密に `0 .. 300 mm` であり、今後の超過もexport時に拒否したい場合は、
project-local `.grafix/config.yaml` の `bed_y_range` を `[0.0, 300.0]` へ変更する。
packaged default への機種固有上限の埋め込みは行わない。

## 5. 中間校正の検証

- [x] focused pytest、Ruff、config validationを実行する。
- [x] 既存成果物を上書きせず、A4/A5 alignment を一時ファイルへ実生成する。
- [x] A4の全XY命令は X不変、Y一律 `-12.000 mm` であることを確認する。
- [x] A4用紙の理論Y範囲 `2.195 .. 299.195 mm` と、描画Y範囲
      `7.195 .. 294.195 mm` を確認する。

## 6. 非ゴール

- `alignment_A4.py` の幾何変更
- Xアンカー、X/Y反転、paper marginの変更
- 既存生成済み G-code の上書き
- 実機を自動で動かすテスト

## 7. 作業ツリー境界

現在の作業ツリーには、先行する G-code 右下アンカー実装と、他エージェントの
`sketch/agent_art/` 差分がある。本再校正では対象のアンカー値、関連テスト、文書だけを
変更し、他差分を restore、stage、削除しない。

## 8. 中間校正の実装・検証結果

- 中間既定アンカーを `(302.019, 2.195)` へ更新し、packaged default と project config へ
  同じ値を反映した。
- focused pytest: `249 passed`
- 対象 Python ファイルの Ruff: pass
- project config validation: pass
- `git diff --check`: pass
- 既存の `alignment_A4_210x297_001.gcode` と一時再生成品はともに157個のXY移動を持ち、
  対応する全点で X 不変、Y 一律 `-12.000 mm` を確認した。
- A4 再生成の描画範囲は `X=97.019 .. 297.019`, `Y=7.195 .. 294.195` mm。
  用紙全体の理論Y範囲は `2.195 .. 299.195` mm。
- A5 再生成の描画範囲は `X=159.019 .. 297.019`, `Y=7.195 .. 207.195` mm。
- 再生成 manifest に `paper_bottom_right_mm=[302.019, 2.195]` が記録されることを確認した。
  `bed_y_range` は計画どおり `null` のままである。
- 既存生成済み G-code は上書きせず、検証用の再生成は `/tmp` で行った。
