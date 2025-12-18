# Text Primitive（実装計画 / 2025-12-18）

対象: `src/graft/core/primitives/from_previous_project/text.py`（旧プロジェクト実装）を参考に、現プロジェクト（Graft）の primitive として利用できる `text` モジュールを新規作成する。

目的:

- フォントアウトラインから **ポリライン列（`coords + offsets`）** を生成し、Graft の `G.text(...)` として扱えるようにする。
- 旧実装の仕様（文字間/行送り/揃え/平坦化）を基本的に踏襲する。

非目的（今回やらない）:

- カーニング、リガチャ、複雑スクリプト（アラビア語等）の shaping（旧実装も非対応）。
- 輪郭の塗りつぶし（面）生成（旧実装はアウトライン=線）。
- GUI の高度なフォント検索 UI（現状の Parameter GUI には機能が足りないため、必要なら別タスク化）。

---

## 現状整理（現プロジェクト側の前提）

- primitive は `@primitive(meta=...)` で登録し、評価結果は `RealizedGeometry(coords, offsets)` を返す。
  - `coords`: `float32 (N,3)`、`offsets`: `int32 (M+1,)`（先頭 0 / 末尾 N）。
- 描画の投影は `build_projection(canvas_width, canvas_height)` で **原点左上・Y+下** の座標系を前提としている。
  - 旧 `text.py` はフォント座標（Y+上）を反転して Y+下へ揃えているため、整合が良い。
- Parameter GUI:
  - `ParamMeta(kind="str")` は `imgui.input_text`（単一行）であり、旧実装の「複数行入力 UI」相当は現状未対応。
  - `ParamMeta(kind="choice")` はラジオボタン実装で、フォント候補が大量になると UI 的に破綻する。

---

## 旧実装から踏襲したい仕様（要点）

- API（旧 `@shape def text(...)`）の概念:
  - `text: str`（`\n` 区切りで複数行）
  - `em_size_mm: float`（1em の高さ[mm]）
  - `font: str`（部分一致 or パス）
  - `font_index: int`（`.ttc` の subfont）
  - `text_align: left|center|right`
  - `tracking_em: float`（文字間追加）
  - `line_height: float`（行送り）
  - `flatten_tol_em: float`（平坦化セグメント長を em 比で指定）
- 実装方針:
  - フォント読込のキャッシュ（TTFont のキャッシュ）
  - グリフ平坦化結果の LRU キャッシュ（同一 `char/font/flat` を使い回す）
  - advance 幅は `hmtx` から取得（空白は例外扱いを踏襲）
  - Y 軸反転して「描画座標（Y+下）」へ合わせる

---

## 事前確認が必要な論点（私に確認してください）

1) 依存追加（Ask-first 対象）
- この primitive を旧仕様通りに実装するには `fontTools` が必要です（現状 `pyproject.toml` に未追加）。
- 平坦化に旧実装は `fontPens.flattenPen.FlattenPen` を使っていますが、可能なら `fontTools` 側の FlattenPen を使い **依存を 1 つに減らす** 方針にしたいです。
  - 方針 A: `fonttools` のみ追加（理想）
  - 方針 B: `fonttools` + `fontPens` を追加（旧実装完全踏襲）

2) `font` パラメータの決定性（キャッシュ整合）
- 旧仕様の「部分一致で OS フォント探索」は環境差で結果が変わりえます（同じ GeometryId でも別形状になりうる）。
- 現プロジェクトのキャッシュ設計（GeometryId=内容署名）と相性を良くするため、既定は **リポ内の `data/input/font/SFNS.ttf` を参照する** 方針に寄せたいです。
  - 方針 A: `font` は “パス推奨 / 名前は補助” として旧仕様も残す（探索は決定的に: 安定ソート→先頭採用）
  - 方針 B: `font` は “パスのみ” に制限（最も決定的だが旧仕様から逸れる）

3) Parameter GUI の範囲
- 旧実装の `text` は GUI で multiline 入力ができましたが、現状の GUI は単一行です。
  - 方針 A: まず primitive 実装のみ（`\n` はコードから渡せば動く）
  - 方針 B: GUI も拡張して multiline 入力（`input_text_multiline` 等）を追加する

