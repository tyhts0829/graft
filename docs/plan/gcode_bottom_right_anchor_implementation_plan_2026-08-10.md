# G-code 紙右下アンカー実装計画（2026-08-10）

- 状態: **実装・検証完了**
- 計画作成時 branch: `main`
- 計画作成時 HEAD: `12b0485`
- 対象: preview/canvas 座標から、紙の右下角を固定した machine 座標への G-code 変換
- 置き換える計画:
  `docs/plan/gcode_paper_width_auto_centering_implementation_plan_2026-08-10.md`

本計画は、A5 で位置と向きが正しく合っている現行 G-code を基準にし、任意の紙サイズでも
紙の右下角をプロッターボード上の同じ machine 座標へ合わせるための実装計画である。
本計画はユーザー再承認後に実装し、以下のチェックリストと検証を完了した。

## 2026-08-11 追記: Y アンカーの後続再校正

本計画で導出し実装した `(302.019, 14.195)` は、旧 A5 の位置合わせに由来する
2026-08-10 初回 anchor である。以下の導出式、チェックリスト、検証値は、この初回実装を
歴史的に記録するものとして維持する。

2026-08-11 の最初の安全再校正では、全 canvas size の machine Y を一律 `-12 mm`
移し、中間 anchor `(302.019, 2.195)` を使用した。その後、右下の machine Y を
厳密に `0.0` mm へ合わせる後続再校正を行った。現在の既定 anchor は `(302.019, 0.0)`、
旧 `14.195` mm anchor 比の変化量は全 machine Y で一律 `-14.195 mm` である。現行の
紙全体の machine 範囲は次のとおりである。

| 紙              |        machine X範囲 | machine Y範囲 |      右下 anchor |
| --------------- | -------------------: | ------------: | ---------------: |
| A5 `(148, 210)` | `154.019 .. 302.019` |    `0 .. 210` | `(302.019, 0.0)` |
| A4 `(210, 297)` |  `92.019 .. 302.019` |    `0 .. 297` | `(302.019, 0.0)` |

alignment sketch の現行実描画範囲は、A5 が machine Y=`5 .. 205` mm、A4 が
machine Y=`5 .. 292` mm である。最終再校正の変更範囲と検証は後続計画
[`gcode_bottom_right_anchor_y_zero_followup_plan_2026-08-11.md`](gcode_bottom_right_anchor_y_zero_followup_plan_2026-08-11.md)
を正本とする。

## 1. 計画作成時に確認した座標系

preview/canvas は左上原点で、X は右向き、Y は下向きである。

```text
canvas top-left     = (0, 0)
canvas bottom-right = (W, H)
```

現行 G-code 変換は、X を反転せず、Y だけを反転する。

```text
machine_x = x + origin_x
machine_y = H - y + origin_y  # y_down=true
```

- X 反転は現在も過去の実装にも存在しない。
- `y_down=true` の `H-y` が、preview の Y 下向きと machine の Y 上向きの差を吸収している。
- `allow_reverse=true` は stroke の走査方向を変える travel 最適化であり、図形の鏡写しではない。
- A5 の向きが正しいため、Y 反転を削除したり、X 反転を追加したりしない。

## 2. A5 から確定した初回物理アンカー

A5 は `W=148`, `H=210`, 計画作成時の `origin=(154.019, 14.195)` で正しく出力できていた。
その canvas 右下 `(148, 210)` が対応する machine 座標は次である。

```text
anchor_x = 148 + 154.019       = 302.019
anchor_y = 210 - 210 + 14.195  = 14.195
```

したがって、2026-08-10 初回実装で設定の正本とした物理点は次の紙右下アンカーである。

```text
paper_bottom_right_mm = (302.019, 14.195)
```

## 3. 初回変更後の変換契約

任意の canvas size `(W, H)` について、canvas 右下 `(W, H)` を常に
`paper_bottom_right_mm` へ写像する。

```text
dx = x - W
dy = y - H

machine_x = anchor_x + dx
machine_y = anchor_y - dy  # y_down=true: Yだけ反転
```

