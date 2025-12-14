# bugfix_param_store_duplicate_headers_plan.md

どこで: `src/parameters/key.py`（site_id 生成）と `data/output/param_store/*.json`（永続化）、および Parameter GUI（`src/app/parameter_gui/*`）。
何を: ParamStore 復元後に「polyhedron ヘッダや effect ヘッダが複製される」不具合の原因調査結果と、修正計画をまとめる。
なぜ: 永続化を有効にした運用（調整 → 保存 → コード編集 → 再起動）で GUI が増殖していき、作業性が大きく落ちるため。

## 1. 現象（報告内容の整理）

再現手順（ユーザー報告）:

1. `main.py` を起動して 1 回描画し、閉じる（`data/output/param_store/main.json` が保存される）
2. `main.py` の `draw()` を編集し、`G.polyhedron(type_index=2)` のように `type_index` を「コード側で明示指定」する
3. 再度起動すると、Parameter GUI で
   - polyhedron のヘッダが 2 つに増える
   - effect のヘッダも複製される

補足:

- これは「GUI のグルーピングが壊れている」というより、**同一スクリプト内の“同じつもりの呼び出し箇所”が別物として認識され、古い方が残り続ける**挙動に見える。

## 2. 調査（コード上の根拠）

### 2.1 GUI 側は「(op, ordinal)」や「chain_id」でグループを作る

- Primitive のグループ境界は `(op, ordinal)`（`src/app/parameter_gui/grouping.py`）。
  - ordinal は ParamStore の「op ごとの site_id 初出順」で採番（`src/parameters/store.py`）。
- Effect のグループ境界は `chain_id`（`src/app/parameter_gui/grouping.py`）。
  - chain_id は EffectBuilder 生成時の site_id（`src/api/effects.py`）。

つまり、**site_id が変わると別グループになり、ヘッダは増える**。

### 2.2 site_id が「コード編集で変わりやすい」形式になっている

site_id 生成は `src/parameters/key.py` にあり、**修正前**は以下（コメント含む）:

- `make_site_id()` の形式: `"{filename}:{co_firstlineno}:{f_lasti}"`
- `caller_site_id()` はユーザー側の呼び出しフレームまで遡って `make_site_id()` を呼ぶ

ここで問題になるのが `f_lasti`（最後に実行したバイトコードオフセット）:

- `G.polyhedron()` → `G.polyhedron(type_index=2)` のような**同一行・同一呼び出し箇所の編集でも**、
  コンパイル結果のバイトコードが変わり `f_lasti` が変わり得る。
- 結果として site_id が変わり、ParamStore 上は「別の呼び出し箇所」として新規キー扱いになる。
- 永続化により旧キーが残っているため、GUI では旧グループ＋新グループの両方が表示され、ヘッダが複製される。

この構造は primitive/effect の両方で成立するため、「polyhedron も effect も増える」説明が付く。

※ 現行実装の site_id 形式は `"{filename}:{co_firstlineno}:{f_lasti}"`。

## 3. 根本原因（結論）

ParamStore 永続化そのものではなく、**site_id が `f_lasti` 依存で不安定**なことが原因。

- 永続化なし: 実行ごとに store が空になるので “増殖” は目立たない
- 永続化あり: 編集のたびに新 site_id が生まれ、古い site_id が残り続け、GUI が増殖する

## 4. 修正方針（シンプル優先）

### 4.1 目標

- 編集（引数追加/空行整理/整形）で site_id がズレても、GUI が増殖しないこと。
- 可能な範囲で GUI 調整値（override/ui_value/ui_min/ui_max/cc_key）を新しい呼び出し箇所へ引き継ぐこと。
- 曖昧な場合は誤マッチしないこと（初期化の方が優先）。

### 4.2 採用案（site_id を信仰しない）

site_id 安定化は「空行編集」「呼び出し順の変更」などで別の揺れが残り、やりたいことに対して大げさになりやすい。
そこで、永続化は以下の “reconcile/prune” と “GUI 側の隠蔽” で成立させる:

- load 直後に “ロード済みグループ集合” を保持する
- 実行中に “観測済みグループ集合” を集める
- 旧だけ残っているグループ（stale）と新だけ現れたグループ（fresh）を比較し、fingerprint で再リンクする
  - `label`（任意）と `arg/kind` の一致でスコアリング
  - 同点首位が複数なら移行しない
- GUI 表示では「増殖の原因になる stale」を隠す（その場で増殖しない）
- 保存時に stale を削除する（ファイル肥大化しない）

実装の要点:

- `ParamStore.snapshot_for_gui()` を GUI 側で使用し、stale を隠す
- `save_param_store()` 保存前に `ParamStore.prune_stale_loaded_groups()` を呼び、stale を削除する

詳細な実装計画/チェックリストは `docs/param_store_reconcile_and_prune_plan.md` に切り出す（最新はこちら）。

## 5. 既存の保存ファイルについて

手動削除は不要（復元時に reconcile/prune するため）。

## 6. 修正チェックリスト（実装フェーズ）

- [x] 根本原因の整理（site_id 揺れ + 永続化で増殖）
- [x] reconcile/prune の方針を確定し、別ドキュメントへ分離（`docs/param_store_reconcile_and_prune_plan.md`）
- [x] GUI 表示で stale を隠す（増殖を見せない）
- [x] 保存時に stale を削除する（ファイル肥大化しない）
- [x] pytest を追加（再リンク/隠蔽/削除の最小回帰）
- [ ] 手動スモーク（`main.py` を編集しながら起動/終了を繰り返して増殖しないこと）

## 7. 追加で確認したい点（任意）

- `main.py` 以外（`sketch/*.py`）でも同様に増殖が止まるか
- effect chain の chain_id 変更（E の宣言箇所移動）でも増殖しないか
