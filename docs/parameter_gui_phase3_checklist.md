# どこで: docs/parameter_gui_phase3_checklist.md
# 何を: フェーズ3（GUI 骨格）で DearPyGui 依存部を実装するためのチェックリスト。
# なぜ: パラメータ行モデルを実際の UI に載せ、イベントループ統合とクリーンアップを安全に進めるため。

## 方針
- DPG 部は `src/app/parameter_gui.py` に集約し、純粋関数（view.py）は再利用する。
- イベントループは pyglet と同一スレッドで `render_dearpygui_frame()` をポーリングする。
- 破壊的変更を許容し、シンプルさ優先（互換フラグなし）。

## チェックリスト（具体的な変更対象・内容）
- [ ] 現状確認  
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ3要件、既存の `parameter_gui.py` の有無（未実装想定）。  
  - 内容: 必要なウィジェット構成（3列: label / control / cc&override）とイベントループ統合ポイントをメモ。
- [ ] UI レイアウト構築  
  - 対象: 新規 `src/app/parameter_gui.py`。  
  - 内容: DPG コンテキスト生成/破棄、メインウィンドウ作成、3列テーブル（label, control, cc/override）を構築。  
- [ ] ウィジェットディスパッチ設計  
  - 対象: `parameter_gui.py` 内の kind→ウィジェット生成関数マップ。  
  - 内容: kind ごとの UI 方針を固定し、共通インターフェース（生成・値取得・更新）を定義する。  
    - float: `add_slider_float`（ui_min/ui_max 必須）。ui_min>=ui_max であれば例外。  
    - int: `add_slider_int`（ui_min/ui_max 必須）。ui_min>=ui_max であれば例外。  
    - bool: `add_checkbox`。  
    - string: `add_input_text`。  
    - choice: `add_combo`（choices 必須。空/None は例外）。  
    - vec3: `add_slider_float3`（サポート無しなら例外）。  
    - rgb: `add_color_picker3`（または `add_slider_int3`）。サポート無しなら例外。  
    - 未知 kind: 例外を投げて検知させる。  
    - ユーザー定義 primitive/effect: meta 無しなら GUI 非表示、meta 不正は例外。  
- [ ] 行の追加/更新/非表示管理  
  - 対象: `parameter_gui.py` 内のロジック。  
  - 内容: `rows_from_snapshot` の出力を使い、既存行は更新、新規は追加、未観測になった行は非表示またはグレーアウト。widget_id 命名規約を統一。
- [ ] コールバック配線  
  - 対象: `parameter_gui.py`。  
  - 内容: ウィジェット変更時に `update_state_from_ui` を呼び、状態を ParamStore に反映。エラー時はログ/色でフィードバック。override トグル・cc入力もここで処理。
- [ ] イベントループ統合  
  - 対象: `parameter_gui.py`。  
  - 内容: `tick()` 的な関数で `render_dearpygui_frame()` を呼び、閉じるボタンで DPG を teardown。pyglet 側から呼び出せる I/F を定義。
- [ ] クリーンアップ  
  - 対象: `parameter_gui.py`。  
  - 内容: shutdown 時に DPG コンテキストを明示的に破棄し、複数回起動を考慮して二重初期化を防ぐ簡易ガードを入れる。
- [ ] 最小スモークテスト  
  - 対象: `tests/app/test_parameter_gui_smoke.py`（スキップ可）。  
  - 内容: DPG が import できる環境ではウィンドウ生成/teardown だけを確認し、それ以外は `pytest.skip`。
- [ ] ドキュメント更新  
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ3の進捗を反映。  
  - 内容: 実装した I/F（例: `ParameterGUI.tick()`/`close()`）を記載。
- [ ] 確認ポイント CP3  
  - 内容: 最小パラメータセットで GUI ウィンドウを開き、3列表示と override トグルが動くことをユーザーに確認してもらう。
