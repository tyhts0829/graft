# Text Primitive（実装計画 / data/input/font 固定 / 2025-12-18）

対象（旧実装）:

- `src/grafix/core/primitives/from_previous_project/text.py`
- `src/grafix/core/primitives/from_previous_project/fonts.py`
- `src/grafix/core/primitives/from_previous_project/utils.py`

目的:

- 旧 `text.py` を基本的に踏襲しつつ、現プロジェクト（Grafix）の `primitive` 仕様（`RealizedGeometry(coords, offsets)`）で使える `text` モジュールを新規作成する。
- フォントは **OS 探索や config ではなく**、リポ内の `data/input/font/` から読む（この要件を優先）。

非目的（今回やらない）:

- カーニング / リガチャ / 文字列 shaping（旧実装と同様に非対応）。
- 塗りつぶし（面）生成。
- OS フォントディレクトリ探索、`configs/default.yaml` 等の設定読込（`fonts.py`/`utils.py` は “参考” とし、現プロジェクト要件に合わせて簡略化）。

---

## 現プロジェクト側の制約（実装前提）

- primitive 実装:
  - `@primitive(meta=...)` を付けた関数が `RealizedGeometry` を返す。
  - `coords`: `float32 (N,3)`、`offsets`: `int32 (M+1,)`（先頭 0 / 末尾 N）。
- 描画座標系:
  - `build_projection(canvas_w, canvas_h)` が **(0,0) = 左上 / Y+ 下** の射影を採用。
  - 旧実装の “フォント座標（Y+上）→ 描画座標（Y+下）” 反転は、そのまま相性が良い。
- 依存:
  - 現状 `fontTools` が環境に無い（`pyproject.toml` に未追加）。旧仕様のアウトライン取得/平坦化に必要。
  - 依存追加は Safety ルール上 Ask-first（ネットワーク）なので、実装前に承認が必要。

---

## 旧仕様（踏襲する API と挙動）

旧 `text(...)` の引数（概念）を Grafix の primitive として持つ:

- `text: str`（`\n` 区切りで複数行）
- `em_size_mm: float`（1em の高さ [mm]）
- `font: str`（**data/input/font/ から解決**。ファイル名/ステム/部分一致を許容）
- `font_index: int`（`.ttc` の subfont index）
- `text_align: str`（`left|center|right`）
- `tracking_em: float`（文字間の追加、em 比）
- `line_height: float`（行送り、em 比）
- `flatten_tol_em: float`（平坦化の近似セグメント長、em 比）

レイアウト挙動（旧実装踏襲）:

- 行幅（em）= 各文字の `advance_em` + `tracking_em` の総和（ただし末尾の tracking は引く）
- 揃え:
  - left: `x_em = 0`
  - center: `x_em = -width_em/2`
  - right: `x_em = -width_em`
- 行送り:
  - 行ごとに `y_em += line_height`（Y+下なので正方向）
- 空白 `" "`:
  - アウトラインは作らないが advance は進める（旧 `_get_char_advance_em` の例外処理を踏襲）

---

## フォント解決方針（data/input/font 固定）

参考: 旧 `fonts.py` の `glob_font_files(...): list[Path]`（再帰列挙・重複除去・安定ソート）

このリポ用に以下へ簡略化する:

- フォント探索ディレクトリ: `data/input/font/` のみ
- 対象拡張子: `(".ttf", ".otf", ".ttc")`
- 列挙:
  - `data/input/font/**` を再帰 `glob` し、`Path.resolve()` で重複除去、`sorted()` で安定順
- `font` 引数の解釈:
  1. `Path(font)` が存在する場合: そのパスを優先（相対パスは CWD 依存になるので、テスト/実行は repo root 前提）
  2. そうでなければ `data/input/font` 内のファイル名（stem + suffix）を対象に部分一致（大文字小文字無視）
  3. それでも見つからない場合はエラー（fallback を勝手に OS から拾わない）
- 既定フォント:
  - `data/input/font/SFNS.ttf` があればそれを既定にする
  - 無ければ列挙結果の先頭（安定ソート）を既定にする

（補足）旧 `utils.py` の `_find_project_root` は、相対パス解決の参考になるが、今回は `data/input/font` 固定なので **`Path(__file__).resolve()` から repo root を辿るだけ**で十分。

---

## 実装チェックリスト

### 0) 依存追加（Ask-first）

- [ ] `pyproject.toml` に `fonttools` を追加する（`dependencies`）
- [ ] `fontPens` は追加しない方針にする（`fontTools.pens.flattenPen.FlattenPen` を使う）