---

## 実装チェックリスト（新規モジュール作成）

### 1) 仕様確定（上の論点の選択）

- [ ] 依存追加方針（`fonttools` 単独 or `fontPens` 追加）を確定する
- [ ] `font` の扱い（パス必須か、部分一致探索も残すか）を確定する
- [ ] GUI の multiline 対応を今回やるか確定する

### 2) primitive 追加（Graft 仕様へ適合）

- [ ] `src/graft/core/primitives/text.py` を新規作成する
  - [ ] 先頭ヘッダ（どこで/何を/なぜ）を付ける
  - [ ] `@primitive(meta=text_meta)` で `def text(*, ...) -> RealizedGeometry` を定義する
  - [ ] `ParamMeta` を用意する（最低限: `text/ em_size_mm/ font/ font_index/ text_align/ tracking_em/ line_height/ flatten_tol_em`）
- [ ] `src/graft/api/primitives.py` に `from graft.core.primitives import text as _primitive_text` を追加し、`G.text(...)` を有効化する

### 3) フォント解決・キャッシュ（旧仕様を基本踏襲）

- [ ] フォントローダ（旧 `TextRenderer.get_font(...)` 相当）を実装する
  - [ ] “パスならそれを優先” を踏襲
  - [ ] “名前なら探索” を採用する場合は探索範囲を決める（例: `data/input/font/` + OS 既定ディレクトリ）
  - [ ] TTFont キャッシュキーは `(font, font_index)` 相当にする
- [ ] グリフコマンドの LRU キャッシュ（`char/font/font_index/flatten`）を実装する

### 4) グリフ→ポリライン変換（mm / Y+下）

- [ ] flatten（曲線→線分列）で `moveTo/lineTo/closePath` のみが来る前提を作る
- [ ] 旧 `_glyph_commands_to_vertices_mm(...)` 相当を実装する
  - [ ] close 時に始点を終点に追加して閉じる（旧仕様踏襲）
  - [ ] Y 軸反転
  - [ ] `x_em/y_em` と `em_size_mm` による平行移動 + 一様スケール

### 5) 文字列レイアウト（旧仕様踏襲）

- [ ] 複数行: `text.split(\"\\n\")`
- [ ] 行幅: `advance_em + tracking_em` を積み、末尾 tracking は除外
- [ ] 行揃え: `left|center|right` で `x_em` を決定
- [ ] 行送り: `y_em += line_height`（Y+下なので正方向）
- [ ] 空白はアウトラインを生成しないが advance は進める

### 6) `RealizedGeometry(coords, offsets)` へ変換

- [ ] “複数ポリライン（list[np.ndarray]）” を 1 本の `coords` に連結し、`offsets` を生成する
  - [ ] polyline が短すぎる（<2 点）場合の扱いを決める（旧 `Geometry.from_lines` 相当の挙動に合わせる）
- [ ] dtype を `float32/int32` に揃える（`RealizedGeometry` が補正するが、生成側も揃える）

### 7) テスト（最小）

- [ ] `tests/.../test_text_primitive.py` を追加する（配置は既存テスト構成に合わせて決める）
  - [ ] `G.text(text=\"A\", font=\"data/input/font/SFNS.ttf\")` を realize して、`coords/offsets` の不変条件を検証する
  - [ ] `text_align` の違いで bbox の X がシフトすることを検証する（大まかな比較で良い）
  - [ ] `text=\"A\\nA\"` で Y が増えることを検証する

### 8) 仕上げ

- [ ] `ruff check .` / `mypy src/graft` / `pytest -q`（必要なら対象限定で実行）で整合を確認する
- [ ] `README.md` または `spec.md` に “text primitive 追加” を短く追記する（必要なら）

---

## リスク / 注意点

- 依存追加が必要（ネットワーク許可が必要になる見込み）。
- OS フォント探索を入れる場合、結果が環境依存になる（決定性とトレードオフ）。
- GUI での multiline 入力は現状非対応（実装範囲を分けるのが安全）。

