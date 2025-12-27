# ParameterGUI: クリック不発 / スライダー「ドラッグ解除不能」修正計画（2025-12-27）

## 背景（現象）
- `parameter_gui` でクリックが効かなくなることがある
- スライダーをドラッグしたあと、ドラッグが解除されず UI 全体が「押しっぱなし」っぽくなることがある

## 原因（現時点の結論）
- pyimgui の pyglet backend が `io.mouse_down[...]` を `on_mouse_press` / `on_mouse_release` によって更新している
  - しかし「release が ParameterGUI ウィンドウに届かない」ケースがあり、`io.mouse_down[0]=1` が残留する
  - 残留すると ImGui 的には「常に左ボタン押下中」になり、クリック（押して離す）が成立しない + スライダーが解除されない
- `on_mouse_leave` はドラッグ中には発火しない（pyglet 2.1.11 の仕様）ため、「leave で解除」は成立しない

## 目標（完了条件）
- ドラッグ中に別ウィンドウ/アプリ/デスクトップ側でボタンを離しても、`parameter_gui` が「押しっぱなし」状態に陥らない
- クリックが効かなくなる状態が再現しない
- 既存の操作感（通常のドラッグ/クリック）を壊さない

## 方針（採用案）
### 方針A: 共有 `MouseStateHandler` で「ボタン状態だけ」を毎フレーム補正する（第一候補）
pyglet には `pyglet.window.mouse.MouseStateHandler` があり、イベントからボタン状態を追跡できる。
同一インスタンスを「全ウィンドウ」に `push_handlers` すると、release が別ウィンドウで発生しても同じ状態が更新される。

ParameterGUI 側では ImGui の `io.mouse_down[0..2]` を「毎フレームこの共有状態で上書き」し、取りこぼしを補正する。

**狙い**
- release が draw window に届くケース → 共有ハンドラが False 化 → ParameterGUI の ImGui 側もそのフレームで解除
- release がアプリ外（他アプリ/デスクトップ）で起きるケース → `on_deactivate` で state が clear → ParameterGUI も解除

**注意**
- 位置（`io.mouse_pos`）はウィンドウ座標が混ざると壊れるので、補正対象は「ボタン状態のみ」に限定する

## 実装タスク（チェックリスト）
### 0. 再現手順の固定
- [ ] 現在の再現手順を docs に明記（例: スライダーをドラッグ → 描画ウィンドウ上でボタンを離す → 以後クリック不発）
- [ ] 「期待挙動」を1行で決める（例: 離した時点で必ず解除される）

### 1. 共有マウス状態の導入（作成と配線）
- [ ] `src/grafix/api/run.py` で `mouse_state = pyglet.window.mouse.MouseStateHandler()` を生成する
- [ ] `mouse_state` を `run()` が生成する全 window（少なくとも draw window と parameter gui window）へ `push_handlers(mouse_state)` する
  - [ ] どちらのウィンドウ生成が先でも同じインスタンスを使う（状態を共有するため）

### 2. ParameterGUI 側で ImGui のボタン状態を補正
- [ ] `ParameterGUI`（`src/grafix/interactive/parameter_gui/gui.py`）に `mouse_state` を受け取れるようにする（引数追加 or 受け渡し経路追加）
- [ ] `draw_frame()` の `self._renderer.process_inputs()` の後に、`self._renderer.io.mouse_down[0..2]` を `mouse_state` で上書きする
  - [ ] LEFT/MIDDLE/RIGHT の対応を明示（0/1/2）
  - [ ] 位置やホイールは触らない

### 3. 手動QA（最小）
- [ ] 通常のクリック/ドラッグが壊れていないこと（slider, checkbox, button）
- [ ] slider をドラッグ → 描画ウィンドウ上で release → `parameter_gui` が固まらない
- [ ] slider をドラッグ → デスクトップ/別アプリで release（フォーカス喪失）→ `parameter_gui` が固まらない
- [ ] 「固まった後にどこをクリックしても効かない」状態が再現しない

### 4. 仕上げ（必要なら）
- [ ] 追加で `on_deactivate` による強制クリア（`renderer.io.mouse_down[:] = 0`）を入れるか判断
- [ ] デバッグ表示（`io.mouse_down` と `mouse_state` の差）を一時的に入れて、確認後に削除するか判断

## 代替案（採用しない/保留）
- 方針B: `set_exclusive_mouse(True)` をドラッグ中だけ有効化して release を確実に取る
  - カーソル挙動が変わりやすく、UI 用途として副作用が大きいので保留
- 方針C: imgui の pyglet backend を fork/パッチして「release 取りこぼし」を独自に解決
  - 依存側コードの管理が増えるので、まずは方針Aで最小修正を狙う

## 要確認（あなたに先に聞きたいこと）
- [ ] 「ドラッグ中に GUI 外で release したら、値更新はそこで確定して解除」でOK？（今の感覚に合わせたい）
- [ ] 共有 `MouseStateHandler` を導入してもOK？（実装はシンプルだが run.py / ParameterGUI の配線は触る）

