# pyclipper clip_xy effect 実装計画（2025-12-27）

目的: `pyclipper` を使い、2 つの Geometry（A=被切り抜き, B=閉曲線マスク）から **A を B の内側（または外側）領域に切り抜く**新規 effect を追加する。処理は **両入力を同一の姿勢変換で XY 平面へ整列**して 2D で行い、結果を **逆変換して姿勢復元**する。

参考: `fill` effect の「2D 化して処理し、必要なら姿勢を戻す」方針（`grafix.core.effects.util.transform_to_xy_plane/transform_back`）。

非目的:

- 3D ブーリアン（任意姿勢の真の 3D 演算）
- B（マスク）の「閉じていない入力」を過剰に修復する挙動
- 互換ラッパー/シムの追加

## 0) 前提（確定）

- 入力:
  - `inputs[0] = A`（被切り抜き geometry。開いたポリラインを含む想定）
  - `inputs[1] = B`（閉曲線 geometry。リングとして扱う）
- 処理:
  - B（マスク）の代表リングから「XY 平面へ整列する剛体変換」を決める
  - A/B をその変換で XY 平面へ整列し、pyclipper の整数座標で計算する
  - 結果を逆変換して元の姿勢へ戻す
- 結果:
  - A のポリラインを **B の内側**（または **外側**）の部分だけ残したポリライン列
  - 出力は「元の座標系に戻した」`RealizedGeometry`（マスク平面上に乗る）

## 1) 仕様の決定事項（あなたの確認が必要）

- [ ] (1-1) effect 名
  - A: `clip_xy`（本仕様が明確）；こちらで
  - B: `clip`（短いが将来の「任意平面 clip」と紛れやすい）
- [ ] (1-2) 内側/外側の指定方法（UI）
  - A: `mode: choice ("inside"|"outside")`（推奨）；こちらで
  - B: `invert: bool`（シンプル）
- [ ] (1-3) マスク B の複数リングの解釈
  - A: even-odd（`fill` と同様に「ネストが穴になる」扱い）；こちらで
  - B: union 扱い（穴は考えない）
- [ ] (1-4) 量子化スケール（float -> int）
  - 既定 `scale=1000`（mm を想定し、1e-3 精度）；こちらで

## 2) 最小仕様（案）

- effect 名: (1-1) に従う（以下では仮に `clip_xy` と呼ぶ）
- パラメータ（最小）:
  - `mode` または `invert`
  - `scale: int`（pyclipper 用）
- no-op / 例外方針:
  - `inputs` が空 → 空
  - `len(inputs) < 2` → A を返す（=入力そのまま）
  - A が空 → A を返す
  - B に「有効な閉リング」が 1 つも無い → A を返す
  - `pyclipper` 未導入 → `RuntimeError("clip_xy effect は pyclipper が必要です")`

## 3) 実装チェックリスト（承認後に実施）

### 3.1 依存追加

- [ ] `pyproject.toml` の `dependencies` に `pyclipper` を追加する

### 3.2 effect 実装（core）

- [ ] 新規ファイル: `src/grafix/core/effects/clip_xy.py`
  - 先頭 docstring は「効果の説明」だけを書く（`src/grafix/core/effects/AGENTS.md` 準拠）
  - 他 effect モジュールへ依存しない（`util.py` の利用は可）
- [ ] `clip_xy_meta: dict[str, ParamMeta]` を定義する
  - `mode: choice`（または `invert: bool`）
  - `scale: int`
- [ ] `@effect(meta=clip_xy_meta)` で `clip_xy(inputs, *, ...) -> RealizedGeometry` を実装する
  - `pyclipper` はローカル import（未使用時の import 回避）
  - 姿勢変換（推奨）:
    - B の最初の有効リングを代表として `transform_to_xy_plane` を呼び、`rotation_matrix, z_offset` を得る
    - A/B の全頂点に同じ変換を適用して XY 平面へ整列する（z は 0 近傍のはず）
    - 非共平面っぽい場合（整列後の |z| が閾値を超える等）は no-op（A を返す）
  - A のポリライン列を open path として登録（`closed=False`）
  - B のリング列を clip polygon として登録（`closed=True`）
  - fill rule は (1-3) を反映
  - 実行は open path 対応の `Execute2` + `OpenPathsFromPolyTree` を使用する（inside/outside の両方で open path が欲しいため）
  - XY 整列 → int 量子化 → clip → float 復元（z=0）→ `transform_back` で姿勢復元、で `RealizedGeometry` を組み立てる

### 3.3 API 登録（E から使えるように）

- [ ] `src/grafix/api/effects.py` に `clip_xy` の import 登録を追加する（他 effect と同様）

### 3.4 「2 Geometry 入力」を API で渡す（方針）

- [ ] `EffectBuilder.__call__` を「複数入力対応」に拡張する（推奨・最小）
  - 使い方: `E.clip_xy(...)(a, b)`（a=被切り抜き, b=マスク）
  - 仕様:
    - 1 ステップ目の `Geometry.create(...)` は `inputs=(a,b,...)`
    - 2 ステップ目以降は従来通り `inputs=(result,)`
  - 期待する落とし所:
    - unary effect に対して複数 Geometry を渡した場合の挙動は **明確に禁止**（`TypeError`）にして事故を防ぐ
      - ※判定方法は設計が必要（「最初の op が multi-input か」を表す最小情報を effect 側に持たせる等）

### 3.5 スタブ同期

- [ ] `tools/gen_g_stubs.py` を更新し、`src/grafix/api/__init__.pyi` を再生成する
  - `clip_xy` が `_EffectBuilder` に追加されること
  - `EffectBuilder.__call__` の型を更新する場合は、それも stub に反映する
- [ ] `PYTHONPATH=src pytest -q tests/stubs/test_api_stub_sync.py` を通す

### 3.6 テスト（最小）

- [ ] `tests/core/effects/test_clip_xy.py` を追加する
  - [ ] inside: `G.grid(...)` 等を `B=G.polygon(...)` の内側にクリップし、bbox が縮む
  - [ ] outside: inside と逆（bbox が元より大きくならず、かつ内側が消える）
  - [ ] B が無効（開いている/点数不足）なら no-op（A と同じ）
  - [ ] 姿勢復元: 入力を回転させたケースで、出力が `z=0` に潰れず「元の平面」に戻っていること
  - [ ] 期待値は「点列一致」ではなく、`offsets` 本数・bbox・平面性（同一平面へ乗る）などの **壊れにくい条件**で評価する

### 3.7 最小品質ゲート（対象限定）

- [ ] `ruff check src/grafix/core/effects/clip_xy.py src/grafix/api/effects.py tests/core/effects/test_clip_xy.py`
- [ ] `mypy src/grafix`（難しければ少なくとも追加ファイル周辺）
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_clip_xy.py`

## 4) 実装中に追記する観測ポイント

- [ ] pyclipper の open path 差分（outside）が期待通り動くか（`ctDifference` + `OpenPathsFromPolyTree`）
- [ ] 境界上の扱い（線がマスク境界と一致するケース）を「どちらでも OK」にできるよう、テストは許容幅を持たせる
- [ ] 姿勢変換が安定しているか（B の代表リングの選び方で結果が暴れないか）
