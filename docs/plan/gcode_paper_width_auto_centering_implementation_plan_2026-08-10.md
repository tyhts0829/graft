# G-code 任意紙幅 X 自動中央合わせ実装計画（2026-08-10）

- 状態: **取り下げ（未実装）**
- 計画作成時 branch: `main`
- 計画作成時 HEAD: `f6b52b7`
- 対象: G-code の canvas X 座標から machine X 座標への配置変換

本計画は、現在正しく中央合わせできている A5 の実機位置を基準にし、A4 を含む任意の
`canvas_size.width` で紙面中心が同じ machine X 座標へ来るよう、自動で X 座標を調整するための
実装計画である。production code、設定、テスト、文書の変更は、本計画への承認後に行う。

> 2026-08-10 追記: 紙は中心合わせではなく、右下角をプロッターボード上の同一点へ合わせる運用と
> 判明したため、本計画は実装せず取り下げる。後継計画は
> `docs/plan/gcode_bottom_right_anchor_implementation_plan_2026-08-10.md` とする。

## 1. 背景と原因

現在の設定は `origin=(154.019, 14.195)` を全紙幅で共用し、X 座標を次の式で変換する。

```text
machine_x = canvas_x + origin_x
```

この `origin_x` は A5 幅 148 mm で中央が合うよう校正された値である。紙を同じ機械 X 中心へ
置く場合、A4 幅 210 mm では幅差の半分 `(210 - 148) / 2 = 31 mm` だけ描画中心が右へ移る。

A5 の現在の紙面中心は次の machine X 座標である。

```text
154.019 + 148 / 2 = 228.019 mm
```

この `228.019 mm` が紙幅によらず維持すべき物理的な校正値である。

## 2. ゴール

- A5 幅 148 mm の G-code X 座標を現在と同じ値に保つ。
- A4 幅 210 mm では X 原点相当値を自動的に `123.019 mm` とし、現状より 31 mm 左へ補正する。
- A4/A5 に限らず、正の任意 `canvas_size.width` で紙面中央を machine `X=228.019 mm` に保つ。
- 紙幅をスケッチ側や export 呼び出し側で重複指定しない。既存の `canvas_size` を唯一の紙幅とする。
- Y 反転、Y 配置、clip、安全マージン、bed 検証、stroke 最適化、feed、Z の挙動を変えない。
- 旧 `origin` と新設定を併存させる互換 mode、alias、shim は作らない。

## 3. 変更後の座標契約

固定の左端 X offset ではなく、機械座標上の紙面中心 X を設定値として直接保持する。

```yaml
export:
  gcode:
    # 紙(canvas)の水平中心を合わせる machine X 座標 [mm]
    paper_center_x_mm: 228.019

    # 既存の Y 変換後に加える machine Y offset [mm]
    origin_y_mm: 14.195
```

変換式は次の一意な形とする。

```text
effective_origin_x = paper_center_x_mm - canvas_width / 2
machine_x = canvas_x + effective_origin_x
machine_y = (canvas_height - canvas_y) + origin_y_mm  # y_down=true
machine_y = canvas_y + origin_y_mm                    # y_down=false
```

`effective_origin_x` を先に一度だけ求めてから加算し、A5 の既存式
`canvas_x + 154.019` と量子化後の出力を一致させる。

代表値は次のとおり。

| 紙幅 | canvas 中心 X | 実効 X offset | machine 中心 X |
|---:|---:|---:|---:|
| 148 mm (A5) | 74 mm | 154.019 mm | 228.019 mm |
| 210 mm (A4) | 105 mm | 123.019 mm | 228.019 mm |
| `W` mm | `W / 2` | `228.019 - W / 2` mm | 228.019 mm |

`reference_canvas_width_mm=148` と旧 `origin_x` の組で間接計算する設計は採用しない。同じ物理点を
二つの設定値で表して不整合を作るためである。`paper_center_x_mm` を唯一の X 配置校正値とする。

## 4. 実装チェックリスト

### 4.1 GCodeParams の配置設定を単純化

- [ ] `/Users/tyhts0829/Documents/Grafix/src/grafix/core/gcode_params.py`
  - [ ] `origin: tuple[float, float]` を削除する。
  - [ ] `paper_center_x_mm: float = 228.019` を追加する。
  - [ ] `origin_y_mm: float = 14.195` を追加する。
  - [ ] 両値を有限実数として検証する。
  - [ ] NumPy スタイル docstring に machine/canvas 座標の対応を明記する。

### 4.2 runtime config を新しい物理値へ移行

- [ ] `/Users/tyhts0829/Documents/Grafix/src/grafix/core/runtime_config.py`
  - [ ] `export.gcode.origin` の読み取りを削除する。
  - [ ] `paper_center_x_mm` と `origin_y_mm` を必須の有限実数として読む。
  - [ ] `GCodeParams` 構築を新 field に統一する。
- [ ] `/Users/tyhts0829/Documents/Grafix/src/grafix/resource/default_config.yaml`
  - [ ] `origin` を新しい二つの key へ置換し、意味をコメントで説明する。
