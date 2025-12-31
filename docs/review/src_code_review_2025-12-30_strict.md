# src 配下モジュール 厳しめコードレビュー（2025-12-30）

対象: `src/grafix/`（`api/`, `core/`, `export/`, `interactive/`）  
観点: 全体設計 / 責務境界 / リファクタリング価値の高い箇所（厳しめ）  
除外: **primitive/effect 個別実装のアルゴリズム指摘**（ディレクトリ構造・登録方式など “全体設計に関わる点” は触れる）  
実施: 実コード読解 + `mypy src/grafix`（OK）+ `pytest -q tests/architecture/test_dependency_boundaries.py`（OK）  
備考: `ruff` は環境に無く実行不可。

---

## 総評（先に結論）

- 設計の芯（`Geometry` DAG → `realize` → `interactive/export` 分離、依存方向のテスト化、`contextvars` によるフレーム固定）はかなり良い。ここは維持した方がよい。
- 一方で、今のまま進めると後で確実に詰まる “構造負債” が 3 つある:
  1) **site_id 生成が環境依存（絶対パス + `f_lasti`）**で、ParamStore の永続化が壊れやすい  
  2) **`realize_cache` が無制限**で、作り方次第で常時メモリリークになる  
  3) **`core/pipeline.py` が「評価 + GUI 連携 + style 上書き」を混在**させており、責務境界が今後の拡張に耐えにくい

---

## 良い点（残すべき設計の芯）

- **依存境界が明確**で、テストで担保されている（`core` が `interactive/export` に依存しない、など）。設計規律が崩れにくい。
- `Geometry` を **不変 + 内容署名（`GeometryId`）**で扱うのは強い。キャッシュ設計・差分最適化の土台として正しい。
- `RealizedGeometry` の **不変条件チェック + writeable=False** は “壊れるなら早く落ちる” 方向で良い。
- `grafix.core.parameters` はアーキテクチャ文書と層分け（data / pure / ops / control）が揃っていて、比較的読み手に優しい。
- mp-draw（別プロセス実行）で **ParamStore の “スナップショット固定” を崩さず**に観測ログだけ戻す設計は筋が良い。

---

## 最優先で直すべき構造問題（P0）

### P0-1) site_id が “環境依存” で永続化が壊れやすい

現状:

- `caller_site_id()` → `make_site_id()` が `"{absolute_path}:{co_firstlineno}:{f_lasti}"` 形式。
- `site_id` は ParamStore の key に直結しており、永続 JSON にも残る。

問題:

- **別 PC / 別ディレクトリへ移動しただけで `absolute_path` が変わる**ため、保存済みの GUI 状態が参照不能になりやすい。
- `f_lasti` は Python 実装や最適化の影響も受けるため、**“同じ見た目のコード” でも変わり得る**（＝予期せぬ再リンク/増殖の温床）。
- reconcile はあるが、全ケースを救える保証はない（特に path が丸ごと変わるケース）。

提案（方向性）:

- `site_id` の「人間が読める識別子」と「安定なキー」を切り分ける。
  - **キー用途**は “移動しても壊れない” ことを優先し、`absolute_path` を含めない。
  - 表示用途は label（既にある）へ寄せる。
- Python 3.12 前提なら、PEP 657 の位置情報（行・列）を使って `f_lasti` 依存を外し、`"{relpath}:{lineno}:{col}"` のように寄せる余地がある。
- どうしても callsite ベースが限界なら、`L(..., key=...)` / `G(..., key=...)` / `E(..., key=...)` のように **ユーザーが衝突回避・安定化できる逃げ道**を用意する（いまの `preset(key=...)` と同じ思想）。

---

### P0-2) `realize_cache` が無制限で、使い方次第で常時メモリリーク

現状:

- `grafix.core.realize.realize_cache` は容量上限なし（設計メモはあるが未実装）。

問題:

- `GeometryId` は内容署名なので正しく作るほど “新規 id” が発生する。例えば `t` を引数に含めるだけでフレームごとに別 id が増え続ける。
- interactive で長時間回すと **確実にメモリが増え続ける**構造になっている（ユーザーコード側で回避しろ、は無理がある）。

提案（方向性）:

