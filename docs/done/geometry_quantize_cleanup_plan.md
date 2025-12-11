# geometry_quantize_cleanup_plan.md

どこで: `src/core/geometry.py`, `src/parameters/resolver.py`, `src/api/*.py`, `tests/*`, `docs`。  
何を: Geometry 側の量子化ロジックを削除し、全量子化を resolver に一本化するクリーンアップ。`quantized` フラグも不要化する。  
なぜ: 量子化の責務を 1 箇所に限定し、不要な実装・分岐を排除してシンプルにするため。

## 方針
- Geometry は「正規化と検証のみ」を担当し、量子化は一切行わない。
- 量子化は resolver の `_quantize` だけで実施し、すべての呼び出し経路でそれを通す（直接 Geometry.create を呼ぶ箇所も resolver または新しいヘルパを介す）。
- `quantized` フラグは削除し、Geometry.create / normalize_args は常に非量子化入力を前提とする（ただし量子化処理自体は持たない）。
- NaN/inf などの検証は Geometry 側に残す（署名用正規化として必要）。

## アクションチェックリスト
- [x] Geometry 側の量子化コード削除: `_quantize_float` と `quantized` 分岐を削り、normalize は純粋な正規化＋検証のみにする。
- [x] API シグネチャ整理: Geometry.create / normalize_args から `quantized` 引数を削除し、呼び出し箇所をすべて更新。
- [ ] 直接 Geometry.create を呼んでいる箇所の修正: `src/api/layers.py` やテスト・ツール類で、必要なら簡易ヘルパ（resolver を通す or 量子化なしでそのまま使う方針を明記）に置き換え。
- [x] resolver の `_quantize` を「唯一の量子化」だと明示するコメント／docstring 追加。`quantized` フラグ関連のコードを削除。
- [x] ドキュメント更新: `spec.md`/`parameter_spec.md`/`docs/done/parameter_gui_plan.md` から Geometry 側の量子化言及を除去し、「量子化は resolver のみ」に統一。
- [ ] テスト更新: Geometry 直呼びテストがあれば期待値を調整。量子化を期待する箇所は resolver 経由に切り替える。回帰テストを再実行。

## 留意点
- 量子化を経由させない Geometry.create 呼び出しが残っていると、ID 揺れが発生する可能性があるので、呼び出し経路の棚卸しを必ず行う。
- NaN/inf リジェクトなどの防御的検証は Geometry に残すことで安全性は維持する。