- [ ] `/Users/tyhts0829/Documents/Grafix/.grafix/config.yaml`
  - [ ] A5 の現校正値から導いた `paper_center_x_mm: 228.019` と、現 Y 値
    `origin_y_mm: 14.195` を設定する。
- [ ] capture manifest の effective config が新 key をそのまま記録することを確認する。
- [ ] config の `version: 1` と capture manifest の `schema_version: 3` は据え置く。
  effective config の key 置換は既存 schema 内の設定 snapshot 更新として扱い、旧 `origin` は
  unknown/missing key error で明示的に拒否する。

### 4.3 exporter の X 変換を紙幅連動にする

- [ ] `/Users/tyhts0829/Documents/Grafix/src/grafix/export/gcode.py`
  - [ ] `_canvas_to_machine_xy()` の X 変換へ `canvas_size[0] / 2` を用いる。
  - [ ] A5 の実効 offset が従来の `154.019` と一致することを保つ。
  - [ ] Y 変換は `origin_y_mm` への field 名移行以外は変更しない。
  - [ ] 変換順序と単位をコメント/docstringへ明記する。

### 4.4 既存呼び出しとテスト fixture を新契約へ移行

- [ ] `GCodeParams(origin=...)` を使う repository 内の production/test 呼び出しを新 field へ更新する。
- [ ] 従来の identity X 変換を必要とするテストでは、canvas 幅 `W` に対し
  `paper_center_x_mm=W/2`、`origin_y_mm=0` を明示する。
- [ ] 旧 `origin` を受け付ける wrapper、property、fallback は追加しない。

### 4.5 回帰テストを追加

- [ ] `/Users/tyhts0829/Documents/Grafix/tests/export/test_gcode.py`
  - [ ] A5 幅 148 mm で既知の X 出力が従来値と一致する。
  - [ ] A4 幅 210 mm で紙面中心が `X=228.019`、実効 offset が `123.019` になる。
  - [ ] 複数の任意幅を parameterize し、`canvas_x=width/2` が常に
    `machine_x=paper_center_x_mm` になる。
  - [ ] 左右に同距離の点が machine 上でも中心に対して対称になる。
  - [ ] `paper_center_x_mm` / `origin_y_mm` の非有限値を拒否する。
  - [ ] Y 反転と `canvas_height_mm` の既存挙動が変わらない。
- [ ] `/Users/tyhts0829/Documents/Grafix/tests/core/test_runtime_config.py`
  - [ ] 同梱 default と project config が新 key を正しく構築する。
- [ ] `/Users/tyhts0829/Documents/Grafix/tests/export/test_capture_service.py`
  - [ ] capture 経路でも frame の `canvas_size.width` が同じ中央合わせへ使われる。
  - [ ] manifest の effective config に新配置値が保存される。
- [ ] 必要な interactive export worker テストを新 field 名へ更新し、snapshot した
  `GCodeParams` が worker でも同じ結果になることを維持する。

### 4.6 設計・移行文書を同期

- [ ] `/Users/tyhts0829/Documents/Grafix/architecture.md` に G-code の紙配置契約を追記する。
- [ ] 新しい専用 migration 文書に、`origin` から
  `paper_center_x_mm` / `origin_y_mm` への破壊的設定変更と換算式を記載する。
- [ ] GCodeParams の公開 docstring、設定コメント、実装コメントで用語を統一する。

## 5. 検証

- [ ] focused pytest:
  - [ ] `PYTHONPATH=src pytest -q tests/export/test_gcode.py`
  - [ ] `PYTHONPATH=src pytest -q tests/core/test_runtime_config.py`
  - [ ] `PYTHONPATH=src pytest -q tests/export/test_capture_service.py`
  - [ ] `PYTHONPATH=src pytest -q tests/interactive/runtime/test_export_job_system.py`
- [ ] 対象 Python ファイルへの `ruff check`。
- [ ] `mypy src/grafix`。
- [ ] 一時出力で A5/A4 alignment G-code の XY bounds を読み、中心 X が両方
  `228.019 mm` であることを確認する。既存成果物は上書きしない。

## 6. 非ゴール

- Y 方向の紙サイズ連動または縦中央合わせ
- 紙サイズ enum、A4/A5 専用分岐、用紙 preset registry
- bed range から中心位置を推測する処理
- 紙幅に合わせた geometry の拡大縮小
- G-code header/footer、homing、bed leveling、feed、Z、bridge、stroke ordering の変更
- 実機を動かす自動テスト
- 既存生成済み G-code の一括再生成

## 7. 作業ツリー境界

計画作成時点で、本依頼外の削除差分
`docs/plan/fill_angle_hatch_count_stabilization_implementation_plan_2026-08-10.md` と、未追跡ファイル
`docs/plan/fill_size_independent_density_spacing_implementation_plan_2026-08-10.md` が存在する。
本タスクでは、それらを編集、移動、削除、stage、restoreしない。実装開始時に改めて
`git status --short` を確認し、上記 G-code 対象差分だけを扱う。