2026-08-10 の初回設定では、同じ式を次のようにも表せる。

```text
machine_x = x + (302.019 - W)
machine_y = H - y + 14.195
```

代表値:

| 紙        | 実効 X offset | 右下 machine 座標 | 計画作成時との差 |
| --------- | ------------: | ----------------: | ---------------: |
| A5, W=148 |       154.019 | (302.019, 14.195) |             0 mm |
| A4, W=210 |        92.019 | (302.019, 14.195) | Xを62 mm左へ移動 |
| 任意, W   |   302.019 - W | (302.019, 14.195) | 幅差だけ左へ伸長 |

A4 の初回 anchor での四隅は次となる。

```text
top-left     = ( 92.019, 311.195)
top-right    = (302.019, 311.195)
bottom-left  = ( 92.019,  14.195)
bottom-right = (302.019,  14.195)
```

## 4. 設定/API設計

固定左下 offset を表す計画作成時の `origin` を廃止し、物理的に合わせる点を直接表す。

2026-08-10 初回実装時の設定は次である。

```yaml
export:
  gcode:
    # canvasの右下角を合わせるmachine座標 [mm]
    paper_bottom_right_mm: [302.019, 14.195]

    # previewのY下向きをmachineのY上向きへ変換する
    y_down: true
```

- `origin` と `paper_bottom_right_mm` の併存、互換 wrapper、fallback は作らない。
- X 反転設定は追加しない。現行 A5 に X 反転がなく、追加すると左右鏡像になるためである。
- `y_down` は維持し、その意味を「右下アンカーを中心とした Y 方向符号の反転」と明記する。
- `canvas_height_mm` は削除する。実際の `canvas_size.height` と異なる値は右下角の Y 固定を破り、
  現在の project/default 設定も `null` のためである。
- `canvas_size` を紙の幅・高さに関する唯一の真実とする。

## 5. 実装チェックリスト

### 5.1 canonical GCodeParams

- [x] `/Users/tyhts0829/Documents/Grafix/src/grafix/core/gcode_params.py`
  - [x] `origin` を削除し、有限実数ペア `paper_bottom_right_mm` を追加する。
  - [x] 2026-08-10 初回既定値を `(302.019, 14.195)` とする。
  - [x] `canvas_height_mm` を削除する。
  - [x] `y_down` と右下アンカーの座標契約を NumPy スタイル docstring に記載する。

### 5.2 runtime config

- [x] `/Users/tyhts0829/Documents/Grafix/src/grafix/core/runtime_config.py`
  - [x] required key/parser/GCodeParams 構築を `paper_bottom_right_mm` へ移行する。
  - [x] `origin` と `canvas_height_mm` を旧 key として受け付けない。
- [x] `/Users/tyhts0829/Documents/Grafix/src/grafix/resource/default_config.yaml`
  - [x] 新しいアンカー値と Y 反転の意味を記載する。
- [x] `/Users/tyhts0829/Documents/Grafix/.grafix/config.yaml`
  - [x] A5 から得た初回右下アンカー `(302.019, 14.195)` を設定する。
- [x] config `version: 1` は既存運用どおり据え置く。

### 5.3 G-code exporter

- [x] `/Users/tyhts0829/Documents/Grafix/src/grafix/export/gcode.py`
  - [x] `_canvas_to_machine_xy()` を右下アンカー相対の式へ変更する。
  - [x] X は符号を変えず、Y だけ `y_down` に従って符号を変える。
  - [x] `canvas_size` の W/H を両軸の配置に使用する。
  - [x] clip、量子化、bed 検証、stroke 最適化の順序は変えない。

### 5.4 呼び出しと既存テストの移行

- [x] repository 内の `GCodeParams(origin=...)` と `canvas_height_mm=...` を新契約へ移行する。
- [x] identity 変換を使う exporter test では、canvas `(W,H)` に対して
      `paper_bottom_right_mm=(W,H)`, `y_down=false` を明示する。
- [x] 旧 key/property/constructor 引数を残さない。

### 5.5 回帰テスト