### 1) 新規 primitive モジュール追加

- [ ] `src/grafix/core/primitives/text.py` を新規作成
  - [ ] 先頭ヘッダ（どこで/何を/なぜ）を付与
  - [ ] `text_meta`（`ParamMeta`）を定義
    - [ ] `text`: `kind="str"`（GUI は単一行だが、`\n` を含む文字列自体は許容）
    - [ ] `font`: 最小は `kind="str"`、追加で `kind="choice"` も検討（`data/input/font` が少数なら choice でも成立）
    - [ ] `text_align`: `kind="choice", choices=("left","center","right")`
    - [ ] それ以外は旧と同様に `float/int`（UI レンジは旧 meta を参考に設定）
  - [ ] `@primitive(meta=text_meta)` で `def text(*, ...) -> RealizedGeometry` を実装

### 2) API へ露出（G.text）

- [ ] `src/grafix/api/primitives.py` に `from grafix.core.primitives import text as _primitive_text` を追加

### 3) フォント列挙・解決（旧 fonts.py 参考）

- [ ] `data/input/font` の絶対パスを組み立てるヘルパを実装
  - 例: `repo_root = Path(__file__).resolve().parents[4]` → `repo_root / "data/input/font"`
- [ ] フォントファイル列挙（再帰 / 安定ソート）を実装
- [ ] `font` 引数 → `Path` への解決を実装（上記「フォント解決方針」）
- [ ] `TTFont` キャッシュを実装（キー: `resolved_path|font_index`）

### 4) グリフ平坦化・キャッシュ（旧 text.py 踏襲）

- [ ] `FlattenPen` + `RecordingPen` を使い、曲線を線分列へ平坦化する
- [ ] グリフコマンドの LRU キャッシュを実装（キー例: `path|font_index|char|flat_seg_len_units`）
- [ ] `flatten_tol_em` → `approximateSegmentLength` 変換:
  - `units_per_em * flatten_tol_em`（旧 `seg_len_units = max(1.0, tol * units_per_em)` を踏襲）

### 5) advance 幅（旧 \_get_char_advance_em 踏襲）

- [ ] `hmtx.metrics[glyph_name][0] / unitsPerEm` を `advance_em` とする
- [ ] `" "` は `"space"` の metrics を優先し、無ければ 0.25em を fallback（旧挙動）

### 6) グリフ → ポリライン（mm / Y 反転）

- [ ] 旧 `_glyph_commands_to_vertices_mm(...)` 相当を実装
  - [ ] `moveTo/lineTo/closePath` を polyline（`(N,2)`）へ変換
  - [ ] close 時は始点を終点に追加して閉じる（旧仕様）
  - [ ] Y 反転（`y *= -1`）
  - [ ] (x_em, y_em) の em オフセット + `em_size_mm/units_per_em` でスケール
  - [ ] 出力は `float32 (N,3)`（z=0 補完）

### 7) 文字列レイアウト（複数行/揃え/トラッキング）

- [ ] `lines = text.split("\\n")`
- [ ] 各行:
  - [ ] 行幅 `width_em` を先に計算し、`text_align` で `x_em` を決定
  - [ ] 各文字を左から積み、アウトラインを追加（空白はスキップ）
  - [ ] `cur_x_em += advance_em + tracking_em`
- [ ] 次行へ: `y_em += line_height`

### 8) `RealizedGeometry(coords, offsets)` へ統合

- [ ] polyline 群（`list[np.ndarray]`）を `coords` へ連結
- [ ] `offsets` を累積長から生成
- [ ] 短すぎる polyline（<2 点）の扱いを決める（基本はスキップして offsets に入れない）

### 9) テスト（最小）

- [ ] `tests/core/test_text_primitive.py`（仮）を追加
  - [ ] `G.text(text="A", font="SFNS.ttf")` を `realize(...)` して配列不変条件（dtype/shape/offsets 整合）を検証
  - [ ] `text_align` の違いで bbox の X が変わることを検証（厳密値ではなく大小比較でよい）
  - [ ] `text="A\\nA"` で Y が増えることを検証

### 10) 仕上げ（確認コマンド）

- [ ] `ruff check .`
- [ ] `mypy src/grafix`
- [ ] `pytest -q`

---

## 追加提案（必要なら別タスク）

- Parameter GUI の multiline 対応（`imgui.input_text_multiline`）を入れると旧の “複数行テキスト入力” 体験に近づく。
- `font` を `choice` にすると UI がラジオで肥大化しやすいので、フォント数が増えるなら combobox/検索 UI が必要。
