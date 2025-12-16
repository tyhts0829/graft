# どこで: `docs/memo/src_core_export_interactive_code_review_2025-12-16.md`。
# 何を: `src/` の core/export/interactive（+ api）分割後の構造レビュー結果を記録する。
# なぜ: 依存境界の妥当性と、次に手を入れるべき箇所の優先度を明確にするため。

# src 構造レビュー（2025-12-16）

## 対象

- 新レイヤ構造: `src/core/`, `src/export/`, `src/interactive/`, `src/api/`
- 依存方向の意図: `core` → `export` / `interactive`、`api` はファサード

## 良い点

- 依存方向が素直: `src/core/`（モデル/正規化/realize）→ `src/export/`（ヘッドレス）/ `src/interactive/`（GUI+GL）で、共有点は `src/core/pipeline.py:28` / `src/core/pipeline.py:37` に集約できている。
- GL 依存を本当に押し込められている: インデックス生成が `src/interactive/gl/index_buffer.py:12` に閉じていて、`src/interactive/runtime/draw_window_system.py:26` / `src/interactive/runtime/draw_window_system.py:66` が core パイプラインを「使うだけ」になっているのは美しい。
- API の面も筋が良い: GUI 依存を遅延する `run` ラッパが `src/api/__init__.py:17`、実体が `src/api/run.py:29` に分かれていて import 体験が安定している。
- 境界テストが効いてる: `tests/test_dependency_boundaries.py:36` / `tests/test_dependency_boundaries.py:54` は “壊れたらすぐ気づける” 最小の柵としてちょうど良い。

## 気になる点（直す価値が高い順）

- `tests/test_dependency_boundaries.py:36` が相対 import をスキップしているため、理屈上は境界を「相対 import で迂回」できる（運用で禁止するか、テスト側で拾うかは方針決めが必要）。
- `normalize_scene` が裸 `Geometry` を `Layer(site_id=f"implicit:{item.id}")` にする (`src/core/scene.py:18`) ので、スタイル/GUI 行を安定させたいなら「基本は `L(...)` 経由」に寄せたほうが破綻しにくい（今の挙動自体は一貫している）。
- `src/api/effects.py:14`（`src/api/primitives.py` も同様）が “import しただけで全登録” 方式なので、将来 effect/primitive が増えると `from src.api import E` のコストが効いてくる（現状規模なら問題なし、重くなってから最適化で十分）。
- Export は `Export(...)` が即実行で、現状は必ず `NotImplementedError` になる (`src/api/export.py:20`) ため、interactive ショートカット配線を入れる場合は「例外で落とす / noop で通知する」等の方針を先に決めると迷いにくい。

