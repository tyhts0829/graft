# fill 退化入力で落ちないようにする（A: 塗り線スキップ）

目的: `fill` が「ほぼ線/点に潰れた閉領域」を受け取ったとき、巨大なスキャンライン配列を生成して OOM しないようにする。

## 変更チェックリスト

- [x] 退化判定の基準を決める（面積がほぼ 0 のとき）
- [x] `src/grafix/core/effects/fill.py` に退化ガードを追加し、退化時は入力形状をそのまま返す（no-op）
- [x] 既存の挙動（通常形状の fill）を壊していないか確認する
- [x] `tests/core/effects/test_fill.py` に「潰れた形状でも例外にならない」テストを追加する
- [x] `PYTHONPATH=src pytest -q tests/core/effects/test_fill.py` を実行して確認する

## 確認したいこと（1 点）

- 決定: 退化時は入力形状をそのまま return（`remove_boundary` / `density` に関わらず no-op）
