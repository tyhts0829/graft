# ParameterGUI: クリック不発 / スライダー「ドラッグ解除不能」真因と対策候補（2025-12-27）

- どこで: `docs/memo/parameter_gui_mouse_stuck_drag_rootcause_report_2025-12-27.md`
- 何を: ParameterGUI の入力不具合について、真因（仮説）と対策候補を整理する
- なぜ: 変更を reset してから改めて修正するため（実装に依存しない整理が必要）

## 結論（現時点の最有力）

最有力の真因は、**macOS（arm64）で `Window.dispatch_events()` ベースの手動イベントループが不安定になり、mouse release 等の入力イベントが取りこぼされる**こと。
取りこぼしが起きると ImGui 側で押下状態（`io.mouse_down[0]`）が残留し、クリック不発やドラッグ解除不能に繋がる。

併発要因として、**`renderer.process_inputs()` が内部で `pyglet.clock.tick()` を呼ぶ**ため、アプリ側のループ/clock 駆動と混ざると入力・スケジューリングの整合が崩れやすい可能性がある。

## 症状

- `parameter_gui` のクリックが成立しない（押して離しても反応しない）
- スライダーをドラッグしたあと、ドラッグが解除されず UI 全体が「押しっぱなし」っぽくなる
- 体感として「起動直後は動くが、後半（しばらく操作した後）から効かなくなる」ことがある

## 前提（不具合が起きるメカニズム）

- ImGui は基本的に「press → release」という入力イベント列が揃って初めてクリックやドラッグ終了を確定できる
- `on_mouse_release` 相当が取りこぼされると、ImGui 側の押下状態が残留しやすい（= 常に押されている扱い）
- 複数ウィンドウ構成では「release が別ウィンドウに届く」「アプリ外で release される」など、GUI ウィンドウ単体では完結しないケースが発生し得る
- 合意済み期待挙動: **ドラッグ中に GUI 外で release したら、その時点で値更新は確定して解除してよい**

## 真因候補（優先度順）

### 1) 手動 `dispatch_events()` ループの不安定化（主因・濃厚）

`Window.dispatch_events()` を毎フレーム回す実装は macOS（arm64）で不安定になり得る。
不安定化すると入力イベント（特に release）が欠落し、押下残留→クリック不発/解除不能が起きる。

### 2) clock/tick の二重駆動・再入（併発し得る）

`renderer.process_inputs()` が `pyglet.clock.tick()` を呼ぶ構造だと、アプリ側で別ループを回している場合に、
入力処理と clock の責務が二重化し、長時間運転や負荷で破綻しやすくなる可能性がある。

### 3) 「GUI 外で release される」ケースの未対応（仕様上の穴）

ドラッグ中にカーソルが GUI 外へ出て、別ウィンドウ上やアプリ外で release されると、
GUI 側が release を受け取れないことがある（ウィンドウ/OS の入力配送の都合）。
これを前提にしない実装だと、押下残留が起きる。

### 4) ImGui の IO 同期順序の不整合（低〜中）

`delta_time` / `display_size` / `mouse_pos` などの IO 更新が `new_frame()` より後になるなど、
フレーム開始順序が崩れると入力状態が破綻しやすい（特に dt が暴れる/0 になる等）。

## 対策候補（採用案の整理）

### A. `pyglet.app.run` に寄せる（第一候補）

- 手動 `dispatch_events()` をやめ、イベント処理は pyglet の app loop に任せる
- 複数ウィンドウは `on_draw`（= pyglet が描画→flip する流れ）に寄せる
- ウィンドウを閉じたら `pyglet.app.exit()` で確実に抜ける

狙い: macOS（arm64）でのイベント取りこぼしを避ける

### B. `renderer.process_inputs()` を使わない（または呼び方を見直す）

- `process_inputs()` が `pyglet.clock.tick()` を内包しているなら、app loop と二重駆動になるため避ける
- `delta_time` / `display_size` などの IO は `new_frame()` の前に同期する

狙い: event/clock の責務を 1 箇所に集約して破綻しにくくする

### C. 共有マウス状態で `io.mouse_down` を補正する（保険）

- `pyglet.window.mouse.MouseStateHandler` を全ウィンドウで共有し、「ボタン状態」だけを一元化する
- GUI 側は毎フレーム `io.mouse_down[0..2]` を共有状態で上書きする
  - 注意: 座標はウィンドウ座標が混ざるので触らない（ボタン状態のみ）

狙い: release が GUI 外で起きても押下残留しないようにする

### D. フォーカス喪失時の強制解除（仕様として決める）

- `on_deactivate`（フォーカス喪失）を「release と同等」とみなし、押下/ドラッグ状態を解除する
- 期待挙動（GUI 外 release で確定して解除）と整合する

狙い: OS の入力配送仕様に依存しない「確実な解除」を提供する

### E. 代替案（副作用が大きいので保留）

- ドラッグ中だけ `set_exclusive_mouse(True)` 等で「確実に release を取る」方向
  - カーソル挙動が変わる/UX が変質しやすい
- imgui/pyglet backend 側を fork/patche して根治
  - 依存コードの保守コストが増えるため最後の手段

## 推奨する実装順序（reset 後の進め方）

1. **A（`pyglet.app.run` へ移行）**: まず「後半から効かなくなる」根本を潰す
2. **B（`process_inputs()` を避ける）**: 二重駆動を排除して安定化
3. **C or D（保険）**: 「GUI 外 release」「フォーカス喪失」でも必ず解除される UX を保証する

## 最小 QA（確認観点）

- 通常操作: button / checkbox / slider が普通にクリック・ドラッグできる
- 再現操作: slider ドラッグ → 別ウィンドウ上で release → GUI が固まらない
- 再現操作: slider ドラッグ → アプリ外（デスクトップ/別アプリ）で release → GUI が固まらない
- 時間依存: しばらく操作しても（後半でも）クリック不発が再発しない
