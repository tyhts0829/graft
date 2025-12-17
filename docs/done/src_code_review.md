# どこで: `docs/memo/src_code_review.md`。
# 何を: `src` 配下モジュール群のコードレビュー結果を記録する。
# なぜ: 改善方針の合意と追跡をしやすくするため。

# src 配下モジュール群コードレビュー

## 全体所感

- `src/api`（G/E/L/run）→ `src/core`（Geometry/registry/realize）→ `src/parameters`（フレーム固定の解決＋GUI永続）→ `src/render`/`src/app`（pyglet+ModernGL+imgui）という層が素直で、読みやすい構成。

## 良い点

- `Geometry` を不変＋内容署名IDにしてキャッシュ可能にしているのが綺麗（`src/core/geometry.py:120`、`src/core/realize.py:1`）。
- パラメータは `parameter_context` でスナップショット固定→フレーム終端でマージ、という設計が決定的で扱いやすい（`src/parameters/context.py:45`、`src/parameters/resolver.py:128`）。
- `RealizedGeometry` が dtype/shape/不変条件を強制していて下流が楽（`src/core/realized_geometry.py:10`）。
- GUI 周りが「純粋関数（row/rules/grouping）↔ imgui描画 ↔ store反映」に分離されていて保守しやすい（`src/app/parameter_gui/store_bridge.py:1`）。
- テストがかなり揃っていて安心感がある（`tests/`）。

## 優先度高い指摘

- `src/*/from_previous_project` が現行構成と不整合で、存在しないモジュールを import しており“置いてあるだけで地雷”（例: `src/primitives/from_previous_project/text.py:22`、`src/effects/from_previous_project/weave.py:17`、`src/effects/from_previous_project/weave.py:20`）。
- `src/effects` の局所ルール（冒頭の「どこで/なにを/なぜ」docstring禁止）と実装が不一致。`fill` は守れている一方で、他が逆（`src/effects/AGENTS.md:2`、`src/effects/fill.py:1`、`src/effects/scale.py:1`、`src/effects/offset.py:1`、`src/effects/repeat.py:1`）。
- `parameter_context` は `store.store_frame_params(...)` が例外を投げると contextvar を `reset()` できず“漏れる”可能性がある（`src/parameters/context.py:60`）。
- `Geometry` の int 正規化が float 経由で、大きい int で丸めが起き得る（`src/core/geometry.py:42`、`src/core/geometry.py:48`）。

## 改善案（優先度順）

1. `src/*/from_previous_project` を `src` 外へ移動 or 削除（まずここが一番効く）
2. `src/effects` の冒頭 docstring を `src/effects/AGENTS.md:2` に合わせて統一（`fill` と同じ方針へ）
3. `parameter_context` は reset を必ず実行する形に小さく整理（`src/parameters/context.py:60`）
4. int 正規化の float 経由をやめる（`src/core/geometry.py:42`）
5. `_coerce_rgb255` の重複を1箇所へ（`src/app/runtime/draw_window_system.py:88`、`src/render/frame_pipeline.py:51`）

