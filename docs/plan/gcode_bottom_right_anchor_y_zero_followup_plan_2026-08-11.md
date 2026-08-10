# G-code 右下アンカー Y=0 整合修正計画（2026-08-11）

- 状態: **実装・検証完了**
- 対象: `paper_bottom_right_mm=(302.019, 0.0)` への再校正後に残った設定、テスト、文書の不整合

## 1. 調査結果

Yアンカー `0.0` は `GCodeParams` と runtime config の validation 上、正式に許容される。
現在の pytest 失敗は `0.0` が拒否されたためではない。

focused 4 suite の現在結果は `7 failed, 242 passed`。失敗はすべて
`tests/export/test_gcode.py` の期待値に、前回の anchor Y=`2.195` と「旧 `14.195` から
`-12 mm`」という前提が残っていることが原因である。

```text
旧アンカー: 14.195
現行アンカー: 0.000
実際の変化量: 0.000 - 14.195 = -14.195 mm
```

また、production default、packaged default、テストは `0.0` に変わっている一方、
実利用の `.grafix/config.yaml` はまだ `[302.019, 2.195]` である。このままでは
interactive/通常exportはY=`2.195` を使い、fallback/testはY=`0.0` を使う二重状態になる。

## 2. Y=0 での正しい座標

| 対象 | machine Y |
|---|---:|
| A5 用紙全体 | `0 .. 210` |
| A5 alignment 実描画 | `5 .. 205` |
| A4 用紙全体 | `0 .. 297` |
| A4 alignment 実描画 | `5 .. 292` |
| 任意canvasの右下 | `0` |
| 右下から1 mm内側 | `1` |

既存 `alignment_A4_210x297_001.gcode` と比べると、再生成後は全157点で
X不変、Y一律 `-14.195 mm`、描画範囲は `Y=5.000 .. 292.000` となる。

## 3. 実装チェックリスト

- [x] `.grafix/config.yaml` を `[302.019, 0.0]` に揃える。
- [x] `src/grafix/resource/default_config.yaml` の数値表記を `[302.019, 0.0]` に統一する。
- [x] `tests/export/test_gcode.py`
  - [x] A5 期待Yを `210 / 105 / 0` にする。
  - [x] A4 中心/右下の期待Yを `148.5 / 0` にする。
  - [x] 任意canvasの右下/内側1 mmを `0 / 1` にする。
  - [x] 旧値との差を `-14.195 mm` に更新する。
  - [x] test名、docstring、定数の意味をY=`0.0` に揃える。
- [x] `architecture.md`、G-code migration、先行2計画の現行値を整合する。
  - [x] `2.195 ..` の範囲を `0 ..` へ更新する。
  - [x] `-12 mm` を `-14.195 mm` へ更新する。
  - [x] A4/A5 alignment 実生成範囲をY=`5..292` / `5..205` へ更新する。
  - [x] 旧 `14.195` は歴史値として維持する。
- [x] `bed_y_range` は今回も `null` のままとする。

## 4. 検証

- [x] focused pytest、Ruff、config validation、`git diff --check`。
- [x] A4/A5 alignment を `/tmp` へ実生成し、現行 anchor Y=`0.0` の manifest を確認する。
- [x] 既存 A4 `_001.gcode` と再生成品の157点を全比較し、X不変/Y=`-14.195`を確認する。

## 5. 非ゴール

- exporter式、Xアンカー、X/Y反転、paper marginの変更
- `bed_y_range` の有効化
- 既存生成済みG-codeの上書き
- 実機の自動駆動

## 6. 作業ツリー境界

本修正はユーザーが開始したY=`0.0` 再校正の整合だけを扱う。他エージェントの
`sketch/agent_art/` 差分、`sketch/work/260810_measurement_poster.py` の移動/削除差分、
その他の依頼外差分は restore、stage、削除しない。

## 7. 実装・検証結果

- project/packaged config と `GCodeParams` 既定値を `(302.019, 0.0)` へ統一した。
- 修正前の focused pytest: `7 failed, 242 passed`
- 修正後の focused pytest: `249 passed`
- 対象 Python ファイルの Ruff: pass
- project config validation: pass。effective anchor は `(302.019, 0.0)`。
- `git diff --check`: pass
- A4 再生成品は157個のXY移動を持ち、旧 anchor Y=`14.195` の参照出力に対し
  全点で X 不変、Y 一律 `-14.195 mm` となった。
- A4 alignment の実描画範囲は `X=97.019 .. 297.019`, `Y=5.000 .. 292.000` mm。
- A5 alignment の実描画範囲は `X=159.019 .. 297.019`, `Y=5.000 .. 205.000` mm。
- 再生成 manifest の `paper_bottom_right_mm` は `[302.019, 0.0]`、`bed_y_range` は
  計画どおり `null`。
- 既存生成済み G-code は上書きせず、検証用再生成は `/tmp` で行った。
