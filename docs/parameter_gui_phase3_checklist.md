# どこで: docs/parameter_gui_phase3_checklist.md

# 何を: フェーズ 3（GUI 骨格）で pyimgui 依存部を実装するためのチェックリスト。

# なぜ: パラメータ行モデルを実際の UI に載せ、イベントループ統合とクリーンアップを安全に進めるため。

## 方針

- pyimgui 部は `src/app/parameter_gui.py` に集約し、純粋関数（view.py）は再利用する。
- pyglet window を引数に `imgui.integrations.pyglet.create_renderer(window)` で renderer を作る（古い `PygletRenderer` は避ける）。
- イベントループは pyglet と同一スレッドで `renderer.process_inputs()` → `imgui.new_frame()` → UI 描画 → `imgui.render()` → `renderer.render(imgui.get_draw_data())` を 1 フレームとしてポーリングする。
- 破壊的変更を許容し、シンプルさ優先（互換フラグなし）。

## チェックリスト（具体的な変更対象・内容）

- [x] 現状確認
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ 3 要件、既存の `parameter_gui.py` の有無（未実装想定）。
  - 内容: 必要なウィジェット構成（4 列: label / control / min-max / cc&override）とイベントループ統合ポイントをメモ。
- [ ] UI レイアウト構築
  - 対象: 新規 `src/app/parameter_gui.py`。
  - 内容: imgui コンテキスト生成/破棄、`imgui.integrations.pyglet.create_renderer(window)` の初期化、メインウィンドウ（pyglet 側で既存 window を使う想定）と 4 列テーブル（1: label, 2: control, 3: min-max, 4: cc_key/override）を構築。kind によって列 3/4 は空（描画しない）になる。`imgui.begin_table` の戻り値（opened）を確認し、true のときのみ行を追加して最後に `imgui.end_table()`。Style セクションを先頭に配置する（背景色・グローバル thickness・グローバル line_color の 3 行、続いて Layer（L(name)）ごとに thickness 行 + color 行）。Layer で未指定の thickness/color はグローバルを適用する。
- [x] ウィジェットディスパッチ設計
  - 対象: `parameter_gui.py` 内の kind→ ウィジェット生成関数マップ。
  - 内容: kind ごとの UI 方針を固定し、共通インターフェース（生成・値取得・更新）を定義する。
	    - float: `imgui.slider_float`（ui_min/ui_max を `min_value`/`max_value` に渡す）。meta 由来のデフォルトが `ui_min > ui_max` の場合は例外（GUI の min-max 入力では例外にしない）。
	    - int: `imgui.slider_int`（同上）。meta 由来のデフォルトが `ui_min > ui_max` の場合は例外（GUI の min-max 入力では例外にしない）。
	    - bool: `imgui.checkbox`（戻り値は clicked, state。clicked を changed として扱う）。min-max / cc / override は使用しない（列 3/4 は空）。
	    - string: `imgui.input_text`（戻り値は changed, value。buffer_length 省略で可変長）。min-max / cc / override は使用しない（列 3/4 は空）。
	    - choice: `imgui.radio_button`（choices 必須。空/None は例外。戻り値は changed, value（文字列）で保持する）。min-max / cc / override は使用しない（列 3/4 は空）。
	    - vec3: `imgui.slider_float3` を使用（戻り値は changed, values_tuple）。
	    - rgb: `imgui.color_edit3` を第一候補（内部は float 0-1、戻り値は changed, (r,g,b)。0-255 と混ぜる場合は変換層を挟む）。スライダー方式なら `imgui.slider_int3` を選択肢に入れる。
	    - cc 方針（列 4 の cc 入力）
	      - float/int: `imgui.input_int`（負数は None 扱い）
	      - vec3: `imgui.input_int3`（(x,y,z) の 3 つ。負数は None 扱い）
	      - bool/string/choice: cc / override は表示しない（無効）
	    - 未知 kind: 例外を投げて検知させる。
	    - ユーザー定義 primitive/effect: meta 無しなら GUI 非表示、meta 不正は例外。
  - 注意: すべて (changed, value) 系で返る前提でディスパッチの共通 I/F を統一する（checkbox だけ clicked→changed に正規化）。
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
  - 内容: `tick()` 的な関数で `renderer.process_inputs()` → `imgui.new_frame()` → テーブル描画 → `imgui.render()` → `renderer.render(imgui.get_draw_data())` を実行。pyglet 側の `on_draw` / `on_close` 連携を明示し、閉じるボタンで renderer/コンテキストを teardown。pyglet 側から呼び出せる I/F を定義。
- [x] マルチウィンドウ・Retina 対応の実装メモ（今回のスモークで判明した罠）
  - pyglet+pyimgui を別ウィンドウで動かすと、macOS/Retina では framebuffer サイズが論理解像度の 2 倍になる。`imgui.get_io().display_size` / `display_fb_scale` を毎フレーム `gui_window.width/height` と `framebuffer_size / width_height` で上書きしないと UI 位置とヒットテストがずれて入力不能になる。
  - `io.delta_time` も実測 dt で更新しないとイベント処理が重く感じるケースがある。メインループ内で dt 計測して `io.delta_time = dt` をセット。
  - 位置決めは ImGui の座標系（上記で上書きした display_size 基準）で行う。ウィンドウ実寸を使うと Retina で 0.5,0.5 中央指定時に右上へずれる。
  - ダブルバッファ + vsync を有効にし、毎フレーム `window.dispatch_events()` → clear → draw → flip の順に処理すると点滅が消える。
  - pyimgui 2.0 が内部で `distutils.LooseVersion` を使うため DeprecationWarning が出る。根本対応は `packaging.version` へ置換（フォーク/パッチ）か、テスト側で warning をフィルタする。
- [ ] クリーンアップ
  - 対象: `parameter_gui.py`。
  - 内容: shutdown 時に `renderer.shutdown()` → `imgui.destroy_context(imgui.get_current_context())` を呼ぶ。複数回起動を考慮して二重初期化を防ぐ簡易ガードを入れる。
- [ ] 最小スモークテスト
  - 対象: `tests/app/test_parameter_gui_smoke.py`（スキップ可）。
  - 内容: pyimgui が import できる環境ではコンテキスト生成 → 1 フレーム描画 → teardown だけを確認し、それ以外は `pytest.skip`。
- [ ] ドキュメント更新
  - 対象: `docs/parameter_gui_impl_plan.md` フェーズ 3 の進捗を反映。
  - 内容: 実装した I/F（例: `ParameterGUI.tick()`/`close()`）を記載。
- [ ] 確認ポイント CP3
  - 内容: 最小パラメータセットで GUI ウィンドウを開き、4 列表示と override トグルが動くことをユーザーに確認してもらう。

## 見直しメモ（実装して分かった点）

- 「行の追加/更新/非表示管理」は不要寄り: 毎フレーム `snapshot → rows → render → 差分 apply` で十分（ImGui の ID は `push_id(_row_id)` で安定）。；承認します。
- 「UI レイアウト構築」に “Style セクション” まで入っていて重い: (A) parameter table と (B) Style/Layer/label header に分割した方が進めやすい。
- 「create_renderer を使い PygletRenderer を避ける」は現実装は「優先」にしてフォールバックあり: 方針を「必須」にするか、計画側で緩めるか決めたい。
- kind ごとの CC 方針: `vec3` は `input_int3`、`bool/string/choice` は cc 入力を非表示にした。
