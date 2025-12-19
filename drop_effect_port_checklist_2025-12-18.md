# drop effect 移植チェックリスト（2025-12-18）

参照（旧プロジェクト）: `src/grafix/core/effects/from_previous_project/drop.py`

## 目的

- 旧 drop エフェクト（線や面の間引き）を、新 `grafix` の effect 体系へ移植する。
- 旧仕様を基本踏襲し、座標は変更せず `offsets` の再構成で線/面を除外する。

## スコープ

- 追加: `src/grafix/core/effects/drop.py`（新実装）
- 更新: `src/grafix/api/effects.py`（import を追加して effect を登録）
- 追加: `tests/core/effects/test_drop.py`（最小限の挙動テスト）

## 非スコープ（今回やらない）

- `src/grafix/core/effects/from_previous_project/drop.py` の削除・移動（必要なら別途承認）
- `by="face"` を実際に「面単位」で判定する拡張（当面は旧実装同様に line 扱い）

## 旧 → 新の差分メモ（実装に影響する点）

- 旧: `Geometry` を直接受け取り、`__param_meta__` で UI メタを持つ。
- 新: effect は `RealizedGeometry` を受け取り、`@effect(meta=...)`（`ParamMeta`）で登録する。
- 新: `meta` に載せる引数は「default に `None` を使えない」制約がある（`effect_registry`）。

## パラメータ互換案（要確認）

すべてこれで OK

旧仕様の「`None` なら無効」を、新仕様の「None default 禁止」に合わせて sentinel で置換する。

- `interval`（旧: `int | None`）: `0` を無効（旧 `None` 相当）
- `min_length`（旧: `float | None`）: `-1.0` を無効（旧 `None` 相当）
- `max_length`（旧: `float | None`）: `-1.0` を無効（旧 `None` 相当）
- `seed`（旧: `int | None`）: `0` をデフォルト（旧 `seed=None` と同じ挙動：seed=0 を使う）
- `probability`（旧: 0.0 で無効）: そのまま踏襲
- `by`（旧: "line"/"face"）: 当面は旧同様に挙動差なし（line 扱い）
- `keep_mode`（旧: "keep"/"drop"）: そのまま踏襲

## 実装 TODO（チェックリスト）

### 仕様確定（先に確認）

- [x] 無効 sentinel を上記で採用（`interval=0`, `min/max=-1.0`）
- [x] `by="face"` は旧同様に差なし（line と同扱い）
- [x] UI レンジ（`ParamMeta.ui_min/ui_max`）は旧 `PARAM_META`（max=100/200 等）に寄せる

### 実装

- [x] `src/grafix/core/effects/drop.py` を新規作成
  - [x] モジュール docstring は「この effect が何をするか」だけを書く（`src/grafix/core/effects/AGENTS.md` 準拠）
  - [x] 他 effect モジュールへ依存しない（`util.py` 利用のみ許可）
  - [x] `drop_meta: dict[str, ParamMeta]` を定義（`choice/int/float`）
  - [x] `drop(inputs: Sequence[RealizedGeometry], *, ...) -> RealizedGeometry` を実装
  - [x] 旧ロジック踏襲:
    - [x] `cond = interval OR (length filter) OR (probability)`
    - [x] `keep_mode="drop"` は `cond=True` の線を捨てる / `"keep"` は `cond=True` の線だけ残す
    - [x] no-op（入力空/条件無効）は早期 return
    - [x] 全 drop の場合は空ジオメトリを返す
  - [x] 長さ計算ヘルパ `_compute_line_lengths(coords, offsets)` を同ファイル内に実装（旧 `_compute_line_lengths` 相当）

### 登録（API）

- [x] `src/grafix/api/effects.py` に `from grafix.core.effects import drop as _effect_drop` を追加

### テスト

- [x] `tests/core/effects/test_drop.py` を追加
  - [x] `interval` と `offset` の挙動（`keep_mode="drop"` / `"keep"`）
  - [x] `min_length` / `max_length` の閾値挙動（長さ 0/短い/長いの混在）
  - [x] `probability` + `seed` の決定性（同 seed で同結果になる）
  - [x] 全 drop で空ジオメトリ（`coords.shape==(0,3)`, `offsets==[0]`）になる

### 検証（対象限定で実行）

- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_drop.py`
- [ ] `ruff check src/grafix/core/effects/drop.py tests/core/effects/test_drop.py`（環境に ruff が無く未実行）
- [x] `mypy src/grafix/core/effects/drop.py`
