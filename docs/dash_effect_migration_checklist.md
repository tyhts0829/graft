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
  - effect は `inputs` と `args` だけで決まる純粋関数として実装する（乱数は使わない / 使うなら args に seed を含める）。

## 事前確認したいポイント（ここが決まると実装が一意になる）

1. 公開引数（案）
   - 案A（シンプル）: `dash_length: float`, `gap_length: float`, `offset: float`
   - 案B（旧寄り）: 案A + `offset_jitter: float`（線ごとの位相ゆらぎ）
   - 推奨: **案A**（まず最小で移植、必要が出たら拡張）

2. 無効値の扱い（dash/gap/offset が負、または `dash_length + gap_length <= 0`）
   - 案A: no-op（入力をそのまま返す）
   - 案B: `ValueError`
   - 推奨: **案A**（インタラクティブ用途で落としにくい）

3. offset の意味
   - 案A: 旧実装同様「パターン位相（mm）」として扱う（開始が部分ダッシュになり得る）
   - 案B: 「先頭から offset 分だけ捨ててからダッシュ開始」（部分ダッシュを作らない）
   - 推奨: **案A**（旧 dash の挙動を踏襲）

4. 「ダッシュが 1 本も生成されない」場合
   - 案A: no-op（元線を返す）
   - 案B: 空 geometry（何も描かない）
   - 推奨: **案A**（直感的で、デバッグもしやすい）

## 作業チェックリスト

- [ ] 現状整理
  - [ ] 現行 effect 実装の規約を確認（例: `src/effects/rotate.py`, `src/core/effect_registry.py`）
  - [ ] 旧 `src/effects/from_previous_project/dash.py` の機能範囲（offset/補間/多ポリライン）を棚卸し
- [ ] 仕様確定（上の「事前確認したいポイント」）
- [ ] 実装（新スタイルへ移植）
  - [ ] `src/effects/dash.py` を新規作成（`@effect(meta=...)` + `RealizedGeometry` 変換）
  - [ ] 破線化コア（各ポリラインごとに弧長→区間化→補間で切り出し）
  - [ ] no-op 条件（空入力/頂点数<2/全長<=0/無効パターン）を決めた仕様通りに統一
  - [ ] dtype/shape を `RealizedGeometry` 契約に合わせる
- [ ] 登録
  - [ ] `src/api/effects.py` に `from src.effects import dash as _effect_dash` を追加して登録されるようにする
- [ ] テスト
  - [ ] `tests/test_dash.py` を追加
    - [ ] 直線（2点）で `dash_length/gap_length` が期待セグメント数になる
    - [ ] `offset` の位相が効く（先頭が部分ダッシュになり得る）ことを固定
    - [ ] 空 geometry が no-op で落ちない
    - [ ] （任意）複数ポリライン入力で「各ポリラインごとにパターンがリセット」されることを固定
- [ ] 旧ファイルの扱い（破壊的変更を含むので要確認）
  - [ ] `src/effects/from_previous_project/dash.py` を削除する（または `docs/done/` へ退避する）
- [ ] 検証
  - [ ] `pytest -q tests/test_dash.py`
  - [ ] 影響確認として `pytest -q tests/test_scale.py tests/test_rotate.py tests/test_fill.py`

## 追加で気づいた点（今回はやらないが、必要なら次の改善候補）

- 旧 dash の「dash/gap/offset の配列パターン」対応（GUI 側の kind 拡張が必要になりそう）。
- 速度が問題なら、旧実装の 2-pass（count→fill）や Numba を後から検討（まずは素朴実装で固定）。

