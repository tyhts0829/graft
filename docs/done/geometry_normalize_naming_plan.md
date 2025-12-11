# geometry_normalize_naming_plan.md

どこで: `src/core/geometry.py`、`docs/*`（正規化関数名の言及箇所）。  
何を: 正規化系の名前を normalize に統一する。  
なぜ: normalize の方が一般的で理解しやすいため。

## 改善アクションチェックリスト

- [x] 値正規化関数を `_normalize_value` にリネームし内部呼び出しを更新。
- [x] 引数正規化関数を `normalize_args` にリネームし `Geometry.create` など呼び出しを更新。
- [x] 署名関連 docstring やコメントの文言を normalize ベースに揃える。
- [x] `docs/` 内で関数名に言及している箇所を normalize に置換し整合性を取る。
- [x] 影響範囲の簡易確認（`rg` で旧名称の残存なしを確認）。

## 補足・懸念

- 外部 API としての利用は想定していないため互換ラッパーは不要。
