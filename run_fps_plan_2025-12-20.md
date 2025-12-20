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
- `src/grafix/interactive/runtime/window_loop.py`（必要なら docstring を「公開 API と同じ意味」に揃える）
- `src/grafix/interactive/draw_window.py`（未使用の `schedule_tick`/`unschedule_tick` を削除）
- （影響確認）`README.md` / `docs/` 内の `run()` 呼び出し例

## Design（方針）

- `run()` は「値の受け渡し」に徹し、FPS の実装は `MultiWindowLoop` に寄せる
- `fps` の意味は現行 `MultiWindowLoop` と同じにする（`fps<=0` はスロットリング無効）
- `pyglet.options["vsync"] = True` は現状維持とし、実効 FPS は「目標 FPS / VSync / 負荷」の制約を受ける（上限保証はしない）

## Action items（チェックリスト）

- [ ] `src/grafix/api/run.py` に `fps: float = 60.0` を追加
  - [ ] docstring に `fps` の意味（`<=0` で sleep しない）を明記
  - [ ] `loop = MultiWindowLoop(tasks, fps=fps)` に変更
- [ ] `run()` の呼び出し箇所を検索し、必要なら追従修正
  - [ ] `rg -n "grafix\\.api\\.run\\(|\\brun\\("` で確認（同名関数が多いので絞る）
- [ ] `src/grafix/interactive/draw_window.py` の未使用関数を削除
  - [ ] `schedule_tick` / `unschedule_tick` の削除（`create_draw_window` は維持）
- [ ] ドキュメント更新（存在する場合のみ、最小）
  - [ ] README / docs の `run()` 使用例に `fps` を追記（任意）
- [ ] 静的チェック
  - [ ] `ruff check .`
  - [ ] `mypy src/grafix`

## Risks / Edge cases（注意点）

- VSync 有効時、`fps` を上げてもモニタのリフレッシュレートを超えない（「指定したのに 60fps 以上出ない」混乱の可能性）
- `fps<=0` を公開 API に出すことによる誤用（ただし実装は簡単で一貫性は高い）

## Open questions（要確認）

- `run(fps=...)` は `float` 固定で良い？（`None` 等は入れず、単純に `fps<=0` を無効扱いにする方針で進めて良い？）
- `vsync` も `run()` の引数に含める？（今回のスコープは FPS のみで良い？）
- `schedule_tick`/`unschedule_tick` は完全削除で良い？（将来使う可能性があっても「必要になった時に実装し直す」方針でOK？）

