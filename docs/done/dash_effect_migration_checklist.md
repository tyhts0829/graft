# どこで: `docs/dash_effect_migration_checklist.md`。

# 何を: `src/effects/from_previous_project/dash.py`（旧 dash）を現行コア（RealizedGeometry/effect_registry/ParamMeta）へ移植するためのチェックリスト。

# なぜ: 旧プロジェクト依存（`engine.*` / 旧 registry / Numba 前提）を排除し、`E.dash(...)` をこのリポジトリで利用可能にするため。

## ゴール

- `E.dash(...)` が登録済み effect として利用できる。
- `realize()` により破線化済み `RealizedGeometry`（複数ポリライン＝各ダッシュ）が得られる。
- Parameter GUI 用に `ParamMeta` が定義され、デフォルト値も観測される。
- 最小限のユニットテストで挙動が固定される。

## 仕様（今回の前提 / 現仕様に合わせる）

- 入力: `Sequence[RealizedGeometry]`（通常 1 要素）。`inputs[0]` を対象にする。
- 出力: `RealizedGeometry(coords=float32 (N,3), offsets=int32 (M+1,))`。
- パラメータ GUI:
  - 現状の `ParamMeta`/GUI は「スカラー（float/int/bool/choice/vec3/rgb）」が主。
  - 旧 dash の「dash_length/gap_length/offset を list/tuple で与えるパターン」は、現仕様の GUI とは噛み合わないため **今回はスカラーのみ**に寄せる。
- 決定性（キャッシュ整合）:
  - effect は `inputs` と `args` だけで決まる形にする（`offset_jitter` は seed=0 の決定的 RNG を使用）。

## 仕様確定（ユーザー確認済み / 旧仕様踏襲）

1. 公開引数
   - `dash_length: float`, `gap_length: float`, `offset: float`, `offset_jitter: float`
2. 無効値の扱い
   - `dash_length < 0` または `gap_length < 0` は no-op
   - `dash_length + gap_length <= 0` は no-op
   - `offset < 0` は 0 にクランプ
   - `offset_jitter <= 0` は無効
3. offset の意味
   - 「パターン位相（mm）」として扱う（開始が部分ダッシュになり得る）
4. 「ダッシュが 1 本も生成されない」場合
   - no-op（元線を返す）

## 作業チェックリスト

- [x] 現状整理
  - [x] 現行 effect 実装の規約を確認（例: `src/effects/rotate.py`, `src/core/effect_registry.py`）
  - [x] 旧 `src/effects/from_previous_project/dash.py` の機能範囲（offset/補間/多ポリライン）を棚卸し
- [x] 仕様確定（上の「仕様確定」）
- [x] 実装（新スタイルへ移植）
  - [x] `src/effects/dash.py` を新規作成（`@effect(meta=...)` + `RealizedGeometry` 変換）
  - [x] 破線化コア（各ポリラインごとに弧長 → 区間化 → 補間で切り出し）を移植
  - [x] no-op 条件（無効パターン/頂点数<2/全長<=0）を旧仕様通りに統一
  - [x] dtype/shape を `RealizedGeometry` 契約に合わせる
- [x] 登録
  - [x] `src/api/effects.py` に `from src.effects import dash as _effect_dash` を追加
- [x] テスト
  - [x] `tests/test_dash.py` を追加
    - [x] 直線（2 点）で `dash_length/gap_length` が期待セグメント数になる
    - [x] `offset` の位相が効く（先頭が部分ダッシュになり得る）ことを固定
    - [x] 無効パターンが no-op になることを固定
    - [x] 空 geometry が no-op で落ちない
    - [ ] （任意）複数ポリライン入力で「各ポリラインごとにパターンがリセット」されることを固定
- [ ] 旧ファイルの扱い（破壊的変更を含むので要確認）
  - [ ] `src/effects/from_previous_project/dash.py` を削除する（または `docs/done/` へ退避する）
- [x] 検証
  - [x] `pytest -q tests/test_dash.py`
  - [x] 影響確認として `pytest -q tests/test_scale.py tests/test_rotate.py tests/test_fill.py`

## 追加で気づいた点（今回はやらないが、必要なら次の改善候補）

- 旧 dash の「dash/gap/offset の配列パターン」対応（GUI 側の kind 拡張が必要になりそう）。
- `offset_jitter` を「決定的」ではなく「seed 指定」にしたい場合は、公開引数へ `seed` を追加するのが筋。
