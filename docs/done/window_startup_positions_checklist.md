# どこで: `docs/window_startup_positions_checklist.md`。
# 何を: 描画ウィンドウと Parameter GUI ウィンドウの「起動時の OS ウィンドウ位置」を、実装で固定する計画。
# なぜ: 毎回ウィンドウがバラバラに出ると作業がしづらく、2 ウィンドウを並べて使いたいため。

## 方針（最小）

- `pyglet.window.Window.set_location(x, y)` を呼び、OS のウィンドウ位置を指定する。
- `run()` の引数は増やさない（実装内の定数で指定する）。
- Parameter GUI 内の ImGui ウィンドウ（`imgui.set_next_window_position(0, 0)` で全面表示）は変更しない。

## 仕様（決める）

- [x] 描画ウィンドウの基準位置を決める（`DRAW_WINDOW_POS = (200, 200)`）
- [x] Parameter GUI の配置方式を決める（案A: 固定位置）
  - [x] 案A: 固定位置（`PARAMETER_GUI_POS = (800, 200)`）
  - [ ] 案B: 描画ウィンドウの右隣（例: `PARAMETER_GUI_POS = (DRAW_X + draw_w + GAP, DRAW_Y)`）
- [x] 案B の場合、間隔 `GAP` を決める（今回は不要）

## 実装（予定）

- [x] `src/api/run.py` で `DrawWindowSystem` / `ParameterGUIWindowSystem` 生成後に `set_location(...)` を呼ぶ
  - [x] `parameter_gui=False` の場合は描画ウィンドウのみ移動する
  - [x] `parameter_gui=True` の場合は 2 つのウィンドウを意図した配置にする
- [x] 位置定数の置き場所を決める
  - [x] 今回は `src/api/run.py` 内で完結（最小）
  - [ ] 必要になったら `src/app/runtime/window_positions.py` へ切り出す（今回はやらない）

## 動作確認（手動）

- [ ] `python main.py` で起動し、2 ウィンドウが想定位置に出ることを確認する
- [ ] いったん閉じて再起動し、毎回同じ位置になることを確認する

## 追加メモ / 要確認

- [ ] 座標系（左下原点/左上原点）が環境でズレる場合は、値を調整して決め打ちする
- [ ] マルチモニタ環境での「どのディスプレイに出すか」は今回は扱わない（最小実装優先）
