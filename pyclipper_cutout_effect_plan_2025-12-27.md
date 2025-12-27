# pyclipper cutout effect 実装計画（2025-12-27）

目的: `pyclipper` を使い、2 つの Geometry（A=対象, B=カッター）から **A を B で切り抜いた結果**（基本は差分 `A - B`）を生成する新規 effect を追加する。

非目的:

- 3D の非共平面ブーリアン（任意姿勢同士の真の 3D 演算）
- 既存 effect の互換ラッパー/シムの追加
- 過剰な入力修復（自動で勝手に「それっぽく」直す挙動）

## 0) 事前に決める（あなたの確認が必要）

- [ ] (0-1) 演算の意味（名前と一致させる）
  - A: `cutout` = 差分 `A - B`（B の領域を A から抜く）
  - B: `clip` = 交差 `A ∩ B`（B の領域で A を切り出す）
  - C: `mode: ("difference"|"intersection")` を 1 effect に持たせる（最小 UI 追加）
- [ ] (0-2) 対象形状の扱い
  - A: **閉ループ（リング）だけ**を「面」とみなしてブーリアンする（開いた線は無視）
  - B: B は閉ループ（面マスク）、A は開いたポリラインも含めて「線分クリップ」する（やるなら別仕様）
- [ ] (0-3) 3D 入力の前提
  - A: A/B は同一平面（ほぼ共平面）を前提。非共平面なら no-op（A を返す）
  - B: A の平面へ B を射影して処理（仕様が曖昧になりやすいので非推奨）
- [ ] (0-4) 量子化スケール（float -> int）
  - 既定 `scale=1000`（1mm を 1000 単位に）など、デフォルトと UI 範囲を決める
- [ ] (0-5) fill rule
  - A: even-odd（入力向きがバラバラでも破綻しにくい）
  - B: non-zero（向きに意味が出る）

## 1) 最小仕様（案）

- effect 名: `cutout`（※(0-1) の決定に従って最終確定）
- 入力: `inputs[0]=A(対象)`, `inputs[1]=B(カッター)`
- 出力: 差分結果の **境界ループ列**（外周+穴の輪郭をポリラインとして返す）
- no-op:
  - `inputs` が空 → 空
  - `len(inputs) < 2` または B が空 → A を返す
  - A が空 → A を返す
  - 非共平面（(0-3)A の場合）→ A を返す

## 2) 実装チェックリスト（承認後に実施）

### 2.1 依存追加

- [ ] `pyproject.toml` の `dependencies` に `pyclipper` を追加する

### 2.2 effect 実装（core）

- [ ] 新規ファイル: `src/grafix/core/effects/cutout.py`
  - 先頭 docstring は「この effect が何をするか」だけを書く（`src/grafix/core/effects/AGENTS.md` 準拠）
  - 他 effect モジュールへ依存しない（`util.py` の利用は可）
- [ ] `cutout_meta: dict[str, ParamMeta]` を定義する（最小）
  - 例: `scale: int`（ui_min/ui_max を控えめに）
  - (0-1)C の場合は `mode: choice` も追加
- [ ] `@effect(meta=cutout_meta)` で `cutout(inputs, *, ...) -> RealizedGeometry` を実装する
  - `pyclipper` はローカル import にして、未導入時は `RuntimeError("cutout effect は pyclipper が必要です")` を送出（`partition` と同じ方針）
  - A/B の全点を同一変換で 2D 化して pyclipper に渡す
    - 候補 1（推奨）: A の姿勢から `rotation_matrix, z_offset` を決め、B にも同じ変換を適用する
    - 候補 2: A+B の点群から平面基底（PCA 等）を推定して両者を射影する（実装量増）
  - float->int 量子化: `xy_int = round(xy_float * scale)`（`int64`）に統一
  - 入力リング正規化:
    - 3 点未満は捨てる
    - 端点が同一点なら重複末尾を落とす（pyclipper には非重複で渡す）
- [ ] pyclipper 実行
  - `CT_DIFFERENCE`（or (0-1) により `CT_INTERSECTION`）
  - fill rule は (0-5) の決定を反映
  - 返ってきた `paths`（外周/穴）を float へ戻して 3D へ復元し、`RealizedGeometry(coords, offsets)` を組み立てる

### 2.3 API 登録（E から使えるように）

- [ ] `src/grafix/api/effects.py` に `cutout` の import 登録を追加する（他 effect と同様）

### 2.4 「2 Geometry 入力」をどう API で渡すか（どれか 1 つに決めて実装）

- [ ] 案 A（推奨・最小）: `EffectBuilder.__call__` を「複数 Geometry 対応」にする
  - 使い方: `E.cutout(...)(a, b)` / `E.cutout(...).scale(...)(a, b)`
  - 仕様: 最初の step は `inputs=(a,b,...)` を使い、2 step 目以降は従来通り `inputs=(result,)`
- [ ] 案 B: `B` のような別名前空間（合成/ブーリアン）を新設し、`B.cutout(a, b, **params) -> Geometry`
  - effect 自体は `grafix.core.effects.cutout` を使う（DAG ノードの inputs を 2 個で作る）
- [ ] 案 C: `E.cutout(clip=<Geometry>)` のような **特殊 kwarg** を API 層で吸収し、Geometry inputs を 2 個で作る
  - スタブ生成（`tools/gen_g_stubs.py`）の特別対応が必要になりやすい

### 2.5 スタブ同期

- [ ] `tools/gen_g_stubs.py` を更新し、`src/grafix/api/__init__.pyi` を再生成する
  - `cutout` が `_EffectBuilder` に追加されること
  - (2.4)案 A の場合は `_EffectBuilder.__call__` のシグネチャも更新する
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py` を通す

### 2.6 テスト（最小）

- [ ] `tests/core/effects/test_cutout.py` を追加する
  - [ ] 差分: 外周 1 + 穴 1 が出る（例: 大きい正方形 - 小さい正方形）
  - [ ] 交差しない B は no-op（A と同じ）
  - [ ] B が空なら no-op（A と同じ）
  - [ ] (0-3)A の場合: 非共平面入力は no-op（A と同じ）
  - 期待値は「点列完全一致」ではなく bbox/本数/閉ループ性など **壊れにくい条件**でチェックする

### 2.7 最小品質ゲート（対象限定）

- [ ] `ruff check src/grafix/core/effects/cutout.py src/grafix/api/effects.py tests/core/effects/test_cutout.py`
- [ ] `mypy src/grafix`（難しければ少なくとも追加ファイル周辺）
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_cutout.py`

## 3) 追加で気づいた点（実装中に追記）

- [ ] pyclipper の結果パス順序・向きが不安定な場合、テストは「順序不問」で評価する（ループを bbox/重心でソートしてから比較）
- [ ] `scale` のデフォルトは他 effect の単位感（mm）と合わせる（過剰に大きい桁にしない）

