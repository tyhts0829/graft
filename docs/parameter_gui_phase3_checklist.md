# どこで: docs/parameter_gui_phase3_checklist.md

# 何を: フェーズ 3（GUI 骨格）で DearPyGui 依存部を実装するためのチェックリスト。

# なぜ: パラメータ行モデルを実際の UI に載せ、イベントループ統合とクリーンアップを安全に進めるため。

## 方針

- DPG 部は `src/app/parameter_gui.py` に集約し、純粋関数（view.py）は再利用する。
- イベントループは pyglet と同一スレッドで `render_dearpygui_frame()` をポーリングする。
- 破壊的変更を許容し、シンプルさ優先（互換フラグなし）。

## チェックリスト（具体的な変更対象・内容）

- [x] 現状確認
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ 3 要件、既存の `parameter_gui.py` の有無（未実装想定）。
  - 内容: 必要なウィジェット構成（3 列: label / control / cc&override）とイベントループ統合ポイントをメモ。
- [ ] UI レイアウト構築
  - 対象: 新規 `src/app/parameter_gui.py`。
  - 内容: DPG コンテキスト生成/破棄、メインウィンドウ作成、3 列テーブル（1: label, 2: control, 3: ui_min 入力・ui_max 入力・cc_key 入力・override トグル）を構築。先頭に Style セクションを設け、まず背景色・グローバル thickness・グローバル line_color の 3 行を配置し、続けて各 Layer（L(name)）ごとに 2 行（thickness, color）で線太さ・色を制御する。ユーザーが draw 関数内で Layer 機能で明示しなかった thickness/color はグローバル設定が反映される仕様。
- [ ] ウィジェットディスパッチ設計
  - 対象: `parameter_gui.py` 内の kind→ ウィジェット生成関数マップ。
  - 内容: kind ごとの UI 方針を固定し、共通インターフェース（生成・値取得・更新）を定義する。
    - float: `add_slider_float`（ui_min/ui_max を DPG の `min_value`/`max_value` に渡す）。ui_min>=ui_max は例外。
    - int: `add_slider_int`（同上）。ui_min>=ui_max は例外。
    - bool: `add_checkbox`。
    - string: `add_input_text`。
    - choice: `add_combo`（choices 必須。空/None は例外）。
    - vec3: `add_slider_floatx(size=3)` を使用（2.1.1 で公式サポートあり）。
    - rgb: `add_color_picker(no_alpha=True)` を第一候補（内部は RGBA、値の get/set で先頭 3 要素を使用）。スライダー方式なら `add_slider_intx(size=3)` を選択肢に入れる。
    - 未知 kind: 例外を投げて検知させる。
    - ユーザー定義 primitive/effect: meta 無しなら GUI 非表示、meta 不正は例外。
- [ ] 行の追加/更新/非表示管理  
  - 対象: `parameter_gui.py` 内のロジック。
  - 内容: `rows_from_snapshot` の出力を使い、既存行は更新、新規は追加。widget_id 命名規約を統一（未観測の扱いは今回なし）。
- [ ] ヘッダ行・ラベリング  
  - 対象: `parameter_gui.py`。  
  - 構造（表示順）:  
    - Style ヘッダ  
      - 行: background_color  
      - 行: global_thickness
      - 行: global_line_color
      - Layer セクション: L(name) ごとに thickness 行 + color 行
    - Primitive ヘッダ: op 名または `label(name=...)` の値（未指定は op 名）。
    - Effect チェーンヘッダ: デフォルト `effect#N`。`label(name=...)` があれば上書き。
  - ルール:
    - `label(name=...)` は primitive / effect チェーン内どこからでも呼べる。最後に呼ばれたものを採用。複数回呼びは例外。
    - 同名が複数ある場合は末尾に `#1`, `#2` を自動付与。  
    - name は長さ上限を設け、超過時はトリム。  
    - ラベル情報は ParamStore に永続化。  
  - 追加タスク（未実装）:  
    - snapshot の label を GUI で取り出し、重複時は連番付与する処理を実装。  
- [ ] コールバック配線
  - 対象: `parameter_gui.py`。
  - 内容: ウィジェット変更時に `update_state_from_ui` を呼び、状態を ParamStore に反映。エラー時はログ/色でフィードバック。override トグル・cc 入力もここで処理。
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
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ 3 の進捗を反映。
  - 内容: 実装した I/F（例: `ParameterGUI.tick()`/`close()`）を記載。
- [ ] 確認ポイント CP3
  - 内容: 最小パラメータセットで GUI ウィンドウを開き、3 列表示と override トグルが動くことをユーザーに確認してもらう。
