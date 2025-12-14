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

site_id 生成は `src/parameters/key.py` にあり、現状は以下（コメント含む）:

- `make_site_id()` の形式: `"{filename}:{co_firstlineno}:{f_lasti}"`
- `caller_site_id()` はユーザー側の呼び出しフレームまで遡って `make_site_id()` を呼ぶ

ここで問題になるのが `f_lasti`（最後に実行したバイトコードオフセット）:

- `G.polyhedron()` → `G.polyhedron(type_index=2)` のような**同一行・同一呼び出し箇所の編集でも**、
  コンパイル結果のバイトコードが変わり `f_lasti` が変わり得る。
- 結果として site_id が変わり、ParamStore 上は「別の呼び出し箇所」として新規キー扱いになる。
- 永続化により旧キーが残っているため、GUI では旧グループ＋新グループの両方が表示され、ヘッダが複製される。

この構造は primitive/effect の両方で成立するため、「polyhedron も effect も増える」説明が付く。

## 3. 根本原因（結論）

ParamStore 永続化そのものではなく、**site_id が `f_lasti` 依存で不安定**なことが原因。

- 永続化なし: 実行ごとに store が空になるので “増殖” は目立たない
- 永続化あり: 編集のたびに新 site_id が生まれ、古い site_id が残り続け、GUI が増殖する

## 4. 修正方針（シンプル優先）

### 4.1 目標

- `G.polyhedron()` → `G.polyhedron(type_index=2)` のような「呼び出し行は同じ」編集で site_id が変わらないこと。
- その結果として、復元後にヘッダが複製されないこと。

### 4.2 対応案

案 A（最小・推奨）:

- site_id から `f_lasti` を外し、**行番号ベース**にする。
  - 例: `"{filename}:{lineno}"`（lineno は `frame.f_lineno`）
  - もしくは `"{filename}:{co_name}:{line_offset}"`（`line_offset = f_lineno - co_firstlineno`）

メリット:

- 実装が単純で、今回の “引数追加で増殖” を確実に止められる。

デメリット:

- 同一行に複数の `G.*()`/`E.*()` があると衝突しうる（運用で「1 行 1 呼び出し」を推奨するのが最も簡単）。

案 B（やや堅牢、ただし複雑化）:

- `dis.get_instructions(frame.f_code)` と `frame.f_lasti` から「その命令の source position（lineno/col）」を取得し、
  site_id を `"{filename}:{lineno}:{col}"` のようにする（`f_lasti` 自体は含めない）。

メリット:

- 同一行に複数呼び出しがあっても衝突しにくい。

デメリット:

- 実装とテストが増える（このリポの方針としては “必要が出てから” でよい）。

本件の修正は案 A で十分（必要十分 / シンプル優先）。

## 5. 既存の保存ファイルについて

site_id 仕様を変えると、既存の `data/output/param_store/*.json` 内のキーが新仕様と一致しないため、
**1 回だけ**以下が必要になる:

- 手動で `data/output/param_store/main.json` を削除/退避して作り直す

（互換 migration を作るのはシム扱いになりやすいので、このリポ方針ではやらない前提）

## 6. 修正チェックリスト（実装フェーズ）

- [ ] 再現メモを最小化（main.py で再現する手順を確定）
- [ ] 回帰テストを追加（site_id が「同一行の軽微編集」で変わらないこと）
  - 例: `compile(..., filename="main.py", ...)` を使い、`G.polyhedron()` と `G.polyhedron(type_index=2)` の 2 バリアントで
    生成される `ParameterKey.site_id` が一致することを確認する（`parameter_context` + `ParamStore` を使う）
- [ ] `src/parameters/key.py` の site_id 形式を案 A へ変更（`f_lasti` を除去）
- [ ] `tests/parameters/test_site_id.py` を必要に応じて更新
- [ ] ドキュメント（`parameter_spec.md` 等）に site_id 形式が出ていれば更新
- [ ] `data/output/param_store/main.json` を削除してスモーク確認
  - 1 回目: 起動 →GUI で変更 → 終了 → 保存される
  - 2 回目: `G.polyhedron(type_index=2)` に編集 → 起動 → ヘッダが増殖しない

## 7. 追加で確認したい点（任意）

- `main.py` 以外（`sketch/*.py`）でも同様に増殖が止まるか
- 1 行に複数呼び出しがあるスクリプトで衝突が問題になるか（問題なら案 B へ移行）
