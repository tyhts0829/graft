# 残り mypy エラー対応プラン（2025-12-27）

目的: `mypy src/grafix` の残りエラー（13件）を、各項目の推奨案どおりに修正して 0 件にする。

前提:
- 依存追加はしない（ネットワーク作業なし）。
- 仕様/挙動は変えず、型付け・変数/注釈の整理・明示キャストで解消する。

## チェックリスト

- [ ] `src/grafix/core/parameters/style.py`: `coerce_rgb255` の `int(object)` / `r,g,b` 未確定を、要素の型確定（cast）＋最小限の変数注釈で解消
- [ ] `src/grafix/core/realized_geometry.py`: `new_offsets: list[int]` を付ける
- [ ] `src/grafix/core/effects/partition.py`: `loops_2d` の注釈付き再定義をやめ、分岐前に型を確定
- [ ] `src/grafix/core/effects/fill.py`: `out_lines` の注釈付き再定義をやめ、型注釈を 1 箇所に寄せる
- [ ] `src/grafix/core/effects/dash.py`: `np.searchsorted` の戻り値を `int(...)` に寄せて `signedinteger` と `int` の代入衝突を解消
- [ ] `src/grafix/api/export.py`: `canvas_size=tuple(canvas_size)` をやめ、`tuple[int, int]` のまま渡す
- [ ] `src/grafix/interactive/runtime/draw_window_system.py`: `cc_snapshot` を常に `dict[int, float]` に揃える（`None` の場合は `{}` でフォールバック）
- [ ] `mypy src/grafix` を再実行し、`mypy_error_report_2025-12-27.md` を「問題箇所が無い」状態に更新

