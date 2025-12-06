# visualize_cache_plan.md

キャッシュ機構（`realize_cache` / `inflight`）を DAG とイベント種別で可視化するための実装計画メモ。
本メモは **実装方針とチェックリストのみ** を記述し、実際のコード変更はこの計画に対して承認をもらってから行う。

## 0. 目的と制約

- 目的
  - `realize` 実行時に「どの Geometry がいつ計算され、どれがキャッシュヒット／inflight 待ちだったか」を可視化する。
  - 1 フレーム分の Geometry DAG をグラフとして描画し、ノード色でキャッシュ挙動を一目で分かるようにする。
- 制約
  - **`src/` 配下の実装ファイルは変更しない**（可視化は外部ユーティリティとして完結させる）。
  - 可能であれば既存の依存のみで実装し、追加のライブラリ導入が必要な場合は別途相談する。
  - ランタイムの挙動を壊さないよう、instrumentation はオプトイン（呼び出したときだけ有効化）にする。

## 1. 全体アーキテクチャ案

1. `tools/cache_check/visualize_cache.py` のような **開発用ユーティリティモジュール** を `src/` の外に新規追加する。
2. このモジュールの中で `src.core.realize` を import し、以下を行う：
   - `RealizeCache.get` / `realize` などの関数・メソッドを **ラップするモンキーパッチ** を提供する。
   - ラップ内で「CACHE_HIT / INFLIGHT_WAIT / COMPUTE」種別の **イベントログ** を記録する。
3. Geometry DAG の構造は既存の `Geometry(op, inputs, id, args)` から復元し、イベントログで色付けだけを行う。
4. グラフ出力は以下のどちらかで行う：
   - Graphviz（`graphviz` パッケージ + dot 出力）が使える場合：PNG/SVG で DAG を描画。
   - 依存を増やしたくない場合：DOT 文字列だけを吐き、ユーザーが別途 `dot` で画像化する運用にする。

## 2. イベントロギング設計

### 2-1. イベント種別とデータ構造

- Enum: `RealizeEventType = { CACHE_HIT, INFLIGHT_WAIT, COMPUTE }`
- データクラス:
  - `RealizeEvent`: `geom_id`, `op`, `event_type`, `depth`（任意: ルートからの深さ）, `timestamp` や `duration` も将来拡張用に検討。
  - `FrameRealizeLog`: 1 フレーム分の `events: list[RealizeEvent]` を保持。

### 2-2. src を触らずにイベントを拾う方法

- `RealizeCache.get` のラップ
  - オリジナル `RealizeCache.get` を退避し、ラップ関数を `src.core.realize.realize_cache.get` に差し込む。
  - `get(key)` の戻り値が `None` 以外なら、その `GeometryId` に対して `CACHE_HIT` を記録。
- `_inflight` / `RealizeError` 周り
  - `realize` 関数自体をラップし、以下のタイミングで `INFLIGHT_WAIT` / `COMPUTE` を推定する。
    - ラップの先頭で「キャッシュヒット判定前後」の状態を記録するのは難しいので、次の近似案を採用：
      - `RealizeCache.get` ラップでヒットしなかった id について、`realize` ラップ内で「自スレッドが初回計算したかどうか」を判定する。
      - `_inflight` を直接見るのは避けたいので、**経過時間や再入状態を使った近似** も選択肢。ただし、精度を重視するなら `_inflight` も読みに行くラッパを検討。
    - 当面の案：
      - `realize` ラップ内で「オリジナル `realize` 呼び出し前後の `_inflight` テーブル状態」を読み取り、id が既に存在していたら `INFLIGHT_WAIT`、存在していなければ `COMPUTE` とみなす。
      - これは **読み取りのみ** なので、`src/core/realize.py` のコードは変更しない。
- ログのスコープ管理
  - スレッドローカル or シンプルに「コンテキストマネージャ／明示的 begin/end」を採用。
  - 例：`with start_frame_log() as log:` ブロック内での `realize(...)` 呼び出しだけをログ対象にする。

## 3. DAG 可視化設計

### 3-1. Geometry DAG の構築

