# `grafix.api.run()` の FPS 引数化 実装計画

目的: FPS 設定が散在して見える状態を解消し、`grafix.api.run()` の引数で目標 FPS を指定できるようにする。あわせて未使用の FPS 関連実装（`schedule_tick` 等）を削除して迷い所を減らす。

## Requirements（要件）

- `grafix.api.run(draw, ...)` で目標 FPS を指定できる
- 引数省略時の挙動は現状維持（既定 60fps）
- FPS の責務は `MultiWindowLoop` に集約し、他モジュールでの独自 FPS 制御を増やさない
- 未使用実装を残さない（現状未使用の `schedule_tick`/`unschedule_tick` は削除）

## Scope（範囲）

- In:
  - `run()` の引数追加（公開 API）
  - `MultiWindowLoop` への FPS 値の受け渡し
  - 未使用の FPS 関連関数の削除
- Out:
  - GUI から FPS を動的変更する機能
  - 録画 FPS / タイムスタンプ同期
  - ヘッドレス export 側の FPS 概念導入

## Files / Entry points（触る場所）

- `src/grafix/api/run.py`（`run(..., fps=...)` を追加し `MultiWindowLoop(..., fps=fps)` に配線）
- `src/grafix/api/__init__.pyi`（公開 API の型スタブ。`run()` シグネチャ追従が必要）
- `src/grafix/interactive/runtime/window_loop.py`（必要なら docstring を「公開 API と同じ意味」に揃える）
- `src/grafix/interactive/draw_window.py`（未使用の `schedule_tick`/`unschedule_tick` を削除）
- （影響確認）`README.md` / `docs/` 内の `run()` 呼び出し例

## Design（方針）

- `run()` は「値の受け渡し」に徹し、FPS の実装は `MultiWindowLoop` に寄せる
- `fps` の意味は現行 `MultiWindowLoop` と同じにする（`fps<=0` はスロットリング無効）
- `pyglet.options["vsync"] = True` は現状維持とし、実効 FPS は「目標 FPS / VSync / 負荷」の制約を受ける（上限保証はしない）

## Action items（チェックリスト）

- [x] `src/grafix/api/run.py` に `fps: float = 60.0` を追加
  - [x] docstring に `fps` の意味（`<=0` で sleep しない）を明記
  - [x] `loop = MultiWindowLoop(tasks, fps=fps)` に変更
- [x] `src/grafix/api/__init__.pyi` の `run()` シグネチャを追従（公開 API の型スタブ）
- [x] `src/grafix/interactive/runtime/window_loop.py` の `fps` 説明を `<=0` まで明記
- [x] `run()` の呼び出し箇所を検索し、追従修正が不要なことを確認
- [x] `src/grafix/interactive/draw_window.py` の未使用関数を削除
  - [x] `schedule_tick` / `unschedule_tick` の削除（`create_draw_window` は維持）
- [x] ドキュメント更新（最小）
  - [x] `README.md` の `run()` 使用例に `fps` を追記
- [ ] 静的チェック
  - [ ] `ruff check ...`（この環境では `ruff` コマンドが見つからず未実行）
  - [ ] `mypy ...`（この環境では既存エラーが多数あり、差分由来の判定が難しい）
  - [x] `python -m compileall ...`（構文チェック）

## Risks / Edge cases（注意点）

- VSync 有効時、`fps` を上げてもモニタのリフレッシュレートを超えない（「指定したのに 60fps 以上出ない」混乱の可能性）
- `fps<=0` を公開 API に出すことによる誤用（ただし実装は簡単で一貫性は高い）

## Open questions（要確認）

- `run(fps=...)` は `float` 固定で良い？（`None` 等は入れず、単純に `fps<=0` を無効扱いにする方針で進めて良い？）;はい
- `vsync` も `run()` の引数に含める？（今回のスコープは FPS のみで良い？）: No
- `schedule_tick`/`unschedule_tick` は完全削除で良い？（将来使う可能性があっても「必要になった時に実装し直す」方針で OK？）: はい