- `RealizeCache` を **LRU（個数または推定バイト）で上限制御**する。`RealizedGeometry` は `coords/offsets.nbytes` で概算できる。
- 最低限として `realize_cache.clear()` 等の **明示 API**を生やし、テストやツールが internals へ触らなくて済むようにする。

---

### P0-3) `core/pipeline.py` が “評価パイプライン” なのに param/GUI 連携が混ざっている

現状:

- `realize_scene()` が
  - シーン正規化
  - Layer style 解決
  - ParamStore への label 記録
  - GUI 値での style 上書き
  - frame_params の観測ログ追加
  - `realize()`（ジオメトリ評価）
  を 1 本でやっている。

問題:

- 「描画・出力に共通の “評価パイプライン”」という役割に対して、**parameter GUI の都合（キー・ラベル・上書き）が強く混入**している。
- 結果として、今後
  - ヘッドレス実行（export/CI）で完全に副作用ゼロにしたい
  - スタイル解決を別戦略にしたい（テーマ/プリセット/スキン）
  - ParamStore を差し替えたい（複数 store、read-only store など）
  みたいな要求が出たときに、分解が大変になる。

提案（方向性）:

- `pipeline` を 2 層に割る。
  - **pure pipeline**: `SceneItem -> list[Layer] -> realize -> RealizedLayer`（ParamStore を知らない）
  - **integration**: ParamStore（GUI/永続化）と結びつけて label/override/record を適用する層
- これにより “core の中心ロジック” が読みやすくなり、export/interactive も不要な分岐が減る。

---

## 優先度高（P1）

### P1-1) 組み込み primitive/effect の登録が import 副作用で、依存が追いづらい

- 現状は `grafix.api.primitives/effects` の import が登録トリガになっており、mp-draw worker もそれを前提に “念のため import” している。
- 動くが、**「どの import が何を初期化するか」**が見えにくく、テストや将来の plugin 化で足を引っ張りやすい。

提案:

- `grafix.core.builtins.register()` のような **明示的初期化ポイント**を作り、必要な場所（api/run/mp-draw）で呼ぶ。
  - もしくは `grafix.core.primitives` / `grafix.core.effects` の package import を登録トリガに寄せる（“core に置いたものは core の責務で登録する”）。

### P1-2) `Export` が “コンストラクタで実行” になっていて API 形状が不自然

- `Export(...)` の生成がファイル出力の副作用を持つのは、読み手の予想を裏切る。
- 今はユーザーがいない前提でも、将来の API 変更コストを上げる形なので早めに直した方がいい。

提案:

- `export(draw, t, fmt, path, ...)` の関数に寄せるか、`Export(...).save()` のように **実行メソッド**を切る。

### P1-3) 永続化ロード失敗が静かすぎる

- `load_param_store()` が JSON 破損などを “空ストアで黙って継続” するのは便利だが、原因追跡が難しい。

提案:

- 破損時は `logging.warning` で 1 行だけ出す（ファイルパスと例外種別だけで十分）。

### P1-4) Parameter GUI の “表示順” ロジックが store_bridge に集中しすぎ

- `rows` をどう並べるかの規則が大きく、今後ルール追加が続くと “ここだけ読めば良い” 状態が崩れる。

提案:

- 並べ替えは `RowOrdering` のような独立コンポーネントへ切り出し、**入力（rows + runtime 索引）→出力（rows）**の純粋関数としてテストしやすくする。

---

## 優先度中（P2）

- **print と logging が混在**している（特に interactive）。ライブラリとして育てるなら logging に寄せる方が扱いやすい。
- グローバルキャッシュ（font choices / GUI フィルター / config cache 等）が散在している。現状は問題化していないが、将来 “動的に設定を切り替える” 方向へ行くなら整理が必要。

---

## おすすめの進め方（破壊的変更 OK 前提の最短ルート）

1) site_id 生成の置き換え（PEP 657 位置情報 + relpath 化）  
2) `realize_cache` の上限制御（LRU + clear/stats）  
3) pipeline を pure と integration に分割  
4) 組み込み登録を明示化（import 副作用の整理）  
5) Export API の形を整える（constructor side-effect 排除）