- [x] `/Users/tyhts0829/Documents/Grafix/tests/export/test_gcode.py`
  - [x] A5 の既知 G-code XY が初回移行前と一致する。
  - [x] A4 の右下が A5 と同じ初回 anchor `(302.019,14.195)` になる。
  - [x] A4 の全 X 座標が計画作成時より62 mm左へ移る。
  - [x] 任意の複数 W/H で canvas 右下が常に同じ machine anchor になる。
  - [x] 非対称な四隅/点で、Xは非反転、Yだけ反転することを直接検証する。
  - [x] `paper_bottom_right_mm` の不正tuple・非有限値を拒否する。
- [x] `/Users/tyhts0829/Documents/Grafix/tests/core/test_runtime_config.py`
  - [x] default/project config の新アンカーを検証する。
  - [x] 旧 `origin` / `canvas_height_mm` を拒否する。
- [x] `/Users/tyhts0829/Documents/Grafix/tests/export/test_capture_service.py`
  - [x] capture 経路と manifest effective config に新アンカーが保持される。
- [x] interactive export worker の GCodeParams snapshot test を新 field へ移行する。
- [x] capture manifest `schema_version: 3` は据え置く。既存の `canvas_size` と
      effective config のアンカーから変換を再構成できるためである。

### 5.6 文書

- [x] `/Users/tyhts0829/Documents/Grafix/architecture.md` に canvas/machine 軸と右下アンカー契約を追記する。
- [x] 専用 migration 文書へ、旧 `origin` から右下アンカーへの換算式と破壊的変更を記載する。
- [x] 旧中央合わせ計画を実装していないことを明記した状態で維持する。

## 6. 検証

- [x] focused pytest:
  - [x] `PYTHONPATH=src pytest -q tests/export/test_gcode.py`
  - [x] `PYTHONPATH=src pytest -q tests/core/test_runtime_config.py`
  - [x] `PYTHONPATH=src pytest -q tests/export/test_capture_service.py`
  - [x] `PYTHONPATH=src pytest -q tests/interactive/runtime/test_export_job_system.py`
- [x] 対象 Python ファイルへの `ruff check`。
- [x] `mypy src/grafix`。
- [x] 既存成果物を上書きせず、一時出力で次を確認する。
  - [x] A5 alignment の G-code が現行と同じ XY bounds/座標になる。
  - [x] A4 alignment の XY bounds が X 方向だけ `-62 mm` 移動する。
  - [x] A5/A4 とも右側マーカーと下側マーカーが同じ machine 座標へ揃う。

## 7. 非ゴール

- 紙中心の固定
- X 反転の追加
- Y 反転の削除または二重反転
- 紙サイズ enum、A4/A5 専用分岐、paper preset registry
- geometry の拡大縮小
- G-code header/footer、homing、bed leveling、feed、Z、bridge、stroke ordering の変更
- 実機を自動で動かすテスト
- 既存生成済み G-code の一括再生成

## 8. 作業ツリー境界

計画作成時点で、README、fill effect、benchmark、measurement poster とその関連 test/document に
本依頼外の tracked/untracked 差分が存在する。本タスクでは、それらを編集、移動、削除、stage、
restoreしない。実装開始時に `git status --short` を再確認し、G-code 対象差分だけを扱う。

## 9. 実装・検証結果

- focused pytest: `247 passed`
- 対象 Python ファイルの Ruff: pass
- project config: `config valid`
- mypy: 実行済み。本差分外の `font_resources.py` と `effects/boolean.py` に既存4エラーが残るが、
  変更3モジュールからの新規エラーはない。
- A5 実生成の XY bounds は既存成果物と同じ
  `X=[159.019, 297.019], Y=[19.195, 219.195]`。当時の同一 frame を旧式と新式で出力した
  A5 G-code は byte-for-byte 一致。
- A4 実生成の XY bounds は
  `X=[97.019, 297.019], Y=[19.195, 306.195]`。旧 A4 の全157移動点に対し、
  差分は一律 `(-62.000, 0.000)`。
- A5/A4 新出力の右下 cross endpoint は完全一致し、Y 反転と X 非反転も四隅テストで確認済み。
