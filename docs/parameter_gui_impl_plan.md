# parameter_gui_impl_plan.md

どこで: `src/app/parameter_gui.py`, `src/api/run.py`, `src/parameters/*`, `tests/parameters/*`, `docs`。
何を: DearPyGui ベースの Parameter GUI を実装し、ParamStore とランナーに統合する。
なぜ: 実行中パラメータを GUI から発見・上書きできるようにし、創作時のフィードバックループを短縮するため。

## 0. ゴール

- Parameter GUI ウィンドウが 3 列（ラベル / 値コントロール / ui_min-ui_max-cc）で全パラメータを表示し、override 切替と値更新ができる。
- GUI 操作が ParamStore に即時反映され、次フレームの param_snapshot で有効になる（描画を止めずに更新）。
- DearPyGui と pyglet のイベントループを同一スレッドで回し、閉じる/終了時に双方のリソースをクリーンアップできる。

## 1. スコープ・前提

- Parameter backend（ParamStore / resolver / contextvars）は既存実装を活用し、追加するのは GUI 表示と ParamStore への書き込み経路。
- MIDI CC 実値の入力処理は未実装のままとし、UI では cc 番号設定を保持するのみ（後続タスク）。
- DearPyGui は依存に含まれている前提だが、import 失敗時は 例外。
- 永続化やプロファイル管理は現行の ParamStore JSON API を流用し、新規フォーマット追加は範囲外。

## 2. 設計方針

- データ経路: ParamStore に ParamMeta（kind/step/choices/ui_min/ui_max）を保持し、`ParameterRow` ビューに射影 → DPG ウィジェットを生成/更新。UI 変更は型変換・検証して ParamStore を更新する。
- イベントループ: pyglet の tick 内で `render_dearpygui_frame()` を呼ぶポーリング方式とし、追加スレッドは持たない。閉じる/例外時の teardown を明確化。
- UI: op ごとの ordinal をラベルに使い、arg 名を補助表示。kind に応じて slider / checkbox / input_text / combo / vec3 スライダーを使い、override トグルと cc 入力を併置する。
- ライフサイクル: run 起動時に ParameterGUI を初期化し、閉じる動作（GUI/描画ウィンドウ双方）に合わせてクリーンアップする。GUI を閉じても描画は継続させる方針で検討。

## 3. タスク分解（チェックリスト）

- [ ] ParamStore に ParamMeta 永続化を追加し、key/state/meta/ordinal を返す `iter_descriptors()` を用意。`snapshot()` / `merge_frame_params()` の更新と既存テスト調整を行う。
- [ ] ViewModel ヘルパ（純粋関数）を追加し、ParamStore から `ParameterRow`（label/op/arg/kind/ui_value/ui_min/ui_max/step/choices/cc/override/ordinal/last_seen）を生成。並び順・型判定のユニットテストを作成。
- [ ] UI 更新ユーティリティを設計し、ユーザー入力を型変換・妥当化して ParamStore に反映する処理を DPG 非依存で実装。ui_min>=ui_max や型不一致時のフォールバック挙動もテストする。
- [ ] `src/app/parameter_gui.py`: DearPyGui のセットアップ/破棄と 3 列テーブル生成。各 kind に応じたウィジェット生成とコールバック配線、行の追加/更新/非表示管理、override・cc 表示を実装。
- [ ] `api/run.py`: `parameter_gui` オプション（デフォルト要確認）を追加し、ParamStore を共有した ParameterGUI を初期化。tick 内で GUI フレームを回し、終了時に teardown する。
- [ ] UX 微調整: override/cc 状態の色分けまたはアイコン表示、最近出現していないパラメータのグレーアウトなど最小限のフィードバックを追加。
- [ ] ドキュメント: README に GUI 起動方法（オプション名、依存ライブラリ、制約）を追記し、parameter_spec に GUI 実装の反映があれば補足する。
- [ ] テスト: view-model / 更新ユーティリティの単体テストを `tests/app/test_parameter_gui_view.py` 等に追加。DPG 依存の統合テストはスキップか軽いスモークのみとする。

## 4. リスク・要確認

- DearPyGui と pyglet を同一スレッドで回す際の FPS 低下や GL コンテキスト衝突の有無。必要に応じて GUI ポーリング頻度を落とす設定を設ける。
- `parameter_gui` オプションのデフォルトを有効/無効どちらにするか（既存挙動維持 vs 機能訴求）。決定が必要。
- ParamMeta の永続化によるデータ構造変更が既存 JSON 互換性やテストに影響しないか。
- CC 未実装のまま番号入力だけを許可する場合のユーザー混乱。UI またはドキュメントで明示する必要。

## 5. 完了定義

- run を GUI 有効で起動すると別ウィンドウが開き、発見済みパラメータが 3 列 UI で編集でき、変更が次フレームの描画に反映される。
- 既存テストが通り、追加した view-model / store テストがグリーンになる。
- GUI ウィンドウを閉じた際に DPG コンテキストが解放され、描画ループの終了/継続が予測可能に制御できる。
