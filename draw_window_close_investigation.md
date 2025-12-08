# draw_window_close_investigation.md
どこで: `src/api/run.py`, `src/render/draw_renderer.py`。
何を: ESC でウィンドウを閉じた際に発生する `InvalidObject` 例外の原因を特定し、設計改善方針をまとめる。
なぜ: プレビューを安全に終了できるループ設計を確立し、ModernGL コンテキスト破棄時の例外を解消するため。

## 0. 症状と再現
- [x] `python main.py` を起動し、描画ウィンドウで ESC を押して閉じると、`AttributeError: 'InvalidObject' object has no attribute 'fbo'` が標準出力に記録される。
- [x] スタックトレースは `pyglet.app.base._redraw_windows` → `window.dispatch_event('on_draw')` → `DrawRenderer.viewport` で `self.ctx.viewport` にアクセスする過程で発生している。

## 1. 原因分析
1. `on_close` ハンドラで `renderer.release()` → `window.close()` を即時に実行している。
2. しかし `pyglet.app.run()` が内部で呼ぶ `_redraw_windows` は、同じティック内でまだ `window` を保持したまま `on_draw` を dispatch する。
3. `renderer.release()` が ModernGL コンテキストを破棄した直後に `on_draw` が再入すると、`self.ctx` の裏側にある `mglo` が `InvalidObject` となり `viewport`/`clear`/`render` の各 setter が失敗する。
4. `tick` からも `on_draw` を直接呼び出しており、pyglet 標準の描画フローと二重に呼ばれているため、ウィンドウクローズ時の `on_draw` 呼び出しタイミングを制御できていない。

## 2. 設計上の課題
- ライフサイクル境界: GL リソース解放と `pyglet.app.run()` 停止の順序が逆で、イベントループに未解放のウィンドウ参照が残るままコンテキストを破棄している。
- ハンドラ再入ガード不足: `on_draw`/`viewport` 側に閉塞状態を検知する仕組みがなく、一度解放した `renderer` を触っても早期 return できない。
- ダブルドロー構造: `tick` が `on_draw` を直接呼び、さらに `window.flip()` まで担っているため、pyglet の `_redraw_windows` が行う描画と責務が競合している。

## 3. 設計改善計画（チェックリスト）
- [ ] **終了シーケンスの反転**: `on_close` では `closed = True` と `unschedule_tick` と `pyglet.app.exit()` のみに留め、`renderer.release()` と `window.close()` を `pyglet.app.run()` の直後にまとめて実行する。
- [ ] **描画ガード導入**: `DrawRenderer` に `_released` フラグを持たせ、`viewport`/`clear`/`render`/`release` が複数回呼ばれても安全に no-op できるようにする。`run` 側の `on_draw` でも `if closed: return` を最上段に追加する。
- [ ] **描画フローの一本化**: `tick` は `draw` データ更新と `window.invalidade()` （macOS では `flip` を任せて `pyglet.clock.schedule_interval` から `window.dispatch_events` + `on_draw` 呼び出しに統一）へ役割を限定し、`window.flip()` の呼び出しを `on_draw` 内へ集約する。
- [ ] **回帰確認**: 端末で ESC クローズを 5 回繰り返し、例外が出ずに即時終了することを目視確認。必要であれば `pytest -q -k draw_window_close` のような軽量統合テストを追加する。

## 4. 補足メモ
- ModernGL の `InvalidObject` はコンテキストが解放済み/別スレッドに移ったときの共通シグナルであり、今回のスタックでは `self.ctx.viewport = ...` が `Context.viewport` → `self.mglo.fbo` にアクセスする段階で失敗している。したがって ModernGL 側ではなくランナーのライフサイクル管理を見直す必要がある。
- `window.close()` を `on_close` 内で明示的に呼ぶと、pyglet が実際のクローズ処理に入る前に GL リソースが破棄される。pyglet 自身が `on_close` → `close` を呼び出すため、ここで再度 `close` する必要はない。
