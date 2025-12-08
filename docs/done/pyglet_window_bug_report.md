# pyglet_window_bug_report.md
どこで: `main.py` 実行時の pyglet/ModernGL プレビュー。
何を: 発生中の例外原因を整理し、抜本的な改善計画を提示する。
なぜ: 例外を握りつぶさず、安定した描画ループを構築するため。

## 0. 状況と再現
- [x] `python main.py` 実行で以下の例外が発生。
  - `AttributeError: 'CocoaWindow' object has no attribute 'invalidate'`
  - `AttributeError: 'InvalidObject' object has no attribute 'size'`（`LineMesh` 内の VBO アクセス時）

## 1. 原因分析（現時点の確度: 高）
- [x] `window.invalidate()` 呼び出し
  - pyglet 2.1 (Cocoa) では `Window.invalidate` API が存在せず、毎フレームのスケジュールで例外が連発している。
  - 例外は拾われず `ctypes` コールバックで無視されるため、描画ループの健全性が保たれない。
- [x] `InvalidObject` (moderngl Buffer)
  - moderngl の `InvalidObject` は **GL コンテキストが無効/別コンテキスト** であるときに発生する典型例。
  - 現実装では `Window` 作成直後に `moderngl.create_context()` を呼んでいるが、macOS/Cocoa ではウィンドウコンテキストが確実にカレントになるとは限らない。
  - その結果、moderngl がラップしたコンテキストと描画時にカレントなコンテキストが食い違い、VBO が無効化されていると推定。

## 2. 改善計画（実装前チェックリスト）
- [x] 描画スケジューラを修正
  - `window.invalidate` 依存を排除し、`pyglet.clock.schedule_interval(tick, 1/60)` で `tick` 内から `on_draw`+`flip` を呼ぶループに置き換え。
- [x] GL コンテキストの確定手順を明示
  - `Window` 生成直後に `window.switch_to()` でカレント化し、その状態で `moderngl.create_context(require=410)` を呼ぶ。
- [x] リサイズ対応
  - `on_resize` で `mgl_ctx.viewport` を更新。
- [x] ライフサイクルの健全化
  - `on_close` で `unschedule` → `mesh.release()` → `program.release()` → `mgl_ctx.release()` → `window.close()` の順に解放し、ガード用フラグで再描画を抑止。
- [x] 例外の扱い
  - `tick` 内で例外発生時に `pyglet.app.exit()` し、例外を再送出して即時終了させる方針へ更新。
- [ ] 動作確認
  - `python main.py` で 60fps 相当で継続描画し、例外が出ないことを目視で確認。

## 3. オープン事項
- [ ] macOS での GL4.1 Core コンフィグ要求値（`sample_buffers`, `samples`）をどこまで上げるか。
- [ ] 将来的にマルチレイヤー/キャッシュを導入した際の描画頻度制御（垂直同期と `schedule_interval` の整合）。
