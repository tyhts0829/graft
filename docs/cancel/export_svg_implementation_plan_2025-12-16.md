# どこで: `docs/memo/export_svg_implementation_plan_2025-12-16.md`。
# 何を: `src/export/svg.py:export_svg()` の実装計画（チェックリスト）をまとめる。
# なぜ: core/export/interactive の依存方向を保ったまま、SVG 出力の “本実装” を安全に進めるため。

# export_svg 実装計画（2025-12-16）

## 前提（現在地）

- 入口: `src/api/export.py:Export` が `src/export/svg.py:export_svg` を呼ぶ。
- 入力: `Sequence[RealizedLayer]`（`src/core/pipeline.py`）で、各要素は
  - `realized.coords: (N,3) float32`
  - `realized.offsets: (M+1,) int32`（ポリライン境界）
  - `color: (r,g,b)`（0..1 float）
  - `thickness: float`（ワールド単位）
- `src/export` は headless（`pyglet/moderngl/imgui` を import しない）。
- 出力機能は未実装のため、現状 `export_svg` は `NotImplementedError`。

## ゴール（成功条件）

- `export_svg(layers, path, canvas_size=...) -> Path` が SVG を書き出して返す。
- 出力が決定的（同入力→同出力）で、ユニットテストで検証できる。
- 依存追加なし（標準ライブラリ中心。外部ライブラリに頼らない）。

## 設計方針

### 1) 座標系

- interactive の投影（`src/interactive/gl/utils.py:build_projection`）に合わせて
  - `(0,0)` を左上
  - `x` 右向き、`y` 下向き
  - `canvas_size=(W,H)` をそのまま SVG の `viewBox="0 0 W H"` に対応させる。
- `z` は無視する（将来 3D→2D を export 側でやるなら別タスク）。

### 2) SVG の最低限仕様（まずは stroke のみ）

- ルート:
  - `<svg xmlns="http://www.w3.org/2000/svg" width="W" height="H" viewBox="0 0 W H">`
- 各 Layer:
  - `<g id="layer-{i}">` にまとめる（`layer.name` があるなら `data-name` 等で残す）
- 各ポリライン:
  - `<path d="M x y L x y ...">` を基本にする
  - `fill="none"`, `stroke="#RRGGBB"`, `stroke-width="{thickness}"`
  - 端点/結合は見た目を安定させるため `stroke-linecap="round"`, `stroke-linejoin="round"` を既定にする

### 3) 数値フォーマット（ファイルサイズと可読性のバランス）

- 小数は固定桁（例: 3〜4 桁）で丸め、`-0.0` を `0` に正規化する。
- 文字列生成は “座標→文字列” の小関数に閉じる（将来の最適化余地を残す）。

### 4) I/O 方針

- `path` は `Path` に正規化して返す。
- 親ディレクトリは `mkdir(parents=True, exist_ok=True)` で作る（失敗は例外のまま）。
- エンコーディングは UTF-8、改行は `\n` に統一する。

## テスト計画

新規: `tests/export/test_svg.py`

- [ ] 1 Layer / 1 polyline を出力し、`<path>` が 1 個であることを確認
- [ ] offsets が複数（複数 polyline）なら `<path>` が増えることを確認
- [ ] `canvas_size` 指定時に `viewBox` と `width/height` が一致することを確認
- [ ] `stroke`（色）と `stroke-width`（太さ）が反映されることを確認
- [ ] XML として parse できることを確認（`xml.etree.ElementTree`）

## 実装チェックリスト（合意後に着手）

- [ ] 1. `src/export/svg.py` の方針確定（viewBox/座標系/単位）
- [ ] 2. offsets→polyline 反復の純粋関数を切り出し（空/短い polyline の扱いも決める）
- [ ] 3. `RealizedLayer`→SVG 要素（`<g>` + `<path>`）への変換を実装
- [ ] 4. ルート `<svg>` を組み立ててファイルへ保存
- [ ] 5. `tests/export/test_svg.py` を追加し `pytest -q` を通す
- [ ] 6. 必要なら `README.md` / `architecture.md` の export 記述を最小更新

## 非ゴール（この計画ではやらない）

- fill（塗り）、クリッピング、スタイル継承、複雑な SVG 最適化
- SVG 以外（PNG/G-code/動画）の実装
- interactive のショートカット連携（別タスク）