- 入力: ルート `Geometry`（例：user_draw の戻り値から得た Geometry）と、対応する `FrameRealizeLog`。
- 処理:
  1. `visited` セットで `Geometry.id` を重複排除しながら `inputs` を再帰的にたどる。
  2. 各 `Geometry` ごとに最新の `RealizeEventType` を `FrameRealizeLog` から引く（最後のイベントを優先）。
  3. その種別に応じてノード色を決定する：
     - `COMPUTE`: 赤
     - `CACHE_HIT`: 緑
     - `INFLIGHT_WAIT`: オレンジ
     - その他（ログなし）は灰色

### 3-2. グラフ生成

- Graphviz 利用案（推奨・依存追加が許可される場合）:
  - `graphviz.Digraph` に `node(id, label, color)` / `edge(parent_id, child_id)` を登録。
  - `label` は `"op\\n{idの先頭数文字}"` 程度の簡潔なものにする。
  - `dot.render(filename, format="png", cleanup=True)` で PNG 出力。
- 依存追加なし案:
  - 単純に DOT 言語のテキストを組み立てて `.dot` ファイルとして保存。
  - ユーザーが手元の Graphviz CLI で `dot -Tpng in.dot -o out.png` する運用。

## 4. 具体的タスクチェックリスト

### 4-1. ユーティリティモジュールの骨組み

- [x] `tools/cache_check/visualize_cache.py` を新規追加する。
- [x] `RealizeEventType`, `RealizeEvent`, `FrameRealizeLog` を定義する。
- [x] スレッドローカルに現在の `FrameRealizeLog` を保持する仕組みを用意する。

### 4-2. realize/inflight のインストルメント

- [x] `src.core.realize` から `realize`, `realize_cache`, `_inflight` を import する（読み取り専用）。
- [x] `RealizeCache.get` に対するラッパ関数を実装し、`CACHE_HIT` を記録する。
- [x] `realize` に対するラッパ関数を実装し、`COMPUTE` / `INFLIGHT_WAIT` を記録する。
- [x] これらのラップを適用 / 解除する `install_realize_tracer()` / `uninstall_realize_tracer()` を実装する。
- [x] `with frame_logging():` のようなコンテキストマネージャで 1 フレーム分のログスコープを張れるようにする。

### 4-3. DAG エクスポート機能

- [x] ルート `Geometry` と `FrameRealizeLog` を受け取り、DOT 文字列を構築する関数 `export_geometry_dag_dot(root, log) -> str` を実装する。
- [x] （オプション）Graphviz が利用可能な場合に PNG を生成する `save_geometry_dag_png(...)` を実装する。
- [x] ノード色（赤/緑/オレンジ/灰）とラベル表記のルールをドキュメントコメントとして明文化する。
- [x] 画像内に色の意味を示す凡例クラスタ（Legend）を追加する。

### 4-4. 最小動作確認用スクリプト

- [x] シンプルなサンプル（`G.circle` + `E.scale` を使った DAG）を 1 フレーム分だけ評価し、DOT あるいは PNG を出す小さなスクリプトを追加する（`examples/visualize_cache_demo.py` などを想定）。
- [ ] README か専用の md に「どう呼び出せば可視化できるか」の手順を追記する（例：`install_realize_tracer()` を呼んでから draw を実行、`frame_log` を渡して `export_geometry_dag_*` を呼ぶ）。

## 5. オープンな論点・要相談事項

- [ ] Graphviz / networkx などの外部ライブラリを依存に追加してよいか（AGENTS.md のポリシー的に事前相談が必要）。
- [ ] `_inflight` への直接アクセスをどこまで許容するか（読み取りのみか、それとも「状態タグ」を付与するか）。
- [ ] 可視化の出力先フォーマット（PNG/SVG/DOT）の優先順位と、どこまで自動化するか（ファイルパス命名規則など）。
- [ ] 将来的に「時間軸」を含めたアニメーション（フレーム列）可視化まで広げるかどうか。

### 5-1. 実装してみて分かったメモ

- [ ] `realize` のモンキーパッチは `src.core.realize.realize` を直接呼ぶ経路に対して確実に効く。`from src.core.realize import realize` を先に実行している既存コードは、alias 先の関数オブジェクトが差し替わらないため、必要であれば「トレーサをインストールしてから import する」運用ルールを決めたほうがよい。

---

この計画に問題なければ、このチェックリストに沿って
`tools/visualize_cache.py` などの新規モジュールと簡易サンプルスクリプトを実装していきます。
実装中に新たな論点が出てきた場合は、この md に項目を追記していきます。
