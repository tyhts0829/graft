# どこで: `docs/memo/export_svg_implementation_plan_2025-12-16_v2.md`。

# 何を: `src/graft/export/svg.py:export_svg()` の実装計画（チェックリスト）をまとめる。

# なぜ: headless export（SVG）を最小実装で通し、core/export/interactive の依存境界を保ったまま反復できる状態にするため。

# export_svg 実装計画 v2（2025-12-16）

## 0. 前提（現在地）

- 入口: `src/graft/api/export.py:Export` → `src/graft/export/svg.py:export_svg`。
- 入力: `Sequence[RealizedLayer]`（`src/graft/core/pipeline.py`）。
  - `realized.coords: (N,3) float32`
  - `realized.offsets: (M+1,) int32`（ポリライン境界）
  - `color: (r,g,b)`（0..1 float）
  - `thickness: float`（interactive の shader にそのまま渡している “clip 空間” 相当の値）
- 制約: `src/graft/export` は headless（`src/graft/interactive` / `pyglet` / `moderngl` / `imgui` を import しない）。
- 現状: `src/graft/export/svg.py` は実装済み（2025-12-16）。

## 1. ゴール（成功条件）

- `export_svg(layers, path, canvas_size=...) -> Path` が **妥当な SVG** を書き出して返す。
- 出力が **決定的**（同入力 → 同出力）で、ユニットテストで検証できる。
- 依存追加なし（標準ライブラリ + 既存の core だけ）。

## 2. 最小仕様（今回ここまで）

### 2.1 座標系

- interactive の投影（`src/graft/interactive/gl/utils.py:build_projection`）に合わせる。
  - 原点 `(0,0)` は左上
  - `x` 右向き、`y` 下向き
  - `z` は無視（`coords[:, :2]` のみ使用）

### 2.2 SVG ルート

- `canvas_size=(W,H)` 指定時:
  - `viewBox="0 0 W H"`、`width="W"`、`height="H"`。
- `canvas_size=None` 時:
  - 現在は例外（将来 bbox 対応を追加する想定）。

### 2.3 Path 表現

- 各 polyline を `<path>` で出力する。
  - `d="M x y L x y ..."`（polyline ごとに独立）
  - `fill="none"`
  - `stroke="#RRGGBB"`
  - `stroke-width="{stroke_width}"`
  - 既定で `stroke-linecap="round"`, `stroke-linejoin="round"`（見た目の安定性）
- `end-start < 2` の polyline はスキップ（描画不能）。

### 2.4 stroke-width（太さ）の解釈（要確認）

interactive は shader で **projection 後（clip 空間）** の座標に対して太さを適用しているため、
SVG にそのまま `thickness` を入れると細すぎる可能性が高い。

- 推奨: `stroke_width = thickness * min(W, H) / 2`
  - 例: `canvas_size=(800,800)` で `thickness=0.001` → `stroke-width=0.4`（px 相当）
- 代替: 互換より “引数の直感” を優先し、`stroke_width = thickness` とする（その場合、interactive 側も後で揃える前提）。

※この点だけは先に合意が必要（出力の見た目と API 意味が変わるため）。

### 2.5 数値フォーマット（決定的出力）

- 小数は固定桁で丸め（例: 3 桁）、`-0.0` を `0` に正規化する。
- 文字列化は小関数（例: `_fmt(x) -> str`）に閉じる。

## 3. 実装チェックリスト（2025-12-16 実施）

- [x] 1. `canvas_size=None` は例外にする（当面禁止）
- [x] 2. `stroke_width = thickness * min(W,H)/2` を採用
- [x] 3. `src/graft/export/svg.py` に純粋関数を追加
  - [x] offsets→polyline 走査（`coords[start:end]` の列挙）
  - [x] float→ 文字列（桁数・`-0` 正規化）
  - [x] RGB01→`#RRGGBB`（`src/graft/core/parameters/style.rgb01_to_rgb255` を使用）
  - [x] thickness→stroke-width（`canvas_size` を使う）
- [x] 4. SVG 文字列生成を実装し、UTF-8 で保存
  - [x] 親ディレクトリ `mkdir(parents=True, exist_ok=True)`
  - [x] 改行は `\n` に統一
- [x] 5. テストを追加: `tests/export/test_svg.py`
  - [x] 最小 1 polyline で `<path>` が生成される
  - [x] offsets が複数なら `<path>` が増える
  - [x] `canvas_size` 指定時に `viewBox` / `width` / `height` が一致する
  - [x] `stroke` / `stroke-width` が反映される（色と太さ）
  - [x] `xml.etree.ElementTree` で parse できる（妥当な XML）
- [ ] 6. 手動確認（任意）
  - [ ] 生成した `.svg` をブラウザ/Illustrator/Inkscape で開き、座標・太さが直感に合う
- [x] 7. ドキュメント更新（必要なら）
  - [x] `README.md` の “export stubs” 記述を更新（SVG のみ実装済みにする等）

## 4. 非ゴール（今回やらない）

- fill（塗り）、クリッピング、マスク、複雑な最適化（パス結合・圧縮）
- SVG 以外（PNG/G-code/動画）の実装
- 3D（z）を考慮した射影やカメラ

## 5. 事前確認したいこと（返信が欲しい）

1. `stroke_width` は推奨式 `thickness * min(W,H)/2` で進めてよい？（それとも `thickness` をそのまま？） ；はい
2. `canvas_size=None` は bbox 自動（padding 0）でよい？ それとも当面 `None` を禁止して例外にする？ ；例外
3. ルート `<svg>` の `width/height` は数値（px 相当）で固定でよい？（単位 `px` 明示の要否）；はい
