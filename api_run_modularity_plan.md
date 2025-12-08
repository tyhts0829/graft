# api_run_modularity_plan.md

どこで: リポジトリ直下、新規ドキュメント。対象コードは `src/api/run.py` と関連する `src/render/*`。
何を: `run` ランナーの肥大化を防ぐための分割方針と段階的リファクタ計画を整理する。
なぜ: 追加機能を盛り込みやすくしつつエントリポイントを簡潔に保ち、保守コストとバグの温床を抑えるため。

## 0. ゴールとスコープ

- 公開 API `run(draw, ...)` のシグネチャを極力安定させつつ、内部責務をモジュール単位に分離できる構造を決める。
- 近い将来の追加機能（入力/録画/GUI/計測）を入れても `src/api/run.py` が数百行規模に膨らまない見通しを作る。
- ここでは設計方針とタスク分割までを扱い、実装は別タスクで進める。

## 1. 現状整理（2025-12-08 時点）

- `run` がウィンドウ生成、ModernGL コンテキスト初期化、フレームループ、入力イベント、リソース破棄を一括で担っている。
- 描画パス構築とウィンドウイベントは匿名関数で閉じ込めており、単体テストしにくい。
- `_build_line_indices` がレンダリングユーティリティとして `run.py` に同居している。

## 2. 肥大化の主なドライバー予測

- プレビュー機能の拡張（スクリーンショット/録画、ズーム・パン、グリッド表示、FPS 表示）。
- パラメータ GUI やホットリロード対応によるイベントハンドラの追加。
- 設定項目の増加（カラー、スタイル、AA 設定、プロファイルオプション）。
- 例外処理・リソース管理の強化、ログ計測の追加。

## 3. 分割の基本方針

- `run` は「依存を束ねてループを開始する」最小限のオーケストレーション関数に留める。
- GL リソース管理、ウィンドウイベント、ジオメトリ変換をそれぞれ独立モジュールに分離し、純粋関数化できるところは純粋関数に寄せる。
- 構造はシンプルを最優先し、抽象化は「複数の呼び出し箇所が実際に生まれたとき」に限定する。

## 4. 想定するモジュール境界（候補）

- `src/api/run.py`: 公開エントリ。ユーザーから見えるのは G/E/run だけに留める。
- `src/render/render_settings.py`（新規）: 背景色・線色・線幅・解像度などを束ねる dataclass `RenderSettings`。内部利用のみ。
- `src/render/index_buffer.py`（新規）: `_build_line_indices` を移動。Pure なテスト対象。
- `src/render/preview_renderer.py`（新規）: ModernGL コンテキスト、シェーダ、メッシュの生成と `render_frame` メソッドを担当。
- `src/app/preview_window.py`（新規）: pyglet ウィンドウ生成とイベント登録を担当し、外部から `on_draw`/`on_resize`/`on_close` コールバックを受け取る薄いラッパ。
- `tests/api/test_preview_indices.py` など（新規）: インデックス生成とレンダラー初期化の単体テスト用。

### 4.1 Parameter GUI 連携の方針（ランナー側の責務）

- `run(draw)` は「パラメータ解決付き draw」を生成し、それをレンダラーに渡すだけに寄せる。
- パラメータ解決は contextvar ベースのランタイム層（別モジュール、例: `src/runtime/parameters/*`）に委譲し、`run` はフレームごとに snapshot を開始/終了する。
- site_id 取得は `sys._getframe()` の `(f_code, f_lasti)` を用い、永続化キーは `"{filename}:{co_firstlineno}:{f_lasti}"` 形式に文字列化する。
- ParamKey = (op, site_id, arg) を GUI/cc/override のキーに用い、連番 (#1, #2…) は op ごとに site_id 初出順で割り当てる。

## 5. 段階的リファクタ手順（チェックリスト）

- [ ] 設定の塊を `RenderSettings` dataclass（`src/render/render_settings.py`）に切り出し、`run` の引数個数を減らす。
- [ ] `_build_line_indices` を `src/render/index_buffer.py` に移動し、境界値ケースの単体テストを追加。
- [ ] `PreviewRenderer`（仮称）を導入し、コンテキスト生成と `render_frame` をクラス責務としてまとめる。
- [ ] ウィンドウ生成とイベント登録を別モジュール（`preview_window.py`）へ移し、`run` はコールバックを渡すだけにする。
- [ ] フレームループを関数化（例: `run_loop(window, tick_fn, fps=60)`）し、後から計測や録画フックを挟みやすくする。
- [ ] Parameter GUI 連携のランタイム層（snapshot, discovery, resolver）を `run` から分離し、コンテキスト開始/終了フックだけを `run` に残す。
- [ ] 公開シグネチャを壊さずに移行できるステップ順を設計し、PR 単位を小さく切る。

## 6. オープン事項（要確認）

- `RenderSettings` の正確な配置: 現案は `src/render/render_settings.py`（ユーザー公開しない前提）。`src/runtime` 配下に置く案もあるが、描画専用である点を踏まえ要最終決定。
- `app` 名前空間を新設してよいか、それとも `src/render/window.py` など既存階層に収めるか。
- Parameter GUI との橋渡し API（GUI からの state read/write）の設計は保留。描画周りの分割後に再検討する。
- 並列実行時の GUI 制約: DearPyGui はメインスレッド専用（メインスレッドで window loop/描画を動かし、ワーカースレッドは計算のみ）。pyglet との共存方針を決める必要あり（イベントループ統合 or どちらかに寄せる）。

## 7. 非ゴール

- 新しい描画機能（AA、レイヤー合成、オフスクリーンレンダリング）の実装は別タスクとする。
- API シグネチャの互換性を壊すリファクタ（例: `draw` コールバックのプロトコル変更）はこの計画の対象外。
