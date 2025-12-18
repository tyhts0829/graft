# trim effect 移植チェックリスト（旧 → 新プロジェクト）

## 目的

- 旧実装 `src/grafix/core/effects/from_previous_project/trim.py` を参考にしつつ、新プロジェクト用の `src/grafix/core/effects/trim.py` を新規実装する。
- 基本方針は **旧仕様踏襲**（入出力、パラメータ、クランプ、区間抽出ロジック）とする。

## 旧仕様（踏襲する要点）

- 各ポリラインの全長に対する正規化位置 `[0,1]` を使い、指定区間だけを残す。
- 始端/終端点は弧長に基づいて補間して生成する。
- パラメータ:
  - `start_param`, `end_param`: 0..1 にクランプ（旧 `clamp01` 相当）
  - `start_param < end_param` を満たさない場合は no-op
- エッジケース:
  - 入力が空、または全頂点数が 0 の場合は no-op
  - 1 点ポリラインはそのまま保持
  - 全長 0 の線（全点同一）はそのまま保持
  - トリム後に 2 点未満になる線は捨てる
  - ただし「捨てた結果、出力が 1 本も残らない」場合は no-op（元を返す）

## 実装チェックリスト

### 1) 調査（移植先の前提確認）

- [ ] 新実装の effect I/F を確認（`@effect(meta=...)` / `inputs: Sequence[RealizedGeometry]` / `RealizedGeometry` 入出力）。
- [ ] `src/grafix/core/effects/AGENTS.md` の制約を確認（他 effect への依存禁止、冒頭 docstring 形式）。
- [ ] `RealizedGeometry(coords, offsets)` で「ポリライン列」を作る最小実装（list→concat→offsets 構築）方針を決める。

### 2) 実装（新規ファイル追加）

- [ ] `src/grafix/core/effects/trim.py` を新規追加（モジュール先頭 docstring は「この effect が何をするか」の説明のみ）。
- [ ] `trim_meta` を `ParamMeta` で定義する:
  - `start_param`: `ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)`
  - `end_param`: `ParamMeta(kind="float", ui_min=0.0, ui_max=1.0)`
- [ ] `@effect(meta=trim_meta)` を付与し、`trim(inputs, *, start_param=0.1, end_param=0.5) -> RealizedGeometry` を実装する。
- [ ] 旧 `clamp01` 相当を effect 内で実施（float 変換、NaN/inf を含む値の扱いを旧と同等にする）。
- [ ] 各 polyline 区間（`offsets[i]:offsets[i+1]`）ごとに:
  - 累積弧長 `distances` を構築
  - `start_dist = start_param * total_length`, `end_dist = end_param * total_length` を計算
  - `_interpolate_at_distance(...)` 相当で始端/終端点を補間
  - `start_dist < dist < end_dist` の頂点を採用（端点は別途追加、終端重複は `allclose` で回避）
- [ ] トリム後に 2 点未満になる線は捨てる（旧仕様）。
- [ ] 結果が 0 本なら no-op として元を返す（旧仕様）。
- [ ] 出力 `RealizedGeometry` を構築（coords=float32, offsets=int32）。

### 3) 統合（登録）

- [ ] `src/grafix/api/effects.py` に `trim` の import を追加し、`E.trim(...)` で呼べるようにする。

### 4) テスト（最小セット）

- [ ] `tests/core/effects/test_trim.py` を追加する。
- [ ] 直線 2 点（例: `(0,0,0)->(10,0,0)`）で `start_param=0.25,end_param=0.75` のとき、端点が `(2.5,0,0)` と `(7.5,0,0)` になる。
- [ ] `start_param=0.0,end_param=1.0` は「ほぼ noop」（coords/offsets が一致、または等価）になる。
- [ ] `start_param>=end_param` は no-op（元を返す）。
- [ ] `start_param<0` / `end_param>1` はクランプされ、例外なく動く。
- [ ] 複数ポリライン入力でも各線が独立にトリムされる（offsets が正しい）。
- [ ] 全線が 2 点未満になって消える条件では no-op（元を返す）になる（旧仕様確認）。

### 5) ローカル検証コマンド（対象限定）

- [ ] `ruff check src/grafix/core/effects/trim.py tests/core/effects/test_trim.py`
- [ ] `mypy src/grafix/core/effects/trim.py`
- [ ] `PYTHONPATH=src pytest -q tests/core/effects/test_trim.py`

## 事前に確認したい点（あなたの返答待ち）

- [ ] `trim` を API として公開してよい？（= `src/grafix/api/effects.py` へ import 追加）
- [ ] `start_param >= end_param` や「全線が消える」ケースは、旧仕様どおり no-op（元を返す）でよい？
- [ ] トリム後に 1 点だけ残る線は、旧仕様どおり捨てるでよい？（1 点ポリラインとして残す案もあり）
