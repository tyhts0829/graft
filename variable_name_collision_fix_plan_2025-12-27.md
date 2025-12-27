# 変数名衝突 解消プラン（2025-12-27）

目的: mypy エラーの原因になっている「同一スコープ内で別用途の型を同名変数に再代入する」箇所を、変数名の整理だけで解消する（挙動は変えない）。

対象（mypy の該当エラー）:
- `src/grafix/core/primitives/sphere.py`
- `src/grafix/core/effects/mirror.py`
- `src/grafix/interactive/parameter_gui/store_bridge.py`
- `src/grafix/core/realize.py`

## チェックリスト

- [x] `sphere.py`: `x/y/z` のスカラー/配列の再利用を解消（例: `x_pos` / `xs`、`y_pos` / `ys`、`z_pos` / `zs` へ分離）
- [x] `mirror.py`: `base`（RealizedGeometry）と別用途の `base`（int）を分離、`ln` の int/ndarray 再利用も解消
- [x] `store_bridge.py`: `group_key` の型衝突を解消（primitive/effect/other で変数名を分ける）
- [x] `realize.py`: primitive/effect の callable を同一変数 `func` に入れない（`primitive_func` / `effect_func` に分離）
- [x] mypy で上記4ファイル起因のエラーが消えていることを確認（`mypy src/grafix`）

## 事前確認したほうがよいこと（あれば追記）

- 今回は「変数名整理のみ」で、処理フロー・アルゴリズム・返り値の型は変えない方針で進める。

## 実施結果

- `mypy src/grafix` のエラー数: 28 → 13（この作業で「変数名衝突」起因のものが解消）
