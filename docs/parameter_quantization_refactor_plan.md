# parameter_quantization_refactor_plan.md

どこで: `src/parameters/resolver.py`, `src/core/geometry.py`, `src/parameters/meta.py`, `src/primitives/*`, `src/effects/*`, `tests/parameters/*`, `spec.md`, `parameter_spec.md`, `docs/parameter_gui_impl_plan.md` ほか関連ドキュメント。  
何を: 量子化処理を 1 箇所に集約し、per-param `step` を廃止するリファクタ計画をまとめる。  
なぜ: 量子化ポイントの多重化による混乱を避け、GUI/署名/実計算で同一値を共有させるため。

## 決定事項（前提）

- per-param の `step` は廃止し、量子化幅はグローバルな既定値のみで運用する。
- 量子化は resolver 側で一度だけ行い、その値を frame_params 記録・GUI 表示・Geometry 生成に共用する。
- Geometry 側では量子化を行わず、正規化と検証のみを担当する（quantized フラグは廃止）。
- DEFAULT_QUANT_STEP は現状 1e-6 だが、未指定パラメータの ID 揺れを抑える目的で 1e-3 への引き上げを候補とする（要最終決定）。

## 改善アクションチェックリスト

- [x] **量子化責務の単一点化**: `resolve_params` でのみ量子化する実装に改修し、Geometry 側の再量子化を廃止。`_quantize` をグローバル step に固定する。
- [x] **ParamMeta / 推定ロジック整理**: `ParamMeta` から `step` を除去し、`infer_meta_from_value` も対応させる。primitive/effect の meta 定義から step 記述を削除。
- [x] **Geometry 正規化の分岐追加**: `canonicalize_args` に量子化済み入力をそのまま通すオプションを追加し、Geometry.create 呼び出し側を更新。
- [x] **整数パラメータの扱い**: int 系は量子化ではなく明示的な `int()` キャストのみで処理することを明文化・実装。override/CC から float が来ても int に確定させる。
- [x] **DEFAULT_QUANT_STEP の再設定**: 1e-3 への変更可否を決め、決めた値を `core/geometry.py` と resolver のグローバル step に同期させる。
- [x] **GUI スライダー刻み**: per-param step 廃止後の UI 刻みを定義（float は DEFAULT_QUANT_STEP、int は 1、vec は要素ごとに同値）。`docs/parameter_gui_impl_plan.md` の論点を更新。
- [x] **テスト更新**: `tests/parameters/test_resolver.py` など量子化前提のテストを刷新し、新仕様（単一点量子化・グローバル step）の期待値に合わせる。回帰テストを追加。
- [x] **ドキュメント更新**: `spec.md` 3.2、`parameter_spec.md` 9.6 など量子化説明を「グローバル step / resolver 一括」に書き換え。per-param step 言及を削除。
- [ ] **移行リスク周知**: GeometryId が変わる可能性とキャッシュ無効化の必要性を README/CHANGELOG 的な箇所に簡潔に追記。

## 補足・未決事項

- DEFAULT_QUANT_STEP は 1e-3 に設定済み。運用上問題があれば再調整する。
- 互換性は不要という前提で、Geometry.create / canonicalize_args は量子化フラグなしのシンプルなシグネチャに整理済み。
